"""설치된 모델을 찾고, 없는 실행 엔진·모델만 내려받습니다.

다운로드 파일은 .part로 저장한 뒤 SHA-256을 확인합니다. 사진·영상은 이 모듈에 전달하지 않습니다.
"""

from __future__ import annotations
import json
import os
import platform
import shutil
import ssl
import certifi
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from filename_bench.common import file_hash
from .files import FolderLock

RELEASE = "b11642"
ASSETS = {
    "Darwin-arm64": (
        "llama-b11642-bin-macos-arm64.tar.gz",
        "9b08d85971cafd4fc41f3a07727e7a7ebf05c25c423ad556be300bcb7cf0df1d",
    ),
    "Darwin-x86_64": (
        "llama-b11642-bin-macos-x64.tar.gz",
        "6c0e34d1bc87261454459670223a0b3b6cf18289421b953ecad6e0a12eb8ff32",
    ),
    "Windows-AMD64": (
        "llama-b11642-bin-win-vulkan-x64.zip",
        "0ee21ad25e560a63d5884e99795c47a7e7ef17fec3c7499298c289057bd03880",
    ),
}
MODEL_REPO = "google/gemma-4-12B-it-qat-q4_0-gguf"
MODEL_REVISION = "29d097773436b69ff9feafd636ab4cf873786537"
MODEL_FILES = {
    "model_path": (
        "gemma-4-12b-it-qat-q4_0.gguf",
        "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b",
    ),
    "mmproj_path": (
        "mmproj-gemma-4-12b-it-qat-q4_0.gguf",
        "cb018338a7538a9814d994bfe54644c71eb7ed54e31eae2f721e45fd3c260da7",
    ),
}


def app_home():
    if os.environ.get("FILENAME_HOME"):
        return Path(os.environ["FILENAME_HOME"]).expanduser().resolve()
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "filename"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/filename"
    return Path.home() / ".local/share/filename"


def cancelled(event):
    if event.is_set():
        raise InterruptedError(
            "취소했습니다. 다운로드한 부분은 다음 실행에서 이어받습니다."
        )


def download(url, target, sha256, progress, event):
    """네트워크 중단 시 완성 파일은 그대로, 부분 파일은 이어받기용으로 남깁니다."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        progress(f"{target.name} 확인 중")
        if file_hash(target) == sha256:
            return target
        raise ValueError(f"저장된 다운로드 파일의 해시가 다릅니다: {target}")
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    # 전송은 끝났고 이름 변경 전에 중단된 경우에도 이어받기를 복구합니다.
    if offset and file_hash(part) == sha256:
        part.replace(target)
        return target
    cancelled(event)
    headers = {"User-Agent": "filename-local-app/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(
        request, timeout=30, context=ssl.create_default_context(cafile=certifi.where())
    ) as response:
        append = offset > 0 and response.status == 206
        if append and not response.headers.get("Content-Range", "").startswith(
            f"bytes {offset}-"
        ):
            raise ValueError("서버의 이어받기 위치가 다릅니다.")
        done = offset if append else 0
        total = int(response.headers.get("Content-Length", 0)) + done
        if (
            total
            and shutil.disk_usage(target.parent).free < total - done + 256 * 1024**2
        ):
            raise OSError("다운로드를 완료할 디스크 공간이 부족합니다.")
        with part.open("ab" if append else "wb") as out:
            while True:
                cancelled(event)
                block = response.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
                done += len(block)
                progress(
                    f"{target.name} 다운로드 {done/1024**2:,.0f} / {total/1024**2:,.0f} MB"
                )
            out.flush()
            os.fsync(out.fileno())
    progress(f"{target.name} 다운로드 검증 중")
    if file_hash(part) != sha256:
        # 잘못된 부분 파일은 증거로 남기되 다음 실행에서 새로 받을 수 있게 합니다.
        part.rename(part.with_name(part.name + ".invalid-" + os.urandom(4).hex()))
        raise ValueError(
            "다운로드 무결성 검사에 실패했습니다. 다시 분석을 누르면 재시도합니다."
        )
    part.replace(target)
    return target


def find_model(options, cache):
    model, projector = options.get("model_path"), options.get("mmproj_path")
    if model or projector:
        if not model or not projector:
            raise ValueError("model_path와 mmproj_path를 함께 지정하세요.")
        pair = (Path(model).expanduser(), Path(projector).expanduser())
        if not all(p.is_file() for p in pair):
            raise FileNotFoundError("지정한 모델 또는 mmproj 파일을 찾을 수 없습니다.")
        return pair
    roots = [Path.home() / ".lmstudio/models", cache / "models"]
    roots.extend(Path(p).expanduser() for p in options.get("model_search_dirs", []))
    names = {"gemma-4-12b-it-qat-q4_0.gguf"}
    projector_names = [
        "mmproj-gemma-4-12b-it-qat-q4_0.gguf",
        "mmproj-gemma-4-12b-it-qat-bf16.gguf",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("*.gguf", "*/*.gguf", "*/*/*.gguf"):
            for path in sorted(root.glob(pattern)):
                if path.name.casefold() in names:
                    siblings = {
                        p.name.casefold(): p for p in path.parent.glob("*.gguf")
                    }
                    for name in projector_names:
                        if name in siblings:
                            return path, siblings[name]
    return None


def setup(options, cache, progress, event):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with FolderLock(cache):
        pair = find_model(options, cache)
        if pair:
            progress("설치된 Gemma 4 12B QAT 모델을 연결합니다.")
        else:
            if not options.get("auto_download", True):
                raise FileNotFoundError(
                    "모델 경로를 지정하거나 auto_download를 켜세요."
                )
            pair = []
            for filename, sha in MODEL_FILES.values():
                pair.append(
                    download(
                        f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{filename}",
                        cache / "models" / filename,
                        sha,
                        progress,
                        event,
                    )
                )
        for path in pair:
            with Path(path).open("rb") as stream:
                if stream.read(4) != b"GGUF":
                    raise ValueError(f"GGUF 파일이 아닙니다: {path}")
        server = options.get("server_path") or shutil.which("llama-server")
        if server and not Path(server).expanduser().is_file():
            raise FileNotFoundError(f"llama-server 경로를 확인하세요: {server}")
        if not server:
            key = f"{platform.system()}-{platform.machine()}"
            if key not in ASSETS:
                raise RuntimeError(
                    "이 OS/CPU는 server_path에 직접 llama-server 경로를 지정하세요."
                )
            name, sha = ASSETS[key]
            directory = cache / "runtime" / (RELEASE + "-" + key)
            binary = "llama-server.exe" if sys.platform == "win32" else "llama-server"
            binaries = list(directory.rglob(binary)) if directory.exists() else []
            if not binaries:
                if not options.get("auto_download", True):
                    raise FileNotFoundError(
                        "실행 엔진이 없습니다. server_path 또는 auto_download를 설정하세요."
                    )
                archive = download(
                    f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/{name}",
                    cache / "downloads" / name,
                    sha,
                    progress,
                    event,
                )
                directory.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
                    staging = Path(temporary) / "extracted"
                    staging.mkdir()
                    if name.endswith(".zip"):
                        with zipfile.ZipFile(archive) as bundle:
                            for member in bundle.infolist():
                                if (
                                    not (staging / member.filename)
                                    .resolve()
                                    .is_relative_to(staging)
                                ):
                                    raise ValueError(
                                        "압축 파일에 외부 경로가 있습니다."
                                    )
                            bundle.extractall(staging)
                    else:
                        with tarfile.open(archive) as bundle:
                            bundle.extractall(staging, filter="data")
                    binaries = list(staging.rglob(binary))
                    if len(binaries) != 1:
                        raise ValueError(
                            "실행 엔진 압축 파일에서 서버를 찾지 못했습니다."
                        )
                    relative = binaries[0].relative_to(staging)
                    staging.rename(directory)
                    binaries = [directory / relative]
            server = binaries[0]
        return {
            "kind": "llama-server",
            "server_path": str(Path(server).expanduser().resolve()),
            "model_path": str(Path(pair[0]).resolve()),
            "mmproj_path": str(Path(pair[1]).resolve()),
            "model_label": "google/gemma-4-12b-qat",
            "quantization_label": "Q4_0 (QAT)",
            "source_model_id": (
                MODEL_REPO
                if not str(pair[0]).startswith(str(Path.home() / ".lmstudio"))
                else "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
            ),
            "gpu_layers": options.get("gpu_layers", 999),
            "engine_token_batch": options.get("engine_token_batch", 512),
            "engine_microbatch": options.get("engine_microbatch", 128),
            "startup_timeout_s": options.get("startup_timeout_s", 240),
            "request_timeout_s": options.get("request_timeout_s", 180),
        }
