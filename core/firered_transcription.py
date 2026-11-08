"""FireRed AED adapter: bounded speech regions, original-time word timestamps."""

import gc
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from core.highlight_selection import SelectionError, atomic_text, fingerprint, format_time, write_json
from core.paths import PROJECT_ROOT


ROOT = PROJECT_ROOT
REQUIRED_FILES = ("model.pth.tar", "cmvn.ark", "dict.txt", "train_bpe1000.model")
TIMESTAMP_POLICY = 2
REGION_POLICY = 1


class AlignmentError(SelectionError):
    asr_failure_kind = "alignment"


def strict_timestamps(adapter, hyp, hyp_ids, duration):
    """Reject upstream uniform fallback and preserve gaps between CTC tokens."""
    aligned = hyp.get("timestamp")
    if aligned is None:
        raise AlignmentError("FireRed CTC 对齐失败；拒绝把语音时长平均分给文字")
    ids = [i for i in hyp_ids if i != 0]  # CTC removes blank IDs before alignment.
    starts, ends = aligned
    if len(starts) != len(ids) or len(ends) != len(ids):
        raise AlignmentError("FireRed CTC 对齐长度与文字不一致")
    timestamp = []
    for tid, start, end in zip(ids, starts, ends):
        if not math.isfinite(start) or not math.isfinite(end):
            raise AlignmentError("FireRed CTC 对齐存在非有限时间")
        # Match the official encoder's 60 ms shift, without bridging real pauses.
        start, end = max(0, start - .06), min(duration, end - .06)
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise AlignmentError("FireRed CTC 对齐存在无效或零长度词，未生成估计时间")
        token = adapter.tokenizer.detokenize([tid], "", False).lower()
        timestamp.append([token, round(start, 3), round(end, 3)])
    return adapter.tokenizer.merge_spm_timestamp(timestamp)


def install_strict_timestamps(adapter):
    # torchaudio forced_align rejects bfloat16. Keep the large model in BF16,
    # but convert the small CTC output to float32 before alignment.
    adapter.model.ctc.register_forward_hook(lambda module, inputs, output: output.float())
    adapter._get_and_fix_timestamp = lambda hyp, ids, dur: strict_timestamps(adapter, hyp, ids, dur)


def worker_python():
    return ROOT / ".venv-firered" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def model_directory(config):
    return (ROOT / Path(config["model"]).expanduser()).resolve()


def join_tokens(words):
    text = ""
    for word in words:
        token = word["text"]
        if text and token and text[-1].isascii() and text[-1].isalnum() and token[0].isascii() and token[0].isalnum():
            text += " "
        text += token
    return text


def map_words(result, offset_samples, end_samples, sample_rate=16000):
    """Keep VAD gaps in the original timeline; never infer timestamps from text."""
    timestamps = result.get("timestamp")
    if not timestamps:
        raise SelectionError("FireRed 未返回词时间戳，不能生成可追溯字幕")
    offset_ms = round(offset_samples * 1000 / sample_rate)
    limit_ms = round(end_samples * 1000 / sample_rate)
    words = []
    last_start = offset_ms
    for token, start, end in timestamps:
        if not isinstance(token, str) or not token.strip():
            continue
        if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (start, end)):
            raise SelectionError("FireRed 返回了无效的词时间戳")
        start_ms = max(offset_ms, offset_ms + round(start * 1000))
        end_ms = min(limit_ms, offset_ms + round(end * 1000))
        if start_ms < last_start or end_ms <= start_ms:
            raise SelectionError("FireRed 词时间戳逆序或为空，请检查该语音段")
        last_start = start_ms
        words.append({"start_ms": start_ms, "end_ms": end_ms, "text": token.strip()})
    if not words:
        raise SelectionError("FireRed 未返回有效的词时间戳")
    return words


def subtitle_records(words):
    records, current = [], []
    for word in words:
        if current and (word["start_ms"] - current[-1]["end_ms"] >= 450
                        or word["end_ms"] - current[0]["start_ms"] > 7000
                        or len(join_tokens(current + [word])) > 28):
            records.append({"start_ms": current[0]["start_ms"], "end_ms": max(w["end_ms"] for w in current),
                            "text": join_tokens(current), "words": current})
            current = []
        current.append(word)
    if current:
        records.append({"start_ms": current[0]["start_ms"], "end_ms": max(w["end_ms"] for w in current),
                        "text": join_tokens(current), "words": current})
    return records


def recognize_region(adapter, uttid, sample_rate, samples, depth=0):
    """Retry only strict alignment failures; never use upstream uniform timing."""
    def once(name, audio):
        results = adapter.transcribe([name], [(sample_rate, audio)])
        if not results or len(results) != 1:
            raise SelectionError(f"FireRed {name} 未返回单段识别结果")
        return results[0]

    try:
        return once(uttid, samples)
    except AlignmentError as initial:
        print(f"FireRed：{uttid} 对齐异常，正在局部重识别……", flush=True)
        if getattr(adapter.config, "use_gpu", False) and getattr(adapter.config, "use_half", False):
            import torch
            try:
                adapter.model.float()
                adapter.config.use_half = False
                item = once(uttid+"-fp32", samples)
                item["alignment_recovery"] = {"policy": REGION_POLICY, "method": "fp32"}
                return item
            except AlignmentError:
                pass
            except torch.OutOfMemoryError:
                # Restore the normal precision before trying smaller regions.
                pass
            finally:
                adapter.model.bfloat16()
                adapter.config.use_half = True
                torch.cuda.empty_cache()
        if depth >= 3 or len(samples) < sample_rate*2:
            raise AlignmentError(f"FireRed {uttid} 局部重试后仍无法真实对齐；已保存其他分段，未生成估计时间") from initial
        import numpy as np
        hop = max(1, sample_rate//50)
        options = range(max(sample_rate//2, len(samples)//3), min(len(samples)-sample_rate//2, len(samples)*2//3), hop)
        middle = min(options, key=lambda i: float(np.square(samples[i:i+hop].astype(np.float32)).mean()))
        parts = [recognize_region(adapter, uttid+"-left", sample_rate, samples[:middle], depth+1),
                 recognize_region(adapter, uttid+"-right", sample_rate, samples[middle:], depth+1)]
        stamps = []
        for part, offset in zip(parts, (0, middle/sample_rate)):
            if part.get("text", "").strip():
                if not part.get("timestamp"):
                    raise AlignmentError("FireRed 局部重识别有文字但缺少真实对齐时间")
                stamps.extend([[token, round(start+offset, 3), round(end+offset, 3)]
                               for token, start, end in part["timestamp"]])
        return {"uttid": uttid, "text": " ".join(p.get("text", "") for p in parts), "timestamp": stamps,
                "alignment_recovery": {"policy": REGION_POLICY, "method": "low_energy_split",
                                       "split_sample": middle, "children": [p.get("alignment_recovery") for p in parts]}}


def region_checkpoint_identity(video, config):
    stat = video.stat()
    return fingerprint({"region_policy": REGION_POLICY, "timestamp_policy": TIMESTAMP_POLICY,
                        "source": {"path": str(video.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                        "runtime": config})


def read_region_checkpoint(path, identity, region, pcm_hash, sample_rate):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        item = record["result"]
        if (record.get("identity") != identity or record.get("region") != region
                or record.get("pcm_sha256") != pcm_hash or not isinstance(item, dict)
                or not isinstance(item.get("text"), str) or record.get("result_fingerprint") != fingerprint(item)):
            return None
        if item["text"].strip():
            map_words(item, region["start"], region["end"], sample_rate)
        return item
    except (OSError, ValueError, KeyError, TypeError):
        return None


def transcribe_firered(video, config, folder):
    import numpy as np
    import soundfile as sf
    import torch
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    source = ROOT / "third_party" / "FireRedASR2S"
    sys.path.insert(0, str(source))
    from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config

    if config["device"] == "cuda" and not torch.cuda.is_available():
        raise SelectionError("FireRed 的 CUDA 环境不可用，请运行 setup_firered.cmd")
    half = config["compute_type"] == "bfloat16"
    if half and (config["device"] != "cuda" or not torch.cuda.is_bf16_supported()):
        raise SelectionError("当前设备不支持 FireRed bfloat16，请改为 float32")
    wav = folder / "audio.16k.wav"
    temporary = folder / "audio.16k.tmp.wav"
    print("FireRed：提取 16 kHz 单声道音频……", flush=True)
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(temporary)],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if result.returncode:
        raise SelectionError("FireRed 音频提取失败，请检查视频音轨和 FFmpeg")
    temporary.replace(wav)
    print("FireRed：检测人声并划分识别段……", flush=True)
    audio, sample_rate = sf.read(wav, dtype="float32")
    if sample_rate != 16000 or audio.ndim != 1:
        raise SelectionError("FireRed 音频采样格式错误")
    regions = get_speech_timestamps(audio, VadOptions(threshold=0.4, min_speech_duration_ms=100,
        min_silence_duration_ms=500, speech_pad_ms=300, max_speech_duration_s=config.get("chunk_seconds", 25)))
    del audio
    gc.collect()
    if not regions:
        raise SelectionError("未检测到有效人声，未生成空字幕缓存")
    write_json(folder / "speech_regions.json", {"sample_rate": sample_rate, "regions": regions})
    print(f"FireRed：加载本地模型，共 {len(regions)} 个语音段（{config['device']} / {config['compute_type']}）……", flush=True)
    model = FireRedAsr2.from_pretrained("aed", str(model_directory(config)), FireRedAsr2Config(
        use_gpu=config["device"] == "cuda", use_half=half, beam_size=config.get("beam_size", 3), return_timestamp=True))
    install_strict_timestamps(model)
    checkpoint_identity = region_checkpoint_identity(video, config)
    checkpoint_dir = folder / f"regions-v{REGION_POLICY}" / checkpoint_identity[:16]
    records, raw = [], []
    with sf.SoundFile(wav) as stream, torch.inference_mode():
        for index, region in enumerate(regions, 1):
            stream.seek(region["start"])
            samples = stream.read(region["end"] - region["start"], dtype="int16")
            if len(samples) < 400:
                continue
            checkpoint = checkpoint_dir / f"{index:06d}.json"
            pcm_hash = hashlib.sha256(samples.tobytes()).hexdigest()
            item = read_region_checkpoint(checkpoint, checkpoint_identity, region, pcm_hash, sample_rate)
            cached = item is not None
            if item is None:
                item = recognize_region(model, f"segment-{index}", sample_rate, np.asarray(samples))
            item["timestamp_source"] = "ctc_forced_alignment"
            if item.get("text", "").strip():
                words = map_words(item, region["start"], region["end"], sample_rate)
                records.extend(subtitle_records(words))
            if not cached:
                write_json(checkpoint, {"identity": checkpoint_identity, "region": region, "pcm_sha256": pcm_hash,
                                        "result": item, "result_fingerprint": fingerprint(item)})
            raw.append({"region": region, "result": item})
            suffix = "（复用分段缓存）" if cached else ""
            print(f"FireRed 转写进度：{index}/{len(regions)}，视频时间 {format_time(round(region['end'] * 1000 / sample_rate))}{suffix}", flush=True)
    if not records:
        raise SelectionError("未识别到有效语音，未生成空字幕缓存")
    write_json(folder / "firered.raw.json", raw)
    write_json(folder / "transcript.words.json", {"language": config["language"], "runtime": config,
                                                "timestamp_policy": TIMESTAMP_POLICY,
                                                "region_policy": REGION_POLICY,
                                                "timestamp_source": "ctc_forced_alignment", "segments": records})
    blocks = [f"{i}\n{format_time(r['start_ms'], ',')} --> {format_time(r['end_ms'], ',')}\n{r['text']}\n"
              for i, r in enumerate(records, 1)]
    atomic_text(folder / "transcript.srt", "\n".join(blocks))
    if config["device"] == "cuda":
        print(f"FireRed 完成，峰值 GPU 张量显存：{torch.cuda.max_memory_allocated() / 1024**3:.2f} GiB", flush=True)
