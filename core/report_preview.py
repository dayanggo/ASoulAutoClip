"""Loopback-only report previews with explicit assets and seekable media."""
import html
import mimetypes
import re
import secrets
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from core.paths import PROJECT_ROOT


MEDIA_TYPES = {".mp4", ".webm", ".m4v", ".mov", ".mp3", ".wav", ".m4a", ".ogg",
               ".png", ".jpg", ".jpeg", ".webp", ".gif"}
REPORT_TYPES = {".html", ".json", ".txt", ".srt", ".ass"}


def preview_help():
    return ("<p class='preview-help'>视频无法加载时，请在资源管理器中双击本报告旁的 "
            "<b>open_preview.cmd</b>，用本机浏览器预览。</p>")


def write_preview_launcher(report, project_root=PROJECT_ROOT):
    report = Path(report).resolve()
    project_root = Path(project_root).resolve()
    # Batch files require doubled percent signs in literal paths.
    directory = str(project_root).replace("%", "%%")
    filename = report.name.replace("%", "%%")
    text = ("@echo off\nsetlocal\nchcp 65001 >nul\n"
            f'cd /d "{directory}"\n'
            'if exist ".venv\\Scripts\\python.exe" (\n'
            f'    ".venv\\Scripts\\python.exe" -X utf8 -m scripts.preview_report --report "%~dp0{filename}"\n'
            ') else (\n'
            f'    python -X utf8 -m scripts.preview_report --report "%~dp0{filename}"\n'
            ')\n'
            'if errorlevel 1 pause\n')
    target = report.with_name("open_preview.cmd")
    target.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    return target


def byte_range(value, size):
    if not value:
        return 0, size-1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("Invalid range")
    left, right = match.groups()
    if left:
        start = int(left)
        end = min(int(right), size-1) if right else size-1
        if start > end or start >= size:
            raise ValueError("Unsatisfiable range")
    else:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError("Invalid suffix")
        start, end = max(0, size-suffix), size-1
    return start, end, True


class PreviewSite:
    def __init__(self, report):
        self.report = Path(report).resolve()
        if not self.report.is_file() or self.report.suffix.lower() != ".html":
            raise ValueError("报告不存在或不是 HTML 文件")
        self.root = next((p for p in self.report.parents if (p / "analysis.json").is_file()), self.report.parent)
        self.assets, self.urls = {}, {}
        self.lock = threading.Lock()
        self.entry = self.register(self.report)

    def register(self, path):
        path = Path(path).resolve()
        if not path.is_file() or path.name.startswith("."):
            return None
        suffix = path.suffix.lower()
        if suffix not in MEDIA_TYPES and not (suffix in REPORT_TYPES and path.is_relative_to(self.root)):
            return None
        if path.name in {"cookies.txt", "secrets.json"} or path.name.endswith((".local.json", ".secrets.json", ".cookies.json")):
            return None
        with self.lock:
            if path not in self.urls:
                route = "/asset/" + secrets.token_urlsafe(18)
                self.urls[path], self.assets[route] = route, path
            return self.urls[path]

    def rewrite_url(self, value, page):
        parts = urlsplit(value)
        if parts.scheme and parts.scheme != "file" or not parts.path:
            return value
        if parts.scheme == "file":
            if parts.netloc not in {"", "localhost"}:
                return value
            candidate = Path(url2pathname(parts.path))
        else:
            candidate = page.parent / unquote(parts.path)
        route = self.register(candidate)
        if route is None:
            return value
        return route + ("#"+parts.fragment if parts.fragment else "")

    def render(self, page):
        site = self

        class Rewrite(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.parts = []

            def handle_starttag(self, tag, attrs):
                fixed = []
                for name, value in attrs:
                    if value is not None and name in {"src", "href", "poster"}:
                        value = site.rewrite_url(value, page)
                    if tag == "video" and name == "preload":
                        value = "metadata"
                    fixed.append(name if value is None else name+'="'+html.escape(value, quote=True)+'"')
                self.parts.append("<"+tag+(" "+" ".join(fixed) if fixed else "")+">")

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)

            def handle_endtag(self, tag): self.parts.append("</"+tag+">")
            def handle_data(self, data): self.parts.append(data)
            def handle_entityref(self, name): self.parts.append("&"+name+";")
            def handle_charref(self, name): self.parts.append("&#"+name+";")
            def handle_decl(self, decl): self.parts.append("<!"+decl+">")
            def handle_comment(self, data): self.parts.append("<!--"+data+"-->")

        parser = Rewrite()
        parser.feed(page.read_text(encoding="utf-8-sig"))
        return "".join(parser.parts).encode("utf-8")


def preview_server(report, port=0):
    site = PreviewSite(report)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self): self.send_asset(head=True)
        def do_GET(self): self.send_asset()

        def send_asset(self, head=False):
            path = site.assets.get(urlsplit(self.path).path)
            if path is None or not path.is_file():
                self.send_error(404)
                return
            if path.suffix.lower() == ".html":
                data = site.render(path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head: self.wfile.write(data)
                return
            size = path.stat().st_size
            try:
                start, end, partial = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            content_type = "video/mp4" if path.suffix.lower() == ".mp4" else mimetypes.guess_type(path.name)[0]
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, end-start+1)))
            self.send_header("Cache-Control", "private, max-age=0")
            if partial: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head: return
            try:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end-start+1
                    while remaining > 0:
                        chunk = stream.read(min(256*1024, remaining))
                        if not chunk: break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_address[1]}{site.entry}"
