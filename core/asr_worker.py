import argparse
import json
import sys
from pathlib import Path

from core.local_transcription import transcribe_once
from core.highlight_selection import write_json


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if config.get("backend", "faster_whisper") == "firered":
            from core.firered_transcription import transcribe_firered
            transcribe_firered(Path(args.video), config, Path(args.output))
        else:
            transcribe_once(Path(args.video), config, Path(args.output))
        return 0
    except Exception as exc:
        write_json(Path(args.output) / "worker-failure.json", {
            "kind": getattr(exc, "asr_failure_kind", "runtime"), "reason": str(exc)})
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
