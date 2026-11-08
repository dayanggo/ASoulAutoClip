"""Loopback-only Webhook v2 receiver; the request only persists events."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.highlight_selection import SelectionError


def make_server(queue, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, code, payload=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, {"status": "ready"}) if self.path == "/health" else self.reply(404)

        def do_POST(self):
            if self.path != "/bililive":
                return self.reply(404)
            if self.headers.get("Origin") or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self.reply(415, {"error": "仅接受本机录播姬的 application/json 请求"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < size <= 65536:
                    return self.reply(413)
                body = self.rfile.read(size)
                if len(body) != size:
                    return self.reply(400)
                event = json.loads(body)
                queue.receive(event)
            except (ValueError, UnicodeError, SelectionError) as exc:
                return self.reply(400, {"error": str(exc)})
            except Exception:
                # Persisting failed: a non-2xx response lets the recorder retry.
                return self.reply(503, {"error": "事件未保存，请检查服务日志和队列数据库"})
            self.reply(204)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
