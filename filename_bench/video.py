"""원본 영상에서 균일한 시점의 대표 프레임을 추출합니다. 원본은 읽기만 합니다."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image

from .common import RunLock, digest, file_hash, read_json, write_json

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
DEFAULT_OPTIONS = {"seconds_per_frame": 10, "max_frames": 5,
                   "ffmpeg_path": "ffmpeg", "ffprobe_path": "ffprobe", "timeout_s": 120}


def sample_times(duration_s, seconds_per_frame=10, max_frames=5):
    """길이로 장수를 정하고, 각 구간 가운데 시점을 반환합니다. 단위는 초입니다."""
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("영상 길이는 0보다 큰 유한한 값이어야 합니다.")
    if not math.isfinite(seconds_per_frame) or seconds_per_frame <= 0:
        raise ValueError("seconds_per_frame은 0보다 커야 합니다.")
    if type(max_frames) is not int or max_frames < 1:
        raise ValueError("max_frames는 1 이상의 정수여야 합니다.")
    count = min(max_frames, math.ceil(duration_s / seconds_per_frame))
    return [duration_s * (index + 0.5) / count for index in range(count)]


def executable(path):
    found = shutil.which(str(path))
    if not found:
        raise RuntimeError(f"실행 파일을 찾을 수 없습니다: {path}. FFmpeg와 ffprobe 설치·경로를 확인하세요.")
    return found


def run_tool(command, timeout):
    """shell을 거치지 않고 실행합니다. 시간 초과·취소 때 자식 프로세스도 정리됩니다."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} 실행 실패: {result.stderr[-1500:]}")
    return result.stdout


def video_duration(path, ffprobe, timeout):
    """첫 번째 영상 스트림의 길이를 우선 사용하고 컨테이너 길이를 보조로 씁니다."""
    info = json.loads(run_tool([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=duration:format=duration", "-of", "json", str(path),
    ], timeout))
    streams = info.get("streams", [])
    if not streams:
        raise ValueError(f"영상 스트림이 없습니다: {path.name}")
    for value in [streams[0].get("duration"), info.get("format", {}).get("duration")]:
        try:
            duration = float(value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise ValueError(f"영상 길이를 확인할 수 없습니다: {path.name}")


def extract_video(path, cache_root, options=None):
    """성공한 추출 결과만 재사용합니다. 실패한 작업 폴더는 보존하고 새로 시도합니다."""
    path = Path(path).resolve()
    settings = {**DEFAULT_OPTIONS, **(options or {})}
    # 실제 파일을 읽기 전에 간격·상한의 입력 오류부터 확인합니다.
    sample_times(1, settings["seconds_per_frame"], settings["max_frames"])
    timeout = settings["timeout_s"]
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_s는 0보다 커야 합니다.")
    ffmpeg = executable(settings["ffmpeg_path"])
    ffprobe = executable(settings["ffprobe_path"])
    identity = {
        "algorithm": "uniform-bin-centers-v1", "source_sha256": file_hash(path),
        "seconds_per_frame": settings["seconds_per_frame"], "max_frames": settings["max_frames"],
        "ffmpeg_version": run_tool([ffmpeg, "-version"], timeout).splitlines()[0],
        "ffprobe_version": run_tool([ffprobe, "-version"], timeout).splitlines()[0],
    }
    directory = Path(cache_root).resolve() / digest(identity)[:24]
    directory.mkdir(parents=True, exist_ok=True)

    # 동일한 원본·설정을 동시에 준비해도 완료 기록을 덮어쓰지 않습니다.
    with RunLock(directory):
        completed = directory / "completed.json"
        if completed.exists():
            result = read_json(completed)
            if result["identity"] != identity:
                raise ValueError("프레임 캐시의 원본·설정 기록이 다릅니다.")
            for frame in result["frames"]:
                cached_path = (directory / frame["path"]).resolve()
                if (not cached_path.is_relative_to(directory) or not cached_path.is_file()
                        or file_hash(cached_path) != frame["sha256"]):
                    raise ValueError("완료된 프레임 캐시가 변경됐습니다. 새 데이터 폴더에서 실행하세요.")
            return directory, result

        started = time.perf_counter()
        duration = video_duration(path, ffprobe, timeout)
        times = sample_times(duration, settings["seconds_per_frame"], settings["max_frames"])
        attempt = directory / ("attempt_" + uuid.uuid4().hex[:12])
        attempt.mkdir()
        frames = []
        for index, timestamp in enumerate(times, 1):
            output = attempt / f"{index:03d}.png"
            # -ss를 입력 앞에 두면 앞부분 전체를 디코딩할 필요를 줄일 수 있습니다.
            # 정확한 탐색을 사용해 I-frame에만 제한하지 않고 해당 시점의 이미지를 얻습니다.
            run_tool([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                "-threads", "1", "-ss", f"{timestamp:.9f}", "-accurate_seek", "-i", str(path),
                "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-threads", "1", str(output),
            ], timeout)
            with Image.open(output) as image:
                image.verify()
            frames.append({"path": output.relative_to(directory).as_posix(),
                           "requested_time_s": timestamp, "sha256": file_hash(output)})

        result = {"identity": identity, "duration_s": duration, "frames": frames,
                  "initial_extraction_s": time.perf_counter() - started}
        # 모든 PNG가 준비된 뒤에만 완료 표시를 남깁니다. 중단된 결과는 다음에 쓰지 않습니다.
        write_json(completed, result)
        return directory, result
