import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urljoin, urldefrag
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from core.report_preview import byte_range, preview_server, write_preview_launcher


class ReportPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / "task"
        self.task.mkdir()
        (self.task / "analysis.json").write_text("{}", encoding="utf-8")
        self.video = self.root / "录播 # 1.mp4"
        self.video.write_bytes(bytes(range(256))*40)
        self.report = self.task / "report.html"
        self.report.write_text("<!doctype html><video controls preload='none' src='"+self.video.as_uri()
                               +"#t=3.5,7.0'></video><a href='analysis.json'>分析</a>", encoding="utf-8")
        self.server, self.url = preview_server(self.report)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def media_url(self):
        from html.parser import HTMLParser
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "video": self.source = dict(attrs)["src"]
        parser = Parser()
        with urlopen(self.url) as response:
            parser.feed(response.read().decode("utf-8"))
        return urljoin(self.url, parser.source)

    def test_external_unicode_video_is_rewritten_with_timestamp_fragment(self):
        with urlopen(self.url) as response:
            page = response.read().decode("utf-8")
        self.assertNotIn("file://", page)
        self.assertIn('preload="metadata"', page)
        self.assertTrue(self.media_url().endswith("#t=3.5,7.0"))

    def test_partial_request_streams_exact_bytes_and_head_has_no_body(self):
        url = urldefrag(self.media_url())[0]
        for method in ("GET", "HEAD"):
            with urlopen(Request(url, headers={"Range": "bytes=100-199"}, method=method)) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                self.assertEqual(response.headers["Content-Range"], "bytes 100-199/10240")
                self.assertEqual(response.headers["Content-Length"], "100")
                self.assertEqual(response.read(), self.video.read_bytes()[100:200] if method == "GET" else b"")

    def test_suffix_open_ended_and_invalid_ranges(self):
        for value, expected in (("bytes=-32", self.video.read_bytes()[-32:]),
                                ("bytes=10200-", self.video.read_bytes()[10200:])):
            with urlopen(Request(urldefrag(self.media_url())[0], headers={"Range": value})) as response:
                self.assertEqual(response.read(), expected)
        for value in ("bytes=999999-", "bytes=10-3", "bytes=-0", "bytes=1-2,4-5"):
            with self.subTest(value=value), self.assertRaises(HTTPError) as caught:
                urlopen(Request(urldefrag(self.media_url())[0], headers={"Range": value}))
            self.assertEqual(caught.exception.code, 416)

    def test_unknown_paths_cannot_browse_files_or_read_credentials(self):
        secret = self.task / ".env"
        secret.write_text("PRIVATE=do-not-serve", encoding="utf-8")
        outside = self.root / "private.json"
        outside.write_text("{}", encoding="utf-8")
        for path in ("/.env", "/../private.json", "/asset/unknown"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(urljoin(self.url, path))
            self.assertEqual(caught.exception.code, 404)
        # Even explicit references cannot register credentials or external config files.
        self.report.write_text("<a href='.env'>secret</a><a href='"+outside.as_uri()+"'>private</a>", encoding="utf-8")
        with urlopen(self.url) as response:
            page = response.read().decode("utf-8")
        self.assertNotIn("/asset/", page)

    def test_launcher_preserves_space_and_literal_percent_in_project_path(self):
        root = self.root / "Project 100% done"
        launcher = write_preview_launcher(self.report, root)
        data = launcher.read_bytes()
        self.assertIn(b"100%% done", data)
        self.assertIn(b"--report \"%~dp0report.html\"", data)
        self.assertIn(b"\r\n", data)


if __name__ == "__main__":
    unittest.main()
