"""외부 서버 없이 127.0.0.1에서만 여는 작은 화면입니다."""

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def create_server(service, static, port=0):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # 경로·접근 토큰을 로그에 남기지 않습니다.

        def send(self, value, status=200, kind="application/json; charset=utf-8"):
            body = (
                json.dumps(value, ensure_ascii=False).encode()
                if kind.startswith("application/json")
                else value
            )
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self):
            host = f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == host and self.headers.get("Origin") in (
                None,
                "http://" + host,
            )

        def authorized(self):
            value = self.headers.get("X-Filename-Token", "")
            return self.trusted() and hmac.compare_digest(value, token)

        def do_GET(self):
            path = urlsplit(self.path)
            if not self.trusted():
                self.send({"error": "접근할 수 없습니다."}, 403)
                return
            if path.path in ["/", "/app.js", "/style.css"]:
                filename = {
                    "/": "index.html",
                    "/app.js": "app.js",
                    "/style.css": "style.css",
                }[path.path]
                kind = {
                    "/": "text/html; charset=utf-8",
                    "/app.js": "text/javascript; charset=utf-8",
                    "/style.css": "text/css; charset=utf-8",
                }[path.path]
                self.send((Path(static) / filename).read_bytes(), kind=kind)
                return
            if not self.authorized():
                self.send({"error": "앱을 실행할 때 열린 주소로 접속하세요."}, 403)
                return
            try:
                if path.path == "/api/state":
                    self.send(service.snapshot())
                elif path.path == "/api/folders":
                    value = parse_qs(path.query).get("path", [str(Path.home())])[0]
                    directory = Path(value).expanduser().resolve()
                    folders = [
                        {"name": p.name, "path": str(p)}
                        for p in sorted(
                            directory.iterdir(), key=lambda p: p.name.casefold()
                        )
                        if p.is_dir()
                        and not p.name.startswith(".")
                        and not p.is_symlink()
                    ]
                    drives = []
                    if sys.platform == "win32":
                        drives = [
                            f"{c}:\\"
                            for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                            if Path(f"{c}:\\").exists()
                        ]
                    elif sys.platform == "darwin":
                        drives = [
                            str(p) for p in Path("/Volumes").iterdir() if p.is_dir()
                        ]
                    self.send(
                        {
                            "path": str(directory),
                            "parent": str(directory.parent),
                            "folders": folders,
                            "drives": drives,
                        }
                    )
                elif path.path == "/api/preview":
                    identity = parse_qs(path.query).get("id", [""])[0]
                    with service.lock:
                        picture = service.previews.get(identity)
                    if picture:
                        self.send(picture, kind="image/jpeg")
                    else:
                        self.send({"error": "미리보기가 없습니다."}, 404)
                else:
                    self.send({"error": "찾을 수 없습니다."}, 404)
            except (OSError, ValueError) as exc:
                self.send({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.authorized():
                self.send({"error": "접근할 수 없습니다."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("요청 크기가 잘못되었습니다.")
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/analyze":
                    service.analyze(body["folder"], body.get("options"))
                elif self.path == "/api/rename":
                    service.rename(body["items"])
                elif self.path == "/api/undo":
                    service.undo()
                elif self.path == "/api/cancel":
                    service.cancel()
                elif self.path == "/api/quit":
                    # 응답을 먼저 보내야 서버 종료와 응답 전송이 경합하지 않습니다.
                    self.send({"ok": True})
                    service.cancel_event.set()

                    def stop():
                        if service.thread:
                            service.thread.join()
                        self.server.shutdown()

                    threading.Thread(target=stop, daemon=True).start()
                    return
                elif self.path == "/api/config":
                    path = str(service.config_path)
                    if sys.platform == "win32":
                        os.startfile(path)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", "-t", path])
                    else:
                        subprocess.Popen(["xdg-open", path])
                else:
                    raise ValueError("지원하지 않는 요청입니다.")
                self.send({"ok": True})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.send({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_port}/#token={token}"
