"""가까운 keyframe을 우선하고, 부족하거나 멀면 목표 시점의 일반 프레임을 보충합니다."""

import math
import os
import re
import subprocess
from pathlib import Path

from PIL import Image
from filename_bench.common import digest


def validate_options(options):
    mode = options.get("mode", "interval")
    if mode not in ("interval", "nearest_keyframe"):
        raise ValueError("video_extraction.mode는 interval 또는 nearest_keyframe이어야 합니다.")
    if mode == "nearest_keyframe":
        fractions = options.get("fractions")
        if (not isinstance(fractions, list) or not fractions
                or any(type(x) not in (int, float) or not math.isfinite(x)
                       or not 0 < x < 1 for x in fractions)
                or fractions != sorted(set(fractions))):
            raise ValueError("fractions는 0~1 사이의 중복 없는 비율을 오름차순으로 지정하세요.")
        if type(options.get("max_frames")) is not int or options["max_frames"] < len(fractions):
            raise ValueError("max_frames는 지정한 비율 개수 이상이어야 합니다.")
        for name, default in (("max_keyframe_distance_s", 0.5),
                              ("max_keyframe_distance_fraction", 0.05)):
            value = options.get(name, default)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name}은 0 이상의 유한한 숫자여야 합니다.")
        if options.get("max_keyframe_distance_fraction", 0.05) > 1:
            raise ValueError("max_keyframe_distance_fraction은 1 이하여야 합니다.")


def nearest_indices(timestamps, targets):
    if not timestamps:
        raise ValueError("영상에서 keyframe을 찾지 못했습니다.")
    # 같은 거리이면 앞쪽 프레임, 같은 프레임이 여러 목표에 대응하면 한 장만 사용합니다.
    return sorted({min(range(len(timestamps)), key=lambda i: (abs(timestamps[i] - t), timestamps[i]))
                   for t in targets})


def _run(command, timeout, event):
    if event.is_set():
        raise InterruptedError("분석을 취소했습니다.")
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if event.is_set():
        raise InterruptedError("분석을 취소했습니다.")
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result


def _scan(command, timeout, event, keyframes_only=False):
    filters = "select=key," if keyframes_only else ""
    scan = _run(command + ["-vf", filters + "showinfo=checksum=0", "-fps_mode", "vfr",
                           "-f", "null", "-"], timeout, event)
    entries = re.findall(r"\bn:\s*(\d+)\s+pts:.*?\bpts_time:([-+\deE.]+).*?\biskey:([01])\b",
                         scan.stderr)
    return [(int(n), float(t), key == "1") for n, t, key in entries
            if math.isfinite(float(t)) and float(t) >= 0]


def select_targets(keyframes, targets, max_distance):
    """같은 keyframe에는 가장 가까운 목표 하나만 배정하고 나머지는 보충합니다."""
    nearest = [min(range(len(keyframes)),
                   key=lambda i: (abs(keyframes[i][1] - t), keyframes[i][1]))
               if keyframes else None for t in targets]
    owners = {i: min((j for j, k in enumerate(nearest) if k == i),
                     key=lambda j: (abs(keyframes[i][1] - targets[j]), j))
              for i in set(nearest) if i is not None}
    selections = []
    for j, (target, index) in enumerate(zip(targets, nearest)):
        distance = abs(keyframes[index][1] - target) if index is not None else None
        reason = ("no_keyframes" if index is None else
                  "keyframe_too_far" if distance > max_distance else
                  "duplicate_keyframe" if owners[index] != j else None)
        selections.append({"target_timestamp_s": target,
                           "nearest_keyframe_timestamp_s": keyframes[index][1] if index is not None else None,
                           "nearest_keyframe_distance_s": distance,
                           "method": "target_frame" if reason else "keyframe",
                           "fallback_reason": reason,
                           "keyframe_index": keyframes[index][0] if not reason else None})
    return selections


def extract_keyframes(ffmpeg, path, sha, duration, options, cache, event):
    validate_options(options)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("영상 길이를 확인할 수 없습니다.")
    timeout = options.get("timeout_s", 120)
    base = [ffmpeg, "-hide_banner", "-nostdin", "-nostats"]
    input_args = ["-i", str(path), "-map", "0:v:0", "-an", "-sn"]
    key_command = base + ["-skip_frame", "nokey"] + input_args
    valid = _scan(key_command, timeout, event, keyframes_only=True)
    targets = [duration * fraction for fraction in options["fractions"]]
    max_distance = min(options.get("max_keyframe_distance_s", 0.5),
                       duration * options.get("max_keyframe_distance_fraction", 0.05))
    selections = select_targets(valid, targets, max_distance)
    fallback = any(s["method"] == "target_frame" for s in selections)
    # 보충이 필요할 때만 일반 프레임을 디코딩합니다. 실제 PTS에 가장 가까운 프레임을
    # 선택하므로 VFR, 짧은 영상, 마지막 프레임 이후 목표도 기록과 픽셀이 일치합니다.
    command = base + input_args if fallback else key_command
    candidates = _scan(command, timeout, event) if fallback else valid
    if not candidates:
        raise ValueError("영상에서 추출할 수 있는 프레임을 찾지 못했습니다.")
    selected_by_time = {}
    for selection in selections:
        desired = (selection["target_timestamp_s"] if selection["method"] == "target_frame"
                   else selection["nearest_keyframe_timestamp_s"])
        frame = min(candidates, key=lambda f: (abs(f[1] - desired), f[1]))
        selected_by_time[frame[1]] = frame
        selection.update(timestamp_s=frame[1], is_keyframe=frame[2],
                         distance_to_target_s=abs(frame[1] - selection["target_timestamp_s"]))
    selected = sorted(selected_by_time.values(), key=lambda f: f[1])
    times = [t for _, t, _ in selected]
    for selection in selections:
        selection["output_index"] = times.index(selection["timestamp_s"])
    metadata = {"mode": "nearest_keyframe", "algorithm": "nearest-keyframe-target-fallback-v2",
                "duration_s": duration,
                "fractions": options["fractions"], "target_timestamps_s": targets,
                "selected_timestamps_s": times,
                "selected_keyframe_timestamps_s": [t for _, t, key in selected if key],
                "selected_keyframe_indices": [n for n, t, _ in valid
                                              if t in times],
                "available_keyframes": len(valid), "keyframe_only": all(f[2] for f in selected),
                "max_keyframe_distance_s": max_distance,
                "fallback_target_count": sum(s["method"] == "target_frame" for s in selections),
                "selections": selections,
                "unique_frames": len(selected)}
    identity = {"sha": sha, **metadata}
    directory = Path(cache) / digest(identity)[:24]
    directory.mkdir(parents=True, exist_ok=True)
    frames = [directory / f"{i:03d}.png" for i in range(len(selected))]
    if not all(frame.is_file() for frame in frames):
        expression = "+".join(f"eq(n\\,{n})" for n, _, _ in selected)
        filters = "" if fallback else "select=key,"
        _run(command + ["-loglevel", "error", "-vf", f"{filters}select={expression}",
                        "-fps_mode", "vfr", "-frames:v", str(len(selected)),
                        "-start_number", "0", "-y", str(directory / "%03d.partial.png")], timeout, event)
        temporary = [directory / f"{i:03d}.partial.png" for i in range(len(selected))]
        for frame in temporary:
            with Image.open(frame) as im:
                im.verify()
        for source, target in zip(temporary, frames):
            source.replace(target)
    return frames, times, metadata
