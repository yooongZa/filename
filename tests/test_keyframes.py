"""고정 GOP 영상의 실제 픽셀로 키프레임 선택과 기존 추출 경로를 검사합니다."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image
from filename_app.keyframes import nearest_indices, select_targets, validate_options
from filename_app.media import media_input
from filename_bench.common import ROOT, file_hash


class KeyframeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import imageio_ffmpeg
        cls.ffmpeg = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.video = cls.root / "gop.mp4"
        subprocess.run([cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=160x120:rate=10", "-t", "10", "-c:v", "libx264",
                        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-bf", "0",
                        str(cls.video)], check=True, timeout=30)
        cls.sha = file_hash(cls.video)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def config(self, mode="nearest_keyframe"):
        cfg = json.loads((ROOT / "configs/profiles/gemma4_12b.json").read_text())
        cfg["baseline"].update(short_edge_px=480, frames=3)
        cfg["video_extraction"].update(ffmpeg_path=self.ffmpeg, max_frames=3, mode=mode,
                                       fractions=[0.2, 0.5, 0.8], seconds_per_frame=1)
        return cfg

    def test_nearest_real_keyframes_and_pixels(self):
        cfg = self.config()
        cfg["video_extraction"].update(max_keyframe_distance_s=2,
                                       max_keyframe_distance_fraction=0.2)
        _, meta, _ = media_input(self.video, self.sha, cfg, self.root / "key-cache", threading.Event())
        self.assertEqual(meta["timestamps_s"], [3.0, 6.0, 9.0])
        self.assertEqual(meta["images_per_request"], 3)
        self.assertEqual(meta["video_extraction"]["target_timestamps_s"], [2.0, 5.0, 8.0])
        # 전체 프레임을 디코딩한 기준 이미지와 비교해 실제 키프레임의 픽셀인지 확인합니다.
        refs = self.root / "refs"
        refs.mkdir(exist_ok=True)
        subprocess.run([self.ffmpeg, "-v", "error", "-i", str(self.video), "-vf",
                        r"select=eq(n\,30)+eq(n\,60)+eq(n\,90)", "-fps_mode", "vfr",
                        "-start_number", "0", "-y", str(refs / "%03d.png")], check=True, timeout=30)
        for i, source in enumerate(meta["source_paths"]):
            with Image.open(source) as actual, Image.open(refs / f"{i:03d}.png") as expected:
                self.assertEqual(actual.tobytes(), expected.tobytes())
        _, cached, _ = media_input(self.video, self.sha, cfg, self.root / "key-cache", threading.Event())
        self.assertEqual(cached["source_sha256"], meta["source_sha256"])
        self.assertEqual(file_hash(self.video), self.sha)

    def test_single_keyframe_fills_three_target_frames(self):
        video = self.root / "short.mp4"
        subprocess.run([self.ffmpeg, "-v", "error", "-i", str(self.video), "-t", "1",
                        "-c", "copy", "-y", str(video)], check=True, timeout=30)
        _, meta, _ = media_input(video, file_hash(video), self.config(), self.root / "short-cache", threading.Event())
        self.assertEqual(meta["images_per_request"], 3)
        self.assertEqual(meta["timestamps_s"], [0.2, 0.5, 0.8])
        self.assertEqual(meta["video_extraction"]["fallback_target_count"], 3)
        self.assertFalse(meta["video_extraction"]["keyframe_only"])
        # 목표 시점의 일반 프레임을 전부 디코딩한 기준 픽셀과 비교합니다.
        refs = self.root / "short-refs"
        refs.mkdir(exist_ok=True)
        subprocess.run([self.ffmpeg, "-v", "error", "-i", str(video), "-vf",
                        r"select=eq(n\,2)+eq(n\,5)+eq(n\,8)", "-fps_mode", "vfr",
                        "-start_number", "0", "-y", str(refs / "%03d.png")], check=True, timeout=30)
        for i, source in enumerate(meta["source_paths"]):
            with Image.open(source) as actual, Image.open(refs / f"{i:03d}.png") as expected:
                self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_far_keyframes_fall_back_to_targets(self):
        cfg = self.config()
        _, meta, _ = media_input(self.video, self.sha, cfg, self.root / "far-cache", threading.Event())
        self.assertEqual(meta["timestamps_s"], [2.0, 5.0, 8.0])
        self.assertEqual(meta["video_extraction"]["max_keyframe_distance_s"], 0.5)
        self.assertEqual([s["fallback_reason"] for s in meta["video_extraction"]["selections"]],
                         ["keyframe_too_far"] * 3)
        _, cached, _ = media_input(self.video, self.sha, cfg, self.root / "far-cache", threading.Event())
        self.assertEqual(cached["source_sha256"], meta["source_sha256"])

    def test_mixed_keyframe_and_normal_frames(self):
        cfg = self.config()
        cfg["video_extraction"]["fractions"] = [0.3, 0.5, 0.8]
        _, meta, _ = media_input(self.video, self.sha, cfg, self.root / "mixed-cache", threading.Event())
        self.assertEqual(meta["timestamps_s"], [3.0, 5.0, 8.0])
        self.assertEqual(meta["video_extraction"]["selected_keyframe_timestamps_s"], [3.0])
        self.assertEqual([s["method"] for s in meta["video_extraction"]["selections"]],
                         ["keyframe", "target_frame", "target_frame"])

    def test_one_actual_frame_is_not_artificially_duplicated(self):
        video = self.root / "one-frame.mp4"
        subprocess.run([self.ffmpeg, "-v", "error", "-i", str(self.video), "-frames:v", "1",
                        "-c:v", "libx264", "-y", str(video)], check=True, timeout=30)
        _, meta, _ = media_input(video, file_hash(video), self.config(), self.root / "one-cache", threading.Event())
        self.assertEqual(meta["images_per_request"], 1)
        self.assertEqual([s["output_index"] for s in meta["video_extraction"]["selections"]], [0, 0, 0])

    def test_failure_does_not_publish_incomplete_cache_and_retry_succeeds(self):
        from filename_app import keyframes
        original_run = keyframes._run
        cache = self.root / "failure-cache"
        def fail_output(command, timeout, event):
            if command[-1].endswith(".partial.png"):
                raise RuntimeError("simulated extraction failure")
            return original_run(command, timeout, event)
        with patch.object(keyframes, "_run", side_effect=fail_output):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                media_input(self.video, self.sha, self.config(), cache, threading.Event())
        self.assertFalse(list(cache.rglob("*.png")))
        _, meta, _ = media_input(self.video, self.sha, self.config(), cache, threading.Event())
        self.assertEqual(meta["timestamps_s"], [2.0, 5.0, 8.0])

    def test_interval_mode_preserved(self):
        _, meta, _ = media_input(self.video, self.sha, self.config("interval"), self.root / "old-cache", threading.Event())
        self.assertEqual(meta["images_per_request"], 3)
        self.assertAlmostEqual(meta["timestamps_s"][0], 10/6)
        self.assertAlmostEqual(meta["timestamps_s"][1], 5)
        self.assertAlmostEqual(meta["timestamps_s"][2], 50/6)
        self.assertNotIn("video_extraction", meta)

    def test_cancel_then_retry(self):
        event = threading.Event()
        event.set()
        cache = self.root / "retry-cache"
        with self.assertRaises(InterruptedError):
            media_input(self.video, self.sha, self.config(), cache, event)
        self.assertFalse(cache.exists())
        event.clear()
        _, meta, _ = media_input(self.video, self.sha, self.config(), cache, event)
        self.assertEqual(meta["timestamps_s"], [2.0, 5.0, 8.0])

    def test_duplicate_and_missing_keyframes_have_fallback_reasons(self):
        choices = select_targets([(0, 0.3, True)], [0.2, 0.5, 0.8], 1)
        self.assertEqual([s["fallback_reason"] for s in choices],
                         [None, "duplicate_keyframe", "duplicate_keyframe"])
        self.assertEqual(select_targets([], [0.5], 0.1)[0]["fallback_reason"], "no_keyframes")

    def test_tie_duplicate_and_invalid_settings(self):
        self.assertEqual(nearest_indices([0, 2, 4], [1, 1.1, 1.2]), [0, 1])
        with self.assertRaises(ValueError):
            nearest_indices([], [1])
        for fractions in [[0.8, 0.2], [0.2, 0.2], [True], [float("nan")], [0], [1], []]:
            with self.subTest(fractions=fractions), self.assertRaises(ValueError):
                validate_options({"mode": "nearest_keyframe", "fractions": fractions, "max_frames": 3})
        with self.assertRaises(ValueError):
            validate_options({"mode": "nearest_keyframe", "fractions": [0.2, 0.5, 0.8], "max_frames": 2})
        for name in ("max_keyframe_distance_s", "max_keyframe_distance_fraction"):
            for value in [-1, True, float("nan"), float("inf")]:
                with self.subTest(option=name, value=value), self.assertRaises(ValueError):
                    validate_options({"mode": "nearest_keyframe", "fractions": [0.5], "max_frames": 3, name: value})


if __name__ == "__main__":
    unittest.main()
