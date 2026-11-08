"""Open a local report and its media through a loopback-only preview server."""
import argparse
import webbrowser
from pathlib import Path

from core.report_preview import preview_server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0, help="本机端口，默认自动选择")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        server, url = preview_server(args.report, args.port)
    except (OSError, ValueError) as exc:
        print(f"无法打开预览：{exc}")
        return 1
    print(f"本机预览：{url}", flush=True)
    print("视频从本机读取，不上传。保留此窗口即可预览；按 Ctrl+C 结束。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
