"""모델을 실제 다운로드하지 않고 이어받기·해시 검사·없는 모델 준비를 검증합니다."""

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from filename_app.runtime import download, setup


class Response(io.BytesIO):
    def __init__(self, data, status=200, offset=0):
        super().__init__(data)
        self.status = status
        self.headers = {
            "Content-Length": str(len(data)),
            "Content-Range": f"bytes {offset}-999/1000",
        }


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "model.gguf"
        self.data = b"GGUF-test-bytes"
        self.sha = hashlib.sha256(self.data).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def test_resume_then_reuse(self):
        self.target.with_name("model.gguf.part").write_bytes(self.data[:4])
        with patch(
            "urllib.request.urlopen", return_value=Response(self.data[4:], 206, 4)
        ) as request:
            download(
                "https://example.com/model",
                self.target,
                self.sha,
                lambda _: None,
                threading.Event(),
            )
            self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=4-")
        self.assertEqual(self.target.read_bytes(), self.data)
        with patch("urllib.request.urlopen") as request:
            download(
                "https://example.com/model",
                self.target,
                self.sha,
                lambda _: None,
                threading.Event(),
            )
            request.assert_not_called()

    def test_bad_hash_never_becomes_model(self):
        with patch("urllib.request.urlopen", return_value=Response(b"corrupt")):
            with self.assertRaises(ValueError):
                download(
                    "https://example.com/model",
                    self.target,
                    self.sha,
                    lambda _: None,
                    threading.Event(),
                )
        self.assertFalse(self.target.exists())
        self.assertTrue(list(self.root.glob("*.invalid-*")))

    def test_cancel_preserves_partial(self):
        event = threading.Event()
        event.set()
        self.target.with_name("model.gguf.part").write_bytes(b"GGUF")
        with patch("urllib.request.urlopen") as request:
            with self.assertRaises(InterruptedError):
                download(
                    "https://example.com/model",
                    self.target,
                    self.sha,
                    lambda _: None,
                    event,
                )
            request.assert_not_called()
        self.assertEqual(self.target.with_name("model.gguf.part").read_bytes(), b"GGUF")

    def test_missing_model_downloads_pair(self):
        executable = self.root / "server"
        executable.write_bytes(b"fixture")

        def fixture(url, path, *args):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"GGUFfixture")
            return path

        with patch("filename_app.runtime.find_model", return_value=None), patch(
            "filename_app.runtime.download", side_effect=fixture
        ) as request:
            result = setup(
                {"server_path": str(executable)},
                self.root / "cache",
                lambda _: None,
                threading.Event(),
            )
            self.assertEqual(request.call_count, 2)
            self.assertTrue(Path(result["model_path"]).is_file())
            self.assertTrue(Path(result["mmproj_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
