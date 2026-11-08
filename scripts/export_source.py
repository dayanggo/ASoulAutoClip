"""Export checked source files; exclude Git history, credentials and runtime data."""
import argparse
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.check_repository import ROOT, candidate_files, inspect_repository


def export_source(output):
    files = candidate_files(ROOT)
    result = inspect_repository(ROOT, files)
    if result["issues"]:
        raise ValueError("仓库检查未通过，请先运行 python -m scripts.check_repository")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".source-", dir=output.parent) as temporary:
        target = Path(temporary) / "source.zip"
        with ZipFile(target, "w", ZIP_DEFLATED) as archive:
            for relative in files:
                archive.write(ROOT / relative, "ASoulAutoClip/" + relative.as_posix())
        os.replace(target, output)
    return output, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "workspace/releases/ASoulAutoClip-source.zip")
    args = parser.parse_args()
    try:
        output, count = export_source(args.output)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 1
    print(f"已导出 {count} 个源码文件：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
