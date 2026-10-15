"""python app.py 로 시작합니다. --mock은 화면 확인용이며 실제 이름은 바꾸지 않습니다."""

from pathlib import Path
import argparse
import shutil
import sys
import webbrowser

from filename_app.runtime import app_home
from filename_app.service import AppService
from filename_app.server import create_server


def resource_root():
    # PyInstaller 앱에서는 읽기 전용 자원을 실행 파일 옆의 내부 디렉터리에서 찾습니다.
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_config(root, home):
    """첫 실행에서만 편집용 설정을 복사합니다. 업그레이드해도 사용자 설정은 보존합니다."""
    for relative in [
        "configs/app.json",
        "configs/profiles/gemma4_12b.json",
        "assets/prompt_v3_five_tags.txt",
        "assets/schema_v3_five_tags.json",
    ]:
        target = home / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
    return home / "configs/app.json"


def main():
    parser = argparse.ArgumentParser(description="filename 로컬 사진·영상 이름 정리")
    parser.add_argument("--config", help="앱 JSON 설정 경로")
    parser.add_argument(
        "--mock", action="store_true", help="모델 없이 화면 확인, 실제 이름 변경 차단"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--port", type=int, default=0, help="0이면 빈 로컬 포트 자동 선택"
    )
    args = parser.parse_args()
    root = resource_root()
    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    config = (
        Path(args.config).expanduser().resolve()
        if args.config
        else user_config(root, home)
    )
    service = AppService(config, home / "cache", mock=args.mock)
    server, url = create_server(service, root / "filename_app/static", args.port)
    # 창 없는 배포판에서도 현재 실행 주소를 다시 열 수 있습니다.
    (home / "last-url.txt").write_text(url, encoding="utf-8")
    if sys.stdout:
        print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        service.cancel_event.set()
    finally:
        server.server_close()
        if service.thread:
            service.thread.join()


if __name__ == "__main__":
    main()
