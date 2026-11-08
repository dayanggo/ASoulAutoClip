"""Offline audit of existing clips; never alter selection, captions, or videos."""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
if __package__ in (None, ""):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))

import argparse
import html
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import quote

from analyze_recording import load_config, source_identity
from core.highlight_render import DEFAULT_STYLE, caption_display_text, clip_srt, probe_video
from core.caption_layout import layout_captions
from core.highlight_selection import SelectionError, atomic_text, format_time, read_subtitles, write_json
from core.job_lock import job_lock
from core.subtitle_correction import DictionaryCorrector, subtitle_fingerprint


def token_boundary_shift(word, legacy_raw, aligned_raw):
    """Compare the same token in old and repaired CTC data, without guessing."""
    for old, new in zip(legacy_raw, aligned_raw):
        if old["region"] != new["region"]:
            return None
        before, after = old["result"].get("timestamp", []), new["result"].get("timestamp", [])
        if [t[0] for t in before] != [t[0] for t in after]:
            continue
        offset = round(old["region"]["start"] / 16)
        for lo, hi in zip(before, after):
            if lo[0] == word["text"] and abs(offset + round(lo[1] * 1000) - word["start_ms"]) <= 2 and abs(
                    offset + round(lo[2] * 1000) - word["end_ms"]) <= 2:
                return {"text": word["text"], "old_start_ms": word["start_ms"], "old_end_ms": word["end_ms"],
                        "ctc_start_ms": offset + round(hi[1] * 1000), "ctc_end_ms": offset + round(hi[2] * 1000)}
    return None


def inspect_clip(clip, subtitles, current_text, output, legacy_words=None, legacy_raw=None, aligned_raw=None,
                 source=None, word_timing_path=None):
    by_id = {s.id: s for s in subtitles}
    inside = [s for s in subtitles if s.end_ms > clip["start_ms"] and s.start_ms < clip["end_ms"]]
    quotes = [{"subtitle_id": e["subtitle_id"], "quote_matches": e["subtitle_id"] in by_id and
               re.sub(r"\s+", "", e["quote"]) in re.sub(r"\s+", "", by_id[e["subtitle_id"]].text),
               "contained": clip["start_ms"] <= e["start_ms"] < e["end_ms"] <= clip["end_ms"]}
              for e in clip["evidence"]]
    differences = [{"subtitle_id": s.id, "old": s.text, "current": current_text[s.id].text}
                   for s in inside if s.text != current_text[s.id].text]
    gaps = [{"start_ms": a.end_ms, "end_ms": b.start_ms, "seconds": (b.start_ms-a.end_ms)/1000}
            for a, b in zip(inside, inside[1:]) if b.start_ms-a.end_ms >= 3000]
    directory = Path(output) / "clips" / clip["id"]
    checks, error = {}, None
    try:
        media = probe_video(directory / "clip.mp4")
        expected = clip["end_ms"] - clip["start_ms"]
        checks["duration_error_ms"] = media["duration_ms"] - expected
        checks["duration_pass"] = abs(checks["duration_error_ms"]) <= 1000
        receipt = json.loads((directory / "render.json").read_text(encoding="utf-8"))
        expected_captions = subtitles
        if "subtitle_style" in receipt:
            style = dict(DEFAULT_STYLE, **receipt["subtitle_style"])
            displayed, _ = caption_display_text(inside, style["text_replacements"])
            expected_captions, layout = layout_captions(displayed, style, source, word_timing_path)
            checks["caption_mode"] = layout["mode"]
        checks["generated_subtitles_match_display_plan"] = (
            (directory / "captions.generated.srt").read_text(encoding="utf-8").strip() ==
            clip_srt(expected_captions, clip["start_ms"], clip["end_ms"]).strip())
        rendered = receipt["clip"]
        checks["render_interval_matches"] = all(rendered[k] == clip[k] for k in ("id", "start_ms", "end_ms"))
    except (OSError, ValueError, KeyError) as exc:
        error = str(exc)
    shifts = {}
    if legacy_words and legacy_raw and aligned_raw:
        records = legacy_words["segments"]
        first, last = records[clip["start_id"]-1]["words"][0], records[clip["end_id"]-1]["words"][-1]
        shifts = {"first_word": token_boundary_shift(first, legacy_raw, aligned_raw),
                  "last_word": token_boundary_shift(last, legacy_raw, aligned_raw)}
    return {"id": clip["id"], "title": clip.get("publication", {}).get("title", clip["title"]),
            "start_ms": clip["start_ms"], "end_ms": clip["end_ms"], "evidence_checks": quotes,
            "technical_checks": checks, "error": error, "dictionary_differences": differences,
            "no_caption_gaps": gaps, "boundary_ctc_comparison": shifts,
            "context_before": [asdict(s) for s in subtitles if clip["start_id"]-4 <= s.id < clip["start_id"]],
            "subtitles": [asdict(s) for s in inside],
            "context_after": [asdict(s) for s in subtitles if clip["end_id"] < s.id <= clip["end_id"]+8]}


def audit(output, config):
    output = Path(output).resolve()
    with job_lock(output / ".job.lock"):
        result = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
        video = Path(result["source"]["path"])
        if source_identity(video) != result["source"]:
            raise SelectionError("录播与已有分析不匹配")
        duration = probe_video(video)["duration_ms"]
        subtitles = read_subtitles(result["srt"], duration)
        raw_path = Path(result.get("correction", {}).get("raw_srt", result["srt"]))
        raw = read_subtitles(raw_path, duration)
        if [(s.id, s.start_ms, s.end_ms) for s in raw] != [(s.id, s.start_ms, s.end_ms) for s in subtitles]:
            raise SelectionError("原始字幕与分析版时间不一致，需要单独复核")
        corrector = DictionaryCorrector(config["correction"]["dictionary_path"], config["correction"]["ignore_sources"]) if config["correction"]["enabled"] else None
        current = [replace(s, text=corrector.correct_text(s.text)[0]) if corrector else s for s in raw]
        current_text = {s.id: s for s in current}
        words_path = raw_path.with_name("transcript.words.json")
        words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else None
        legacy_raw_path = raw_path.with_name("firered.raw.json")
        legacy_raw = json.loads(legacy_raw_path.read_text(encoding="utf-8")) if legacy_raw_path.exists() else []
        log_path = raw_path.with_name("asr-cuda-error.log")
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        errors = log.count("log_probs must be float64, float32 or float16")
        repaired = []
        for p in sorted((output / "asr").glob("*/receipt.json")):
            meta = json.loads(p.read_text(encoding="utf-8"))
            if meta.get("realigned_from") == str(raw_path.parent) and meta.get("timestamp_policy") == 2:
                repaired = json.loads(p.with_name("firered.raw.json").read_text(encoding="utf-8"))
                break
        clips = [inspect_clip(c, subtitles, current_text, output, words, legacy_raw, repaired,
                              source=result["source"], word_timing_path=words_path) for c in result["clips"]]
        data = {"source": result["source"], "status": "review_required", "clips": clips,
                "transcript_fingerprint": subtitle_fingerprint(subtitles),
                "timestamp_policy": words.get("timestamp_policy") if words else None,
                "ctc_dtype_error_count": errors, "repaired_cache_available": bool(repaired),
                "analyzed_text_matches_current_dictionary": subtitle_fingerprint(subtitles) == subtitle_fingerprint(current),
                "limits": ["结构校验不是整体准确率；没有人工听写真值，不能计算 WER/CER。",
                           "无字幕间隔不一定是静音，不能据此直接删去画面。",
                           "同一词的 CTC 重定位只提供时间复核依据，不证明识别文字正确。"]}
        target = output / "audit"
        notes_path = target / "review_notes.json"
        crosscheck_source_matches = False
        if notes_path.is_file():
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
            crosscheck_source_matches = notes.get("source") == result["source"]
            if notes.get("source") == result["source"] and notes.get("transcript_fingerprint") == data["transcript_fingerprint"]:
                for c in clips:
                    c["editor_review"] = notes.get("clips", {}).get(c["id"])
        write_json(target / "audit.json", data)
        esc = lambda s: html.escape(str(s), quote=True)
        cards = []
        for c in clips:
            def cue_rows(cues):
                return "".join(f"<tr><td>{s['id']}</td><td>{format_time(s['start_ms'])}</td><td>{esc(s['text'])}</td></tr>" for s in cues)
            changes = "".join(f"<li>字幕 {s['subtitle_id']}：{esc(s['old'])} → {esc(s['current'])}</li>" for s in c["dictionary_differences"])
            summary = (f"证据匹配 {sum(e['quote_matches'] for e in c['evidence_checks'])}/{len(c['evidence_checks'])}；"
                       f"对应当前词典尚有 {len(c['dictionary_differences'])} 条差异；"
                       f"无字幕间隔 ≥3 秒共 {len(c['no_caption_gaps'])} 处（不代表静音）。")
            review = c.get("editor_review")
            review_html = (f"<p><b>{esc(review['verdict'])}</b>：{esc(review['finding'])}</p>"
                           f"<p>建议：{esc(review['suggestion'])}</p>") if review else ""
            if review and review.get("frame"):
                frame = Path(review["frame"]).name
                if (target / frame).is_file():
                    review_html += f"<p><a href='{quote(frame)}'><img style='width:100%' src='{quote(frame)}' alt='原录播关键帧'></a></p>"
            cards.append(f"<article><h2>{esc(c['title'])}</h2><p>{format_time(c['start_ms'])} — {format_time(c['end_ms'])}</p>"
                         + review_html +
                         f"<video controls preload='none' src='../clips/{c['id']}/clip.mp4'></video><p>{summary}</p><ul>{changes}</ul>"
                         f"<details><summary>技术检查与 CTC 时间复核</summary><pre>{esc(json.dumps(c['technical_checks'],ensure_ascii=False,indent=2))}\n"
                         f"{esc(json.dumps(c['boundary_ctc_comparison'],ensure_ascii=False,indent=2))}</pre></details>"
                         "<details><summary>片前、片内、片后字幕：检查断句、话题和遗漏反应</summary><table>"
                         + cue_rows(c["context_before"]) + "<tr><th colspan='3'>切片开始</th></tr>"
                         + cue_rows(c["subtitles"]) + "<tr><th colspan='3'>切片结束</th></tr>"
                         + cue_rows(c["context_after"]) + "</table></details></article>")
        crosscheck_html = ""
        crosscheck_path = target / "whisper-crosscheck.json"
        if crosscheck_source_matches and crosscheck_path.is_file():
            crosscheck = json.loads(crosscheck_path.read_text(encoding="utf-8"))
            for item in crosscheck:
                name = item["name"]
                if re.fullmatch(r"[a-z0-9-]+", name):
                    crosscheck_html += (f"<details><summary>Whisper 抽查：{esc(name)}</summary>"
                        f"<p>{esc(''.join(s['text'] for s in item['segments']))}</p>"
                        f"<audio controls preload='none' src='audio/{name}.wav'></audio></details>")
        atomic_text(target / "report.html", "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>切片准确性审查</title>"
            "<style>body{font-family:system-ui;max-width:1050px;margin:30px auto;line-height:1.7}article{border:1px solid #ccc;padding:16px;margin:20px 0}"
            "video{width:100%;max-height:520px}td{padding:8px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}</style>"
            f"<h1>已有切片准确性审查</h1><p>检查 {len(clips)} 条。原始日志出现 {errors} 次 CTC 数据类型错误；"
            f"已找到修复后的对齐缓存：{'是' if repaired else '否'}。原有成片未覆盖。</p>"
            "<p>以下只证明文件、引用和时间信息是否一致，不代表整体语义准确率或人工听音结论。具体编辑判断见项目 docs/current-clip-accuracy-review.md。</p>"
            + "".join(cards) + "<h2>独立模型抽查（非人工听写真值）</h2>" + crosscheck_html + "</html>")
        print(f"已检查 {len(clips)} 条：{target / 'report.html'}；未调用 API、未重编码。")
        return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/local/highlights.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        audit(args.output or cfg["input"]["output_dir"], cfg)
    except (SelectionError, OSError, ValueError, KeyError) as exc:
        print(f"审查失败：{exc}")
        raise SystemExit(1)
