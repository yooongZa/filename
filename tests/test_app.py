"""사용자 원본 대신 임시 파일로 이름 변경·중단 복구·자동 모드를 검사합니다."""

import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from filename_bench.common import ROOT, file_hash
from filename_app.files import FolderLock, History, make_name, tagged_name
from filename_app.service import AppService, DemoBackend, load_app_config
from filename_app.runtime import find_model
from filename_app.server import create_server

TAGS = ["해변", "자전거", "도로", "라이딩", "야외"]


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.photo = self.media / "IMG_001.png"
        Image.new("RGB", (80, 60), "green").save(self.photo)
        self.sha = file_hash(self.photo)
        cfg = json.loads((ROOT / "configs/app.json").read_text())
        cfg["profile_path"] = str(ROOT / "configs/profiles/gemma4_12b.json")
        self.config = self.root / "app.json"
        self.config.write_text(json.dumps(cfg))

    def tearDown(self):
        self.tmp.cleanup()

    def test_five_tags_and_invalid_names(self):
        self.assertEqual(
            make_name("IMG.jpg", TAGS), "IMG__해변_자전거_도로_라이딩_야외.jpg"
        )
        for tags in [
            TAGS[:4],
            TAGS + ["추가"],
            ["해변"] * 5,
            TAGS[:4] + ["../폴더"],
            TAGS[:4] + ["태그_하나"],
        ]:
            with self.assertRaises(ValueError):
                make_name("IMG.jpg", tags)
        self.assertTrue(tagged_name(make_name("IMG.jpg", TAGS)))
        self.assertFalse(tagged_name("IMG__해변.jpg"))

    def test_rename_skip_undo_and_collision(self):
        with FolderLock(self.media):
            history = History(self.media)
            target = make_name(self.photo.name, TAGS)
            history.rename(self.photo.name, target, self.sha)
            self.assertEqual(file_hash(self.media / target), self.sha)
            self.assertIn(target, history.done_paths())
            self.assertTrue(history.undo()[0]["ok"])
            self.assertTrue(self.photo.exists())
            (self.media / target).write_bytes(b"keep existing")
            with self.assertRaises(FileExistsError):
                history.rename(self.photo.name, target, self.sha)
            self.assertEqual((self.media / target).read_bytes(), b"keep existing")
            history.close()

    def test_crash_after_rename_recovers(self):
        with FolderLock(self.media):
            history = History(self.media)
            target = make_name(self.photo.name, TAGS)
            with patch.object(
                history, "status", side_effect=RuntimeError("power loss")
            ):
                with self.assertRaises(RuntimeError):
                    history.rename(self.photo.name, target, self.sha)
            history.close()
            recovered = History(self.media)
            self.assertIn(target, recovered.done_paths())
            recovered.close()

    def test_changed_source_and_folder_lock(self):
        with FolderLock(self.media):
            history = History(self.media)
            self.photo.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                history.rename(
                    self.photo.name, make_name(self.photo.name, TAGS), self.sha
                )
            with self.assertRaises(RuntimeError):
                with FolderLock(self.media):
                    pass
            history.close()

    def run_analysis(self, review, automatic, backend=DemoBackend):
        service = AppService(self.config, self.root / "cache")
        with patch("filename_app.service.setup", return_value={}), patch(
            "filename_app.service.AppBackend",
            side_effect=lambda cfg, schema: backend(cfg),
        ):
            service.analyze(
                str(self.media),
                {"review_before_rename": review, "auto_rename": automatic},
            )
            service.thread.join(15)
        self.assertFalse(service.thread.is_alive())
        self.assertFalse(service.snapshot()["error"], service.snapshot())
        return service

    def test_auto_rename_and_second_run_skips(self):
        service = self.run_analysis(False, True)
        row = service.snapshot()["items"][0]
        self.assertEqual(row["status"], "done")
        self.assertEqual(file_hash(self.media / row["proposed"]), self.sha)
        with patch("filename_app.service.setup") as setup:
            service.analyze(str(self.media))
            service.thread.join(10)
            setup.assert_not_called()
        self.assertEqual(service.snapshot()["skipped"], 1)

    def test_review_overrides_auto_and_manual_apply(self):
        service = self.run_analysis(True, True)
        self.assertTrue(self.photo.exists())
        service.rename([{"id": "0", "tags": TAGS}])
        service.thread.join(10)
        self.assertFalse(service.snapshot()["error"])
        self.assertEqual(service.snapshot()["items"][0]["status"], "done")

    def test_invalid_output_never_auto_renames(self):
        class Invalid(DemoBackend):
            def infer(self, *args):
                return {"raw_output": "not JSON"}

        service = self.run_analysis(False, True, Invalid)
        self.assertTrue(self.photo.exists())
        self.assertEqual(service.snapshot()["items"][0]["status"], "review")

    def test_cancel_and_double_run(self):
        started = threading.Event()
        release = threading.Event()

        class Slow(DemoBackend):
            def infer(self, *args):
                started.set()
                release.wait(5)
                return super().infer(*args)

        Image.new("RGB", (80, 60), "red").save(self.media / "second.png")
        service = AppService(self.config, self.root / "cache")
        with patch("filename_app.service.setup", return_value={}), patch(
            "filename_app.service.AppBackend", side_effect=lambda cfg, schema: Slow(cfg)
        ):
            service.analyze(
                str(self.media), {"review_before_rename": False, "auto_rename": True}
            )
            self.assertTrue(started.wait(5))
            with self.assertRaises(ValueError):
                service.analyze(str(self.media))
            service.cancel()
            release.set()
            service.thread.join(10)
        self.assertTrue(self.photo.exists())
        self.assertEqual(len(service.snapshot()["items"]), 1)

    def test_existing_model_discovery(self):
        models = self.root / "models"
        models.mkdir()
        model = models / "gemma-4-12B-it-QAT-Q4_0.gguf"
        projector = models / "mmproj-gemma-4-12B-it-QAT-BF16.gguf"
        model.write_bytes(b"GGUF")
        projector.write_bytes(b"GGUF")
        # 사용자가 지정한 모델·프로젝터 경로를 그대로 재사용합니다.
        pair = find_model(
            {"model_path": str(model), "mmproj_path": str(projector)},
            self.root / "cache",
        )
        self.assertEqual(pair, (model, projector))

    def test_loopback_api_requires_token(self):
        service = AppService(self.config, self.root / "cache", mock=True)
        server, url = create_server(service, ROOT / "filename_app/static")
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = url.split("/#")[0]
            token = url.split("token=")[1]
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/api/state")
            request = urllib.request.Request(
                base + "/api/state", headers={"X-Filename-Token": token}
            )
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["mock"])
            request = urllib.request.Request(
                base + "/api/state",
                headers={"X-Filename-Token": token, "Origin": "https://example.com"},
            )
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(request)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_profile_used_by_app(self):
        # 독립 앱 저장소에 포함한 설정에서 프롬프트·출력 규칙까지 읽습니다.
        cfg = load_app_config(ROOT / "configs/app.json")
        self.assertEqual(cfg["baseline"]["max_new_tokens"], 512)
        self.assertEqual(cfg["baseline"]["frames"], "all")
        self.assertIn("five_tags", cfg["prompt_path"])
        self.assertEqual(cfg["schema"]["properties"]["keywords"]["minItems"], 5)
        self.assertEqual(cfg["schema"]["properties"]["keywords"]["maxItems"], 5)


if __name__ == "__main__":
    unittest.main()
