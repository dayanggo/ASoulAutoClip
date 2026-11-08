"""Realign cached FireRed text with CTC; keep recognition and old files intact."""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
if __package__ in (None, ""):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))

import argparse
import json
import re
import sys
from pathlib import Path

from core.firered_transcription import (ROOT, TIMESTAMP_POLICY, install_strict_timestamps, map_words,
                                        model_directory, strict_timestamps, subtitle_records)
from core.highlight_selection import SelectionError, atomic_text, fingerprint, format_time, write_json
from core.job_lock import job_lock
from core.local_transcription import asr_cache_key
from core.paths import resolve_config_path


def realign(config_path, old_folder):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    config = json.loads(resolve_config_path(config_path).read_text(encoding="utf-8-sig"))["asr"]
    if config["backend"] != "firered":
        raise SelectionError("此工具只用于 FireRed 原始识别缓存")
    old_folder = Path(old_folder).resolve()
    receipt = json.loads((old_folder / "receipt.json").read_text(encoding="utf-8"))
    if config != receipt["asr"]:
        raise SelectionError("当前 ASR 配置与待对齐缓存不一致")
    source = receipt["source"]
    video = Path(source["path"])
    if not video.is_file() or source["size"] != video.stat().st_size or source["mtime_ns"] != video.stat().st_mtime_ns:
        raise SelectionError("缓存录播身份与当前文件不一致")
    output = old_folder.parent.parent
    folder = old_folder.parent / asr_cache_key(source, config)[:16]
    if folder == old_folder:
        raise SelectionError("目标必须与原缓存不同")
    folder.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "third_party/FireRedASR2S"))
    from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config
    use_gpu = config["device"] != "cpu" and torch.cuda.is_available()
    if config["device"] == "cuda" and not use_gpu:
        raise SelectionError("当前无法使用 CUDA")
    use_half = use_gpu and torch.cuda.is_bf16_supported() and config["compute_type"] != "float32"
    runtime = dict(config, device="cuda" if use_gpu else "cpu",
                   compute_type="bfloat16" if use_half and torch.cuda.is_bf16_supported() else "float32")
    raw = json.loads((old_folder / "firered.raw.json").read_text(encoding="utf-8"))
    with job_lock(output / ".job.lock"):
        adapter = FireRedAsr2.from_pretrained("aed", str(model_directory(config)), FireRedAsr2Config(
            use_gpu=use_gpu, use_half=use_half, return_timestamp=True))
        install_strict_timestamps(adapter)
        tensor_dtype = next(adapter.model.parameters()).dtype
        device = next(adapter.model.parameters()).device
        records, aligned_raw, failures = [], [], []
        stream = sf.SoundFile(old_folder / "audio.16k.wav")
        with stream, torch.inference_mode():
            for index, cached in enumerate(raw, 1):
                region, item = cached["region"], cached["result"]
                if not item.get("text", "").strip():
                    aligned_raw.append(cached)
                    continue
                try:
                    # Per-region successful results survive an interruption.
                    key = fingerprint({"policy": TIMESTAMP_POLICY, "source": source, "region": region,
                                       "text": item["text"], "runtime": runtime})
                    cached_alignment = folder / "alignment" / f"{key}.json"
                    if cached_alignment.is_file():
                        fixed = json.loads(cached_alignment.read_text(encoding="utf-8"))
                        if fixed.get("text") != item["text"] or fixed.get("timestamp_source") != "ctc_forced_alignment":
                            raise SelectionError("已缓存的对齐记录与原文或时间来源不符")
                    else:
                        ids = []
                        for piece in re.split(r"(<unk>)", item["text"]):
                            if piece == "<unk>":
                                ids.append(adapter.tokenizer.dict["<unk>"])
                            elif piece:
                                ids.extend(adapter.tokenizer.tokenize(piece, replace_punc=False)[1])
                        ids = [i for i in ids if i != 0]
                        reconstructed = adapter.tokenizer.detokenize(ids).lower()
                        if "".join(reconstructed.split()) != "".join(item["text"].lower().split()):
                            raise SelectionError("原始文字不能无损还原为 CTC token，未擅自修改识别结果")
                        stream.seek(region["start"])
                        samples = stream.read(region["end"] - region["start"], dtype="int16")
                        feats, lengths, durs, _, _ = adapter.feat_extractor([(16000, np.asarray(samples))], [str(index)])
                        enc, enc_lengths, _ = adapter.model.encoder(feats.to(device=device, dtype=tensor_dtype), lengths.to(device))
                        logits = adapter.model.ctc(enc)[0, :int(enc_lengths[0])].unsqueeze(0).float()
                        target = torch.tensor([ids], dtype=torch.int64, device=device)
                        path, _ = torchaudio.functional.forced_align(logits, target, blank=0)
                        starts, ends = adapter.model.ctc.ctc_alignment_to_timestamp(path[0].cpu().tolist(),
                            adapter.model.encoder.input_preprocessor.subsampling, blank_id=0)
                        timestamps = strict_timestamps(adapter, {"timestamp": (starts, ends)}, ids, durs[0])
                        fixed = dict(item, timestamp=timestamps, timestamp_source="ctc_forced_alignment")
                        write_json(cached_alignment, fixed)
                    words = map_words(fixed, region["start"], region["end"])
                    records.extend(subtitle_records(words))
                    aligned_raw.append({"region": region, "result": fixed})
                except Exception as exc:
                    failures.append({"region_index": index, "reason": str(exc)})
                    print(f"对齐失败 {index}: {exc}", flush=True)
                if index % 20 == 0 or index == len(raw):
                    print(f"CTC 重新对齐：{index}/{len(raw)}，失败 {len(failures)}", flush=True)
        write_json(folder / "alignment.failures.json", failures)
        if failures:
            raise SelectionError(f"有 {len(failures)} 段未能对齐，未写入完成凭据；成功段保留，可修复后重跑")
        write_json(folder / "firered.raw.json", aligned_raw)
        write_json(folder / "speech_regions.json", json.loads((old_folder / "speech_regions.json").read_text(encoding="utf-8")))
        write_json(folder / "transcript.words.json", {"language": config["language"], "runtime": runtime,
            "timestamp_policy": TIMESTAMP_POLICY, "timestamp_source": "ctc_forced_alignment", "segments": records})
        blocks = [f"{i}\n{format_time(r['start_ms'], ',')} --> {format_time(r['end_ms'], ',')}\n{r['text']}\n"
                  for i, r in enumerate(records, 1)]
        text = "\n".join(blocks)
        atomic_text(folder / "transcript.srt", text)
        write_json(folder / "receipt.json", {"source": source, "asr": config, "runtime": runtime,
            "timestamp_policy": TIMESTAMP_POLICY, "srt_fingerprint": fingerprint(text),
            "realigned_from": str(old_folder), "recognition_changed": False})
        print(f"重新对齐完成：{folder}；保留原识别文字，共 {len(records)} 条字幕。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/local/highlights.json")
    parser.add_argument("--asr-cache", required=True)
    args = parser.parse_args()
    try:
        realign(args.config, args.asr_cache)
    except (SelectionError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
