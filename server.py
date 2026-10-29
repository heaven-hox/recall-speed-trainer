"""A loopback-only UI and import endpoint. Learning data stays in the browser."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from importer import MAX_UPLOAD, ImportError, import_apkg, import_csv

ROOT = Path(__file__).resolve().parent / "web"
FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
         "/core.js": ("core.js", "text/javascript"), "/style.css": ("style.css", "text/css")}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content, mime="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def json(self, status, value):
        self.reply(status, json.dumps(value, ensure_ascii=False).encode())

    def allowed(self):
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return host in expected and (origin is None or origin == "http://" + host)

    def do_GET(self):
        if not self.allowed():
            return self.json(403, {"error": "ローカルからのみ利用できます。"})
        path = urlsplit(self.path).path
        if path not in FILES:
            return self.json(404, {"error": "Not found"})
        filename, mime = FILES[path]
        self.reply(200, (ROOT / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.allowed():
            return self.json(403, {"error": "別のサイトからの取り込みは許可していません。"})
        url = urlsplit(self.path)
        if url.path != "/api/import":
            return self.json(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD:
                return self.json(413, {"error": "ファイルは空でない50MiB以下のものを選択してください。"})
            filename = parse_qs(url.query).get("name", [""])[0]
            content = self.rfile.read(length)
            if len(content) != length:
                raise ImportError("アップロードが途中で切れました。")
            if filename.lower().endswith(".apkg"):
                result = import_apkg(content)
            elif filename.lower().endswith((".csv", ".tsv", ".txt")):
                result = import_csv(content, filename)
            else:
                raise ImportError(".apkg / .csv / .tsv / .txt を選択してください。")
            self.json(200, result)
        except (ImportError, ValueError) as exc:
            self.json(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="想起速度トレーナー（ローカルWeb UI）")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"想起速度トレーナー: http://127.0.0.1:{server.server_port} （終了: Ctrl+C）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
