"""데이터 읽기 → 프레임 선택 → 크기 조절. 원본 파일은 수정하지 않습니다."""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .common import BenchError, file_hash, read_json, read_rows
from .video import VIDEO_SUFFIXES, extract_video


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def natural_key(path):
    """1.jpg, 2.jpg, 10.jpg를 숫자 순서로 읽기 위한 정렬 기준입니다."""
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", path.name)]


def discover_rows(root):
    """images/·frames/·videos/에서 평가 단위를 읽습니다. 영상 추출은 load_cases가 합니다."""
    rows = []
    photos = sorted((root / "images").glob("*"), key=natural_key)
    for path in photos:
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith("."):
            rows.append({"id": path.stem, "media_type": "image",
                         "image_paths": [path.relative_to(root).as_posix()]})
    for clip in sorted((root / "frames").glob("*"), key=natural_key):
        if not clip.is_dir() or clip.name.startswith("."):
            continue
        frames = [p.relative_to(root).as_posix() for p in sorted(clip.iterdir(), key=natural_key)
                  if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith(".")]
        if not frames:
            raise ValueError(f"영상 폴더에 이미지가 없습니다: {clip}")
        rows.append({"id": clip.name, "media_type": "video", "image_paths": frames})
    for path in sorted((root / "videos").glob("*"), key=natural_key):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES and not path.name.startswith("."):
            rows.append({"id": path.stem, "media_type": "video",
                         "video_path": path.relative_to(root).as_posix()})
    return rows


def load_cases(root, ids=None, video_options=None):
    """사진·프레임 묶음·원본 영상을 읽습니다. 원본 영상은 FFmpeg로 대표 프레임을 준비합니다."""
    root = Path(root)
    input_list = root / "model_inputs.jsonl"
    rows = read_rows(input_list) if input_list.exists() else discover_rows(root)
    if not rows:
        raise ValueError(f"평가 입력이 없습니다: {root}. images/·frames/·videos/ 또는 model_inputs.jsonl을 확인하세요.")
    all_ids = [r["id"] for r in rows]
    if any(not isinstance(case_id, str) or not case_id or case_id in {".", ".."}
           or any(char in case_id for char in "/\\:") for case_id in all_ids):
        raise ValueError("샘플 ID에는 경로 구분자를 넣을 수 없습니다.")
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Duplicate sample IDs")
    if ids and set(ids) - set(all_ids):
        raise ValueError(f"Unknown case IDs: {set(ids) - set(all_ids)}")
    manifest = read_json(root / "manifest.json") if (root / "manifest.json").exists() else {}
    frame_meta = {f["path"]: f for row in manifest.get("files", []) for f in row.get("frames", [])}
    cases = []
    for row in rows:
        if ids and row["id"] not in ids:
            continue
        if "video_path" in row:
            video_path = (root / row["video_path"]).resolve()
            if (row["media_type"] != "video" or not video_path.is_relative_to(root.resolve())
                    or not video_path.is_file() or "image_paths" in row):
                raise ValueError(f"영상은 video_path 하나로 지정하세요: {row['id']}")
            directory, extraction = extract_video(video_path, root / ".filename_frames", video_options)
            row = {**row,
                   "image_paths": [(directory / frame["path"]).relative_to(root.resolve()).as_posix()
                                   for frame in extraction["frames"]],
                   "timestamps_s": [frame["requested_time_s"] for frame in extraction["frames"]],
                   "video_extraction": extraction}
        paths = [(root / p).resolve() for p in row["image_paths"]]
        if not paths or any(not p.is_relative_to(root.resolve()) or not p.is_file() for p in paths):
            raise ValueError(f"Missing or out-of-root input: {row['id']}")
        if row["media_type"] not in ["image", "video"]:
            raise ValueError(f"Unknown media_type: {row['id']}")
        if row["media_type"] == "image" and len(paths) != 1:
            raise ValueError("A photo case must have one image")
        hashes = [file_hash(p) for p in paths]
        cases.append({**row, "paths": paths, "sha256": hashes,
                      "timestamps_s": row.get("timestamps_s", [frame_meta.get(p, {}).get("requested_time_s")
                                                                for p in row["image_paths"]])})
    return cases, manifest


def uniform_indices(n, k):
    """n개 후보에서 k개를 시간 간격이 고르게 선택합니다. 한 장이면 중앙입니다."""
    if k < 1 or k > n:
        raise BenchError("unsupported", f"Requested {k} distinct frames; only {n} available")
    return [n // 2] if k == 1 else [round(i * (n - 1) / (k - 1)) for i in range(k)]


def frame_features(path):
    with Image.open(path) as image:
        gray = ImageOps.grayscale(ImageOps.exif_transpose(image))
        # 같은 분석 크기에서 선명도를 비교합니다. 원본 해상도 차이의 영향을 줄입니다.
        gray.thumbnail((256, 256), Image.Resampling.LANCZOS)
        a = np.asarray(gray, dtype=np.float32)
        lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
        tiny = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS))
        bits = (tiny[:, 1:] > tiny[:, :-1]).reshape(-1)
        dhash = sum(int(b) << i for i, b in enumerate(bits))
        return float(lap.var()), dhash


def select_frames(paths, count, policy="uniform", duplicate_distance=6):
    """uniform=균등, blur=선명도, dedup=중복 억제, both=두 기준 조합입니다."""
    anchors = uniform_indices(len(paths), count)
    if policy == "uniform":
        return anchors, {"policy": policy, "fallback_count": 0}
    if policy not in ["blur", "dedup", "both"]:
        raise ValueError(f"Unknown selection policy: {policy}")
    features = [frame_features(p) for p in paths]
    chosen, fallback = [], 0
    # 시간 구간마다 한 장을 뽑아 영상의 앞·중간·끝이 유지되게 합니다.
    for b in range(count):
        candidates = list(range(b * len(paths) // count, (b + 1) * len(paths) // count))
        center = (candidates[0] + candidates[-1]) / 2
        if policy in ["dedup", "both"]:
            eligible = [i for i in candidates if all(
                (features[i][1] ^ features[j][1]).bit_count() > duplicate_distance for j in chosen)]
            if not eligible:
                eligible, fallback = candidates, fallback + 1
        else:
            eligible = candidates
        if policy in ["blur", "both"]:
            pick = max(eligible, key=lambda i: (features[i][0], -abs(i - center), -i))
        else:
            pick = min(eligible, key=lambda i: (abs(i - center), i))
        chosen.append(pick)
    return chosen, {"policy": policy, "fallback_count": fallback,
                    "blur_scores": [round(x[0], 4) for x in features],
                    "dhash": [hex(x[1]) for x in features], "duplicate_distance": duplicate_distance}


def prepare(case, setting):
    """선택한 이미지를 메모리에서 축소합니다. 원본보다 크게 확대하지 않습니다."""
    count = 1 if case["media_type"] == "image" else setting["frames"]
    if count == "all":
        count = len(case["paths"])  # 길이 기반 추출 결과를 모두 사용하고 싶을 때 선택합니다.
    idx, selection = select_frames(case["paths"], count, setting["selection"])
    images, sizes, hashes = [], [], []
    for i in idx:
        with Image.open(case["paths"][i]) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            scale = min(1.0, setting["short_edge_px"] / min(image.size))
            size = tuple(max(1, round(x * scale)) for x in image.size)
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            stream = io.BytesIO()
            image.save(stream, format="PNG")
        content = stream.getvalue()
        import hashlib
        images.append(content)
        sizes.append(list(size))
        hashes.append(hashlib.sha256(content).hexdigest())
    meta = {"frame_indices_0based": idx, "timestamps_s": [case["timestamps_s"][i] for i in idx],
            "source_paths": [case["image_paths"][i] for i in idx], "source_sha256": [case["sha256"][i] for i in idx],
            "prepared_sha256": hashes, "prepared_sizes_wh": sizes, "selection_details": selection,
            "images_per_request": len(images)}
    return tuple(images), meta


def eda(cases, manifest):
    """데이터 개수·크기·완전 중복을 정리합니다. 정답은 추론에 전달하지 않습니다."""
    sizes = []
    for case in cases:
        for path in case["paths"]:
            with Image.open(path) as im:
                sizes.append(list(im.size))
    hashes = [h for c in cases for h in c["sha256"]]
    return {"cases": len(cases), "images": sum(c["media_type"] == "image" for c in cases),
            "videos": sum(c["media_type"] == "video" for c in cases), "input_files": len(hashes),
            "exact_duplicate_files": len(hashes) - len(set(hashes)), "sizes_wh": sizes,
            "case_ids": [c["id"] for c in cases], "condition_counts_full_dataset": manifest.get("condition_counts"),
            "reference_human_reviewed": manifest.get("human_reviewed", False)}
