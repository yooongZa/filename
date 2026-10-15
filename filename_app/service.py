"""화면과 모델을 연결합니다. 한 번에 한 작업만 실행하고 파일별 결과를 저장합니다."""

from __future__ import annotations
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

from filename_bench.backends import LlamaBackend, MockBackend
from filename_bench.common import (
    apply_profile,
    file_hash,
    read_json,
    validate_output,
    write_json,
)
from filename_bench.data import IMAGE_SUFFIXES
from filename_bench.video import VIDEO_SUFFIXES
from .files import FolderLock, History, make_name, tagged_name
from .media import media_input
from .runtime import setup


class AppBackend(LlamaBackend):
    """앱에서는 5개 태그 Schema를 서버에도 전달합니다. 벤치마크 기본 동작은 유지합니다."""

    def __init__(self, cfg, schema):
        super().__init__(cfg)
        self.schema = schema

    def request(self, route, body=None, timeout=10):
        if route == "/v1/chat/completions" and body is not None:
            body = {
                **body,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "filename_tags",
                        "strict": True,
                        "schema": self.schema,
                    },
                },
            }
        return super().request(route, body, timeout)


class DemoBackend(MockBackend):
    def infer(self, *args):
        result = super().infer(*args)
        result["raw_output"] = json.dumps(
            {
                "keywords": ["모의실행", "대상", "행동", "배경", "검토"],
                "reason": "화면과 파일명 검토 흐름을 확인하는 모의 응답입니다. 실제 장면 분석이 아닙니다.",
                "place_name": None,
            },
            ensure_ascii=False,
        )
        return result


def load_app_config(path):
    path = Path(path).resolve()
    cfg = read_json(path)
    profile = Path(cfg["profile_path"]).expanduser()
    apply_profile(cfg, profile if profile.is_absolute() else path.parent / profile)
    settings = cfg["baseline"]
    for key in [
        "short_edge_px",
        "vision_token_budget",
        "max_new_tokens",
        "context_per_request",
    ]:
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(f"{key}은 양의 정수여야 합니다.")
    if settings.get("concurrency") != 1:
        raise ValueError(
            "앱은 순차 처리합니다. profile의 concurrency를 1로 설정하세요."
        )
    if settings["frames"] != "all" and (
        type(settings["frames"]) is not int or settings["frames"] < 1
    ):
        raise ValueError("frames는 양의 정수 또는 all이어야 합니다.")
    if settings["max_new_tokens"] >= settings["context_per_request"]:
        raise ValueError("최대 출력 토큰은 문맥 길이보다 작아야 합니다.")
    if settings["selection"] not in ["uniform", "blur", "dedup", "both"]:
        raise ValueError("selection 설정을 확인하세요.")
    if (
        not isinstance(settings["temperature"], (int, float))
        or not 0 <= settings["temperature"] <= 2
    ):
        raise ValueError("temperature는 0~2여야 합니다.")
    # 파일을 매 분석 시작 때 읽으므로 수정한 프롬프트가 다음 분석에 반영됩니다.
    cfg["prompt"] = Path(cfg["prompt_path"]).read_text(encoding="utf-8")
    cfg["schema"] = read_json(cfg["schema_path"])
    return cfg


class AppService:
    def __init__(self, config_path, cache, mock=False):
        self.config_path = Path(config_path)
        self.cache = Path(cache)
        self.mock = mock
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.folder = None
        self.items = []
        self.previews = {}
        self.state = {
            "busy": False,
            "message": "정리할 폴더를 선택하세요.",
            "error": "",
            "processed": 0,
            "total": 0,
            "skipped": 0,
            "mock": mock,
            "config_path": str(self.config_path),
            "model": "Gemma 4 12B QAT",
            "operation": "idle",
            "analysis_id": "",
            "review_before_rename": read_json(self.config_path).get(
                "review_before_rename", True
            ),
            "auto_rename": read_json(self.config_path).get("auto_rename", False),
        }

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(
                {
                    **self.state,
                    "folder": str(self.folder) if self.folder else "",
                    "items": self.items,
                }
            )

    def progress(self, message, **values):
        with self.lock:
            self.state.update(message=message, **values)

    def launch(self, operation, function, *args):
        with self.lock:
            if self.state["busy"]:
                raise ValueError("현재 작업이 끝난 뒤 다시 실행하세요.")
            self.cancel_event.clear()
            self.state.update(busy=True, error="", operation=operation)

        def worker():
            try:
                function(*args)
            except InterruptedError as exc:
                self.progress(str(exc))
            except Exception as exc:
                self.progress("작업을 완료하지 못했습니다.", error=str(exc))
            finally:
                with self.lock:
                    self.state["busy"] = False

        self.thread = threading.Thread(
            target=worker, name="filename-worker", daemon=False
        )
        self.thread.start()

    def analyze(self, folder, options=None):
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("존재하는 폴더를 선택하세요.")
        options = options or {}
        if set(options) - {"review_before_rename", "auto_rename"} or any(
            type(v) is not bool for v in options.values()
        ):
            raise ValueError("검토·자동 변경 옵션은 true 또는 false만 사용하세요.")
        self.launch("analyze", self._analyze, root, dict(options))

    def _analyze(self, root, options):
        cfg = load_app_config(self.config_path)
        cfg.update(options)
        self.progress("폴더를 확인합니다.", **options)
        # 화면에서 고른 옵션을 다음 실행에도 유지합니다.
        saved = read_json(self.config_path)
        saved.update(options)
        write_json(self.config_path, saved)
        with self.lock:
            self.folder = root
            self.state["analysis_id"] = uuid.uuid4().hex
            self.items = []
            self.previews = {}
        with FolderLock(root):
            history = History(root)
            try:
                done = history.done_paths()
                candidates = (
                    root.rglob("*") if cfg.get("recursive", False) else root.iterdir()
                )
                paths = []
                skipped = 0
                for p in sorted(candidates):
                    if self.cancel_event.is_set():
                        raise InterruptedError("분석을 취소했습니다.")
                    if (
                        p.is_symlink()
                        or not p.is_file()
                        or any(x.startswith(".") for x in p.relative_to(root).parts)
                    ):
                        continue
                    if p.suffix.lower() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
                        continue
                    relative = p.relative_to(root).as_posix()
                    if relative in done or (
                        cfg.get("skip_tagged_names", True) and tagged_name(p.name)
                    ):
                        skipped += 1
                        continue
                    paths.append(p)
                self.progress(
                    "분석할 파일을 확인했습니다.",
                    total=len(paths),
                    processed=0,
                    skipped=skipped,
                )
                if not paths:
                    self.progress("새로 분석할 사진·영상이 없습니다.")
                    return
                work = root / ".filename"
                run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
                run_dir = work / "analyses" / run_id
                run_dir.mkdir(parents=True)
                write_json(
                    run_dir / "config.json",
                    {k: v for k, v in cfg.items() if k not in ["prompt", "schema"]},
                )
                (run_dir / "prompt.txt").write_text(cfg["prompt"], encoding="utf-8")
                write_json(run_dir / "schema.json", cfg["schema"])
                if self.mock:
                    backend = DemoBackend({})
                else:
                    runtime = setup(
                        cfg["runtime"], self.cache, self.progress, self.cancel_event
                    )
                    backend = AppBackend(runtime, cfg["schema"])
                if self.cancel_event.is_set():
                    raise InterruptedError("분석을 취소했습니다.")
                self.progress("모델을 준비합니다. 처음에는 시간이 걸릴 수 있습니다.")
                try:
                    backend.start(cfg["baseline"], run_dir / "server.log")
                    write_json(run_dir / "backend.json", backend.info)
                    for index, path in enumerate(paths):
                        if self.cancel_event.is_set():
                            break
                        relative = path.relative_to(root).as_posix()
                        row = {
                            "id": str(index),
                            "original": relative,
                            "name": path.name,
                            "tags": [],
                            "proposed": "",
                            "reason": "",
                            "status": "error",
                            "error": "",
                            "sha256": "",
                            "elapsed_s": None,
                        }
                        self.progress(f"분석 중 · {path.name}", processed=index)
                        started = time.perf_counter()
                        try:
                            before = path.stat()
                            sha = file_hash(path)
                            row["sha256"] = sha
                            images, meta, preview = media_input(
                                path, sha, cfg, work / "frames", self.cancel_event
                            )
                            with self.lock:
                                self.previews[row["id"]] = preview
                            response = backend.infer(
                                images,
                                cfg["prompt"],
                                cfg["baseline"],
                                cfg.get("seed", 42),
                            )
                            after = path.stat()
                            if (before.st_size, before.st_mtime_ns) != (
                                after.st_size,
                                after.st_mtime_ns,
                            ):
                                raise ValueError(
                                    "분석 중 파일이 변경되었습니다. 다시 분석하세요."
                                )
                            row.update(
                                raw_output=response["raw_output"],
                                input_meta=meta,
                                finish_reason=response.get("finish_reason"),
                                elapsed_s=round(time.perf_counter() - started, 2),
                            )
                            checked = validate_output(
                                response["raw_output"], cfg["schema"]
                            )
                            parsed = checked["parsed"]
                            if isinstance(parsed, dict):
                                tags = parsed.get("keywords", [])
                                row["tags"] = (
                                    tags
                                    if isinstance(tags, list)
                                    and all(isinstance(t, str) for t in tags)
                                    else []
                                )
                                row["reason"] = str(parsed.get("reason", ""))
                            row["status"] = "review"
                            if not checked["schema_valid"]:
                                row["error"] = (
                                    "출력 형식 또는 태그 개수를 확인하세요. "
                                    + "; ".join(checked["format_errors"])[:300]
                                )
                            elif response.get("finish_reason") in [
                                "length",
                                "timeout",
                            ] or response.get("input_truncated_reported"):
                                row["error"] = (
                                    "모델 응답이 잘렸습니다. 수동 검토가 필요합니다."
                                )
                            else:
                                row["proposed"] = make_name(path.name, row["tags"])
                                row["status"] = "ready"
                        except InterruptedError:
                            break
                        except Exception as exc:
                            row["error"] = str(exc)
                        with self.lock:
                            self.items.append(row)
                        write_json(run_dir / f"{index:05d}.json", row)
                        self.progress(
                            f"{index+1} / {len(paths)}개 분석 완료", processed=index + 1
                        )
                    self.progress(
                        "분석을 중단했습니다. 완료된 결과를 검토할 수 있습니다."
                        if self.cancel_event.is_set()
                        else "추천 태그 5개를 검토하고 변경할 파일을 선택하세요."
                    )
                    if (
                        cfg.get("auto_rename", False)
                        and not cfg.get("review_before_rename", True)
                        and not self.cancel_event.is_set()
                        and not self.mock
                    ):
                        self.progress(
                            "검증을 통과한 파일의 이름을 자동으로 변경합니다."
                        )
                        edits = [
                            {"id": r["id"], "tags": r["tags"]}
                            for r in self.items
                            if r["status"] == "ready"
                        ]
                        if edits:
                            self._apply_edits(history, edits)
                finally:
                    backend.close()
            finally:
                history.close()

    def rename(self, edits):
        if self.mock:
            raise ValueError("모의 실행에서는 실제 파일명을 변경하지 않습니다.")
        if not isinstance(edits, list) or not edits:
            raise ValueError("변경할 파일을 선택하세요.")
        self.launch("rename", self._rename, copy.deepcopy(edits))

    def _rename(self, edits):
        root = self.folder
        if root is None:
            raise ValueError("먼저 분석을 실행하세요.")
        with FolderLock(root):
            history = History(root)
            try:
                self._apply_edits(history, edits)
            finally:
                history.close()

    def _apply_edits(self, history, edits):
        known = {r["id"]: r for r in self.items}
        seen = set()
        plans = []
        for edit in edits:
            row = known.get(edit.get("id"))
            if not row or row["id"] in seen or row["status"] not in ["ready", "review"]:
                raise ValueError("검토 가능한 파일을 한 번씩 선택하세요.")
            seen.add(row["id"])
            name = make_name(Path(row["original"]).name, edit.get("tags", []))
            target = (Path(row["original"]).parent / name).as_posix()
            plans.append((row, target, edit["tags"]))
        count = 0
        for row, target, tags in plans:
            if self.cancel_event.is_set():
                break
            try:
                history.rename(row["original"], target, row["sha256"])
                with self.lock:
                    row.update(
                        status="done", proposed=Path(target).name, tags=tags, error=""
                    )
                count += 1
            except (OSError, ValueError) as exc:
                with self.lock:
                    row["error"] = str(exc)
        self.progress(
            f"{count}개 파일 이름을 변경했습니다. 오류가 있는 항목은 그대로 보존했습니다."
        )

    def undo(self):
        if not self.folder:
            raise ValueError("먼저 폴더를 분석하세요.")
        self.launch("undo", self._undo)

    def _undo(self):
        with FolderLock(self.folder):
            history = History(self.folder)
            try:
                results = history.undo()
            finally:
                history.close()
        count = sum(r["ok"] for r in results)
        with self.lock:
            self.items = []
            self.previews = {}
        self.progress(
            f"{count}개 이름을 복원했습니다. 다시 분석하면 목록을 새로 읽습니다.",
            error="; ".join(r["error"] for r in results if not r["ok"]),
        )

    def cancel(self):
        self.cancel_event.set()
        self.progress("현재 처리 중인 요청이 끝나면 중단합니다.")
