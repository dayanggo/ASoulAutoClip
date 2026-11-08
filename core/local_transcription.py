"""Run Whisper in a worker so CUDA/DLL failures do not kill the main job."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from core.highlight_selection import SelectionError, atomic_text, fingerprint, format_time, write_json


def transcribe_once(video, config, folder):
    """Worker implementation; also injectable in tests without model downloads."""
    from faster_whisper import WhisperModel

    print(f"加载转写模型：{config['model']}（首次使用会下载权重）", flush=True)
    model = WhisperModel(config["model"], device=config["device"], compute_type=config["compute_type"])
    segments, info = model.transcribe(str(video), language=config["language"], vad_filter=True,
                                      word_timestamps=True, initial_prompt=config.get("initial_prompt", ""))
    blocks, records = [], []
    for segment in segments:
        text = segment.text.strip()
        start_ms, end_ms = round(segment.start * 1000), round(segment.end * 1000)
        if not text or end_ms <= start_ms:
            continue
        blocks.append(f"{len(blocks) + 1}\n{format_time(start_ms, ',')} --> {format_time(end_ms, ',')}\n{text}\n")
        records.append({"start_ms": start_ms, "end_ms": end_ms, "text": text,
                        "words": [{"start_ms": round(word.start * 1000), "end_ms": round(word.end * 1000),
                                   "text": word.word} for word in (segment.words or [])]})
        print(f"\r转写进度：{format_time(end_ms)}", end="", flush=True)
    print()
    if not records:
        raise SelectionError("未识别到有效语音，未生成空字幕缓存")
    atomic_text(folder / "transcript.srt", "\n".join(blocks))
    write_json(folder / "transcript.words.json", {"language": info.language, "runtime": config, "segments": records})


def run_worker(video, config, folder):
    failure = folder / "worker-failure.json"
    failure.unlink(missing_ok=True)
    config_path = folder / "worker-config.json"
    write_json(config_path, config)
    environment = os.environ.copy()
    environment.setdefault("HF_HUB_DISABLE_XET", "1")
    with (folder / f"asr-{config['device']}-error.log").open("w", encoding="utf-8") as errors:
        executable = sys.executable
        if config.get("backend", "faster_whisper") == "firered":
            from core.firered_transcription import worker_python
            executable = str(worker_python())
        process = subprocess.Popen(
            [executable, "-X", "utf8", "-u", "-m", "core.asr_worker", "--video", str(video),
             "--config", str(config_path), "--output", str(folder)],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=errors, env=environment,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            for line in process.stdout:
                print(line.rstrip("\r\n"), flush=True)
            returncode = process.wait()
        except BaseException:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            process.wait()
            raise
        finally:
            process.stdout.close()
    if returncode:
        if config.get("backend") == "firered" and failure.is_file():
            try:
                details = json.loads(failure.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                details = {}
            if details.get("kind") == "alignment":
                from core.firered_transcription import AlignmentError
                raise AlignmentError(details["reason"])
        raise SelectionError(f"转写子进程退出码 {returncode}，详情：{folder / ('asr-' + config['device'] + '-error.log')}")


def asr_cache_key(identity, config):
    return fingerprint({"version": 4 if config.get("backend") == "firered" else 3,
                        "source": identity, "asr": config})


def transcribe(video, identity, config, output):
    key = asr_cache_key(identity, config)
    folder = Path(output) / "asr" / key[:16]
    target = folder / "transcript.srt"
    receipt = folder / "receipt.json"
    if target.exists() and receipt.exists():
        try:
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            if metadata.get("srt_fingerprint") == fingerprint(target.read_text(encoding="utf-8")):
                print(f"复用已完成的本地转写（{metadata.get('runtime', {}).get('device', 'cached')}）")
                return target
        except (ValueError, OSError):
            pass
    firered = config.get("backend", "faster_whisper") == "firered"
    if firered:
        from core.firered_transcription import worker_python
        if not worker_python().is_file():
            raise SelectionError("FireRed 独立环境未安装，请运行 setup_firered.cmd")
    elif importlib.util.find_spec("faster_whisper") is None:
        raise SelectionError("本地转写依赖未安装，请使用 run_clips.cmd 或安装 requirements-asr.txt")
    folder.mkdir(parents=True, exist_ok=True)
    requested_device = config["device"]
    devices = ["cuda", "cpu"] if requested_device == "auto" else [requested_device]
    for index, device in enumerate(devices):
        runtime = dict(config, device=device)
        if config["compute_type"] == "auto" or (requested_device == "auto" and device == "cpu"):
            runtime["compute_type"] = ("bfloat16" if device == "cuda" else "float32") if firered else ("int8_float16" if device == "cuda" else "int8")
        backend = "FireRed" if firered else "Whisper"
        print(f"开始本地转写：{backend} / {runtime['device']} / {runtime['compute_type']}。", flush=True)
        try:
            run_worker(Path(video).resolve(), runtime, folder.resolve())
            content = target.read_text(encoding="utf-8")
            if not content.strip():
                raise SelectionError("转写结果为空")
            write_json(receipt, {"source": identity, "asr": config, "runtime": runtime,
                                 "timestamp_policy": 2 if firered else None,
                                 "srt_fingerprint": fingerprint(content)})
            return target
        except (SelectionError, OSError) as exc:
            if firered:
                from core.firered_transcription import AlignmentError
                if isinstance(exc, AlignmentError):
                    raise SelectionError(f"本地转写尚未完成：{exc}。已保存逐段缓存；未退回 CPU 从头重跑") from None
            if index + 1 < len(devices):
                print(f"GPU 转写未完成，切换 CPU 重试；GPU 详情：{folder / 'asr-cuda-error.log'}", flush=True)
                continue
            raise SelectionError(f"本地转写未完成：{exc}") from None
