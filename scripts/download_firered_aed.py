"""Download official FireRedASR2-AED files with resume and SHA-256 checks."""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
if __package__ in (None, ""):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))

import argparse
import hashlib
import json
import time
from pathlib import Path

import requests


MODEL = "FireRedTeam/FireRedASR2-AED"
WEIGHT_SHA256 = "4677cbd30988d63ed3e777f6a42a1e5260a3865317f6e15e488bef40954f7054"
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def download_file(session, item, output):
    name, size, expected = item["Path"], item["Size"], item["Sha256"]
    if Path(name).name != name or name in {".", ".."}:
        raise RuntimeError("Unexpected filename in official repository metadata")
    target = output / name
    partial = output / (name + ".part")
    if target.exists():
        if target.stat().st_size == size and digest(target) == expected:
            print(f"已校验，跳过：{name}", flush=True)
            return
        raise RuntimeError(f"Existing file does not match official SHA-256: {name}")
    if size == 0:
        partial.write_bytes(b"")
    else:
        for attempt in range(6):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset == size:
                break
            if offset > size:
                raise RuntimeError(f"Partial file exceeds expected size: {name}")
            started = last_print = time.monotonic()
            print(f"下载 {name}：{offset / 1e6:.1f} / {size / 1e6:.1f} MB", flush=True)
            try:
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with session.get(
                    f"https://modelscope.cn/api/v1/models/{MODEL}/repo",
                    params={"Revision": item["Revision"], "FilePath": name},
                    headers=headers, stream=True, timeout=(15, 60),
                ) as response:
                    response.raise_for_status()
                    if offset:
                        expected_range = f"bytes {offset}-"
                        if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(expected_range):
                            raise RuntimeError("Server did not honor resume range")
                    written = offset
                    with partial.open("ab" if offset else "wb") as stream:
                        for block in response.iter_content(1024 * 1024):
                            if not block:
                                continue
                            if written + len(block) > size:
                                raise RuntimeError("Download exceeds official file size")
                            stream.write(block)
                            written += len(block)
                            now = time.monotonic()
                            if now - last_print >= 10:
                                speed = (written - offset) / max(now - started, 0.001) / 1e6
                                print(f"  {written / 1e6:.1f} / {size / 1e6:.1f} MB ({written / size:.1%})，{speed:.2f} MB/s", flush=True)
                                last_print = now
                if partial.stat().st_size == size:
                    break
                raise requests.ConnectionError("Incomplete response body")
            except requests.RequestException as exc:
                if attempt == 5:
                    raise RuntimeError(f"Download interrupted ({type(exc).__name__}); rerun to resume {name}") from None
                print(f"连接中断（{type(exc).__name__}），保留已下载内容并重试。", flush=True)
                time.sleep(min(2 ** attempt, 10))
    print(f"校验 SHA-256：{name}", flush=True)
    if partial.stat().st_size != size or digest(partial) != expected:
        raise RuntimeError(f"Integrity check failed; partial file retained: {name}")
    partial.replace(target)
    print(f"完成：{name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models" / "FireRedASR2-AED")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        # Use the official domestic host directly; do not alter system proxy settings.
        session.trust_env = False
        response = session.get(
            f"https://modelscope.cn/api/v1/models/{MODEL}/repo/files",
            params={"Revision": "master", "Recursive": "true"}, timeout=(15, 60),
        )
        response.raise_for_status()
        metadata = response.json()
        if metadata.get("Code") != 200:
            raise RuntimeError("Official repository metadata request failed")
        files = [item for item in metadata["Data"]["Files"] if item["Type"] == "blob"]
        weights = next(item for item in files if item["Path"] == "model.pth.tar")
        if weights["Sha256"] != WEIGHT_SHA256:
            raise RuntimeError("Weight revision differs from the reviewed official Hugging Face SHA-256")
        print(f"模型：{MODEL}\n目录：{output}\n共 {len(files)} 个文件，{sum(item['Size'] for item in files) / 1e9:.3f} GB", flush=True)
        manifest = {"model": MODEL, "source": f"https://modelscope.cn/models/{MODEL}",
                    "huggingface": f"https://huggingface.co/{MODEL}",
                    "huggingface_revision": "2304afed56eacfee6256dee5937ed22ffa0b64ec",
                    "files": [{"path": item["Path"], "bytes": item["Size"], "sha256": item["Sha256"],
                               "revision": item["Revision"]} for item in files]}
        for item in files:
            download_file(session, item, output)
        receipt = output / "download.complete.json"
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(dict(manifest, status="verified"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(receipt)
        print(f"全部下载及完整性校验通过：{output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"下载未完成：{type(exc).__name__}；已下载内容保留，重新运行可续传。", flush=True)
        raise SystemExit(1)
