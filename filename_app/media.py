"""앱의 임의 폴더 입력을 벤치마크와 같은 이미지 전처리에 연결합니다."""

import io
import os
import re
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageOps
from filename_bench.common import digest, file_hash
from filename_bench.data import prepare
from filename_bench.video import VIDEO_SUFFIXES, sample_times
from .keyframes import extract_keyframes, validate_options


def media_input(path, sha, cfg, cache, event):
    path = Path(path)
    frames = [path]
    times = [None]
    extraction_meta = None
    if path.suffix.lower() in VIDEO_SUFFIXES:
        options = cfg["video_extraction"]
        validate_options(options)
        executable = options.get("ffmpeg_path", "ffmpeg")
        ffmpeg = shutil.which(executable)
        if not ffmpeg and executable in ("ffmpeg", "auto"):
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg_path를 확인하세요.")
        timeout = options.get("timeout_s", 120)
        # 배포판은 FFmpeg를 포함합니다. ffprobe 없이도 영상 길이를 읽습니다.
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
        if not match:
            raise ValueError(
                "영상 길이를 읽을 수 없습니다. 파일이 손상되었는지 확인하세요."
            )
        h, m, s = map(float, match.groups())
        duration = 3600 * h + 60 * m + s
        if options.get("mode") == "nearest_keyframe":
            frames, times, extraction_meta = extract_keyframes(
                ffmpeg, path, sha, duration, options, cache, event
            )
        else:
            frames, times = interval_frames(ffmpeg, path, sha, duration, options, cache, event)
    case = {
        "media_type": "video" if path.suffix.lower() in VIDEO_SUFFIXES else "image",
        "paths": frames,
        "timestamps_s": times,
        "image_paths": [str(p) for p in frames],
        "sha256": [file_hash(p) for p in frames],
    }
    setting = dict(cfg["baseline"])
    if isinstance(setting["frames"], int):
        setting["frames"] = min(setting["frames"], len(frames))
    images, meta = prepare(case, setting)
    if extraction_meta is not None:
        meta["video_extraction"] = extraction_meta
    with Image.open(io.BytesIO(images[0])) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((240, 160))
        stream = io.BytesIO()
        im.save(stream, format="JPEG", quality=80)
    return images, meta, stream.getvalue()


def interval_frames(ffmpeg, path, sha, duration, options, cache, event):
    timeout = options.get("timeout_s", 120)
    times = sample_times(duration, options["seconds_per_frame"], options["max_frames"])
    directory = Path(cache) / digest({"sha": sha, "times": times})[:24]
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, timestamp in enumerate(times):
        if event.is_set():
            raise InterruptedError("분석을 취소했습니다.")
        frame = directory / f"{i:03d}.png"
        if not frame.exists():
            temporary = directory / f"{i:03d}.partial.png"
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-ss",
                    str(timestamp),
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-sn",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode:
                raise RuntimeError(result.stderr[-1000:])
            with Image.open(temporary) as im:
                im.verify()
            temporary.replace(frame)
        frames.append(frame)
    return frames, times
