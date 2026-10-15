"""패키징된 실행 파일의 모의 화면·분석 API를 확인합니다. 실사용 모델 검증과 구분합니다."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from PIL import Image
import imageio_ffmpeg

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    photo = root / "photos"
    photo.mkdir()
    Image.new("RGB", (64, 48), "green").save(photo / "test.png")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=64x48:r=5",
            "-t",
            "2",
            str(photo / "test.mp4"),
        ],
        check=True,
    )
    environment = dict(os.environ, FILENAME_HOME=str(root / "app-home"))
    process = subprocess.Popen(
        [sys.argv[1], "--mock", "--no-browser", "--port", "0"], env=environment
    )
    # 사용자 설정 디렉터리는 패키지와 소스에서 같은 규칙입니다.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from filename_app.runtime import app_home

    def call(route, body=None):
        url = (root / "app-home" / "last-url.txt").read_text()
        base = url.split("/#")[0]
        token = url.split("token=")[1]
        request = urllib.request.Request(
            base + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Filename-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError("패키징 앱이 시작 중 종료됐습니다.")
            try:
                state = call("/api/state")
                if state["mock"]:
                    break
            except (OSError, ValueError):
                time.sleep(1)
        else:
            raise RuntimeError("앱 API가 시작되지 않았습니다.")
        profile_path = root / "app-home/configs/profiles/gemma4_12b.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["video_extraction"]["ffmpeg_path"] = "auto"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        call("/api/analyze", {"folder": str(photo)})
        for _ in range(30):
            state = call("/api/state")
            if not state["busy"]:
                break
            time.sleep(0.3)
        assert not state["error"], state
        assert len(state["items"]) == 2, state
        assert all(row["status"] == "ready" for row in state["items"]), state
        assert len(state["items"][0]["tags"]) == 5
        assert (photo / "test.png").exists()
        print(
            "PASS: packaged API, bundled FFmpeg, photo/video 5-tag mock analysis, originals untouched"
        )
    finally:
        try:
            call("/api/quit", {})
        except Exception:
            process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
