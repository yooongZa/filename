"""Small shared utilities; no model imports or network calls at import time."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    # Fail visibly for partial/corrupt lines; never silently discard saved attempts.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_row(path, row):
    # Only the main thread writes this file.
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def environment():
    packages = {}
    for name in ["Pillow", "numpy", "jsonschema", "torch", "torchvision", "transformers", "psutil"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version, "platform": platform.platform(), "packages": packages,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def load_config(path, profile_path=None):
    path = Path(path).resolve()
    cfg = read_json(path)
    for key in ["dataset_root", "run_dir"]:
        p = Path(cfg[key]).expanduser()
        cfg[key] = str((path.parent / p).resolve() if not p.is_absolute() else p)
    for key in ["prompt_path", "schema_path"]:
        cfg[key] = str(ROOT / cfg[key])
    selected = profile_path or cfg.get("profile_path")
    if selected:
        selected = Path(selected).expanduser()
        if not selected.is_absolute():
            selected = path.parent / selected
        apply_profile(cfg, selected)
    return cfg


def apply_profile(cfg, path):
    """A·B·앱이 같은 입력 조건을 읽습니다. profile 경로는 파일 자신을 기준으로 합니다."""
    path = Path(path).expanduser().resolve()
    profile = read_json(path)
    allowed = {"baseline", "video_extraction", "prompt_path", "schema_path"}
    if set(profile) - allowed:
        raise ValueError(f"알 수 없는 profile 항목: {set(profile) - allowed}")
    for key in ("baseline", "video_extraction"):
        cfg.setdefault(key, {}).update(profile.get(key, {}))
    for key in ("prompt_path", "schema_path"):
        if key in profile:
            value = Path(profile[key]).expanduser()
            cfg[key] = str((path.parent / value).resolve() if not value.is_absolute() else value)
    cfg["profile_path"] = str(path)
    return cfg


def validate_output(raw: str, schema: dict):
    from jsonschema import Draft202012Validator

    def pairs(items):
        obj = {}
        for key, val in items:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = val
        return obj

    def reject_constant(value):
        raise ValueError(f"non-JSON constant: {value}")

    try:
        obj = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, TypeError) as e:
        return {"json_valid": False, "schema_valid": False, "parsed": None, "format_errors": [str(e)]}
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(obj)]
    return {"json_valid": True, "schema_valid": not errors, "parsed": obj, "format_errors": errors}


class BenchError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RunLock:
    """A run directory has one writer. Do not remove an active process's lock."""
    def __init__(self, directory):
        self.path = Path(directory) / ".running.lock"

    def __enter__(self):
        try:
            with self.path.open("x", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except FileExistsError as e:
            raise RuntimeError(f"Run is locked: {self.path}. Check the recorded PID before removing a stale lock.") from e
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)
