"""Render selected intervals without the legacy padding/cleanup side effects."""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from core.highlight_selection import SelectionError, atomic_text, fingerprint, format_time, write_json
from core.subtitle_utils import SubtitleUtils
from core.caption_layout import layout_captions
from core.subtitle_style import DEFAULT_SUBTITLE_STYLE
from core.media_geometry import caption_line_capacity, probe_video, resolve_subtitle_style


DEFAULT_STYLE = DEFAULT_SUBTITLE_STYLE


def clip_srt(subtitles, start_ms, end_ms):
    blocks = []
    for sub in subtitles:
        if sub.end_ms > start_ms and sub.start_ms < end_ms:
            lo, hi = max(0, sub.start_ms - start_ms), min(end_ms, sub.end_ms) - start_ms
            blocks.append(f"{len(blocks) + 1}\n{format_time(lo, ',')} --> {format_time(hi, ',')}\n{sub.text}\n")
    return "\n".join(blocks)


def caption_display_text(subtitles, replacements):
    """Apply explicit display edits once; keep selection evidence and timings intact."""
    if not isinstance(replacements, dict) or any(
            not isinstance(wrong, str) or not wrong or not isinstance(right, str) or not right or
            any(char in wrong + right for char in "\r\n")
            for wrong, right in replacements.items()):
        raise SelectionError("subtitle_style.text_replacements 需为非空错词到正词的字符串对象")
    if not replacements:
        return subtitles, []
    pattern = re.compile("|".join(re.escape(s) for s in sorted(replacements, key=len, reverse=True)))
    corrected, changes = [], []
    for sub in subtitles:
        text = pattern.sub(lambda match: replacements[match.group()], sub.text)
        corrected.append(replace(sub, text=text))
        if text != sub.text:
            changes.append({"subtitle_id": sub.id, "start_ms": sub.start_ms, "end_ms": sub.end_ms,
                            "before": sub.text, "after": text})
    return corrected, changes


def caption_line_length(style):
    return caption_line_capacity(style)


def emphasize_ass(path, subtitles, clip, mode="cue", max_chars=24, balanced=False):
    """Color/size only on verified original cues; no invented commentary."""
    if mode == "none":
        return
    focus_ids = set(clip.get("editorial", {}).get("focus_subtitle_ids", []))
    if not focus_ids:
        return
    visible = [s for s in subtitles if s.end_ms > clip["start_ms"] and s.start_ms < clip["end_ms"]]
    text = path.read_text(encoding="utf-8-sig")
    lines, index = [], 0
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if visible[index].id in focus_ids and mode == "cue":
                # Relative font scaling preserves configured resolution/style.
                fields[9] = r"{\fscx112\fscy112\c&H00D7ACFF&\bord5\shad1}" + fields[9]
                line = ",".join(fields)
            elif visible[index].id in focus_ids and mode == "phrase":
                sid = visible[index].id
                phrases = [p["text"] for p in clip.get("editorial", {}).get("focus_phrases", [])
                           if p["subtitle_id"] == sid]
                # Color source substrings before wrapping; editorial captions never
                # become spoken captions. Timings remain the existing ASR cue times.
                original = visible[index].text
                patterns = [p for p in phrases if p.casefold() in original.casefold()]
                if patterns:
                    from core.subtitle_utils import SubtitleUtils
                    marked, cursor = [], 0
                    regex = re.compile("|".join(re.escape(p) for p in sorted(patterns, key=len, reverse=True)), re.I)
                    # Keep the original wrap points despite inline ASS styling.
                    wrapped = SubtitleUtils.auto_wrap_text(original, max_chars, balanced=balanced)
                    breaks = {m.start() for m in re.finditer("\n", wrapped)}
                    # Convert wrapped positions back to offsets in the unwrapped text.
                    breaks = {b-i for i, b in enumerate(sorted(breaks))}
                    for match in regex.finditer(original):
                        marked.append((cursor, match.start(), False))
                        marked.append((match.start(), match.end(), True))
                        cursor = match.end()
                    marked.append((cursor, len(original), False))
                    parts = []
                    for start, end, accent in marked:
                        if accent:
                            parts.append(r"{\c&H00FFEB31&}")
                        for offset in range(start, end):
                            if offset in breaks:
                                parts.append(r"\N")
                            parts.append(original[offset])
                        if accent:
                            parts.append(r"{\r}")
                    fields[9] = "".join(parts)
                    line = ",".join(fields)
            index += 1
        lines.append(line)
    atomic_text(path, "\n".join(lines) + "\n")


def render_clip(video, source_identity, subtitles, clip, output_dir, style=None, encoder="libx264",
                word_timing_path=None):
    if encoder not in {"libx264", "h264_nvenc"}:
        raise SelectionError("encoder 只支持 libx264 或 h264_nvenc")
    video = Path(video).resolve()
    media = probe_video(video)
    style = resolve_subtitle_style(style, media)
    if style["emphasis_mode"] not in {"cue", "phrase", "none"}:
        raise SelectionError("subtitle_style.emphasis_mode 支持 cue、phrase、none")
    display_subtitles, display_changes = caption_display_text(subtitles, style["text_replacements"])
    visible_display = [s for s in display_subtitles if s.end_ms > clip["start_ms"] and s.start_ms < clip["end_ms"]]
    captions, layout = layout_captions(visible_display, style, source_identity, word_timing_path)
    max_chars = caption_line_length(style)
    # Editorial prose, cover focus, and run statuses do not change video pixels.
    render_content = {name: clip[name] for name in ("id", "start_ms", "end_ms")}
    emphasis = clip.get("editorial", {}) if style["emphasis_mode"] != "none" else {}
    render_content["emphasis_ids"] = emphasis.get("focus_subtitle_ids", [])
    render_content["emphasis_phrases"] = emphasis.get("focus_phrases", [])
    identity_data = {"version": 6, "source": source_identity, "clip": render_content,
                            "style": style, "encoder": encoder,
                            "captions": [(s.start_ms, s.end_ms, s.text) for s in captions],
                            "subtitles": [(sub.start_ms, sub.end_ms, sub.text) for sub in subtitles]}
    identity = fingerprint(identity_data)
    directory = Path(output_dir).resolve() / "clips" / clip["id"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "clip.mp4"
    receipt = directory / "render.json"
    duration_ms = clip["end_ms"] - clip["start_ms"]
    if target.exists() and receipt.exists():
        try:
            meta = json.loads(receipt.read_text(encoding="utf-8"))
            actual = probe_video(target)
            compatible = meta.get("fingerprint") == identity
            if not compatible and style["emphasis_mode"] == "none":
                # Version 6 receipts used to include invisible focus annotations.
                # Recompute the old fingerprint with stored annotations; all
                # source, timing, captions, style and encoder inputs still match.
                old_editorial = meta.get("clip", {}).get("editorial", {})
                old_content = dict(render_content,
                    emphasis_ids=old_editorial.get("focus_subtitle_ids", []),
                    emphasis_phrases=old_editorial.get("focus_phrases", []))
                compatible = meta.get("fingerprint") == fingerprint(dict(identity_data, clip=old_content))
            same_shape = (actual["width"],actual["height"]) == (media["width"],media["height"])
            if compatible and same_shape and abs(actual["duration_ms"] - duration_ms) <= 1000:
                if meta.get("fingerprint") != identity:
                    write_json(receipt, dict(meta, fingerprint=identity))
                return target
        except (ValueError, OSError):
            pass

    # Keep the previous output until FFmpeg closes and the new video passes probing.
    with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as temporary:
        staging = Path(temporary)
        ass_path = staging / "captions.ass"
        utility = SubtitleUtils({"subtitle": style})
        all_subs = [{"start": sub.start_ms / 1000, "end": sub.end_ms / 1000, "text": sub.text}
                    for sub in captions]
        utility.create_ass_file(all_subs, ass_path, clip["start_ms"] / 1000,
                                clip["end_ms"] / 1000, max_chars, prepared=True)
        caption_clip = clip
        if layout["mode"] == "phrases" and clip.get("editorial"):
            editorial = clip["editorial"]
            focus = set(editorial.get("focus_subtitle_ids", []))
            focus_ids = [e["id"] for e in layout["events"] if focus.intersection(e["source_ids"])]
            phrases = [{"subtitle_id": e["id"], "text": p["text"]} for e in layout["events"]
                       for p in editorial.get("focus_phrases", [])
                       if p["subtitle_id"] in e["source_ids"] and p["text"] in e["text"]]
            caption_clip = dict(clip, editorial=dict(editorial, focus_subtitle_ids=focus_ids, focus_phrases=phrases))
        emphasize_ass(ass_path, captions, caption_clip, style["emphasis_mode"], max_chars, style["balanced_lines"])
        output = staging / "clip.mp4"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-ss", f"{clip['start_ms'] / 1000:.3f}", "-i", str(video),
                   "-t", f"{duration_ms / 1000:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                   "-vf", "ass=filename=captions.ass", "-c:v", encoder]
        command += ["-preset", "fast", "-crf", "23"] if encoder == "libx264" else ["-preset", "p4", "-cq", "23"]
        command += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output)]
        try:
            subprocess.run(command, cwd=staging, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=max(180, duration_ms / 1000 * 20))
        except FileNotFoundError:
            raise SelectionError("未找到 ffmpeg，请将 FFmpeg 的 bin 目录加入 Path") from None
        except subprocess.CalledProcessError as exc:
            atomic_text(directory / "render-error.log", exc.stderr.decode("utf-8", errors="replace"))
            raise SelectionError(f"切片编码失败，旧视频保留，详情：{directory / 'render-error.log'}") from None
        except subprocess.TimeoutExpired:
            raise SelectionError("切片编码超时，旧视频保留；可调整编码器或缩短片段") from None
        actual = probe_video(output)
        if abs(actual["duration_ms"] - duration_ms) > 1000:
            raise SelectionError("新成片时长与选定范围不符，未替换旧视频")
        if (actual["width"],actual["height"]) != (media["width"],media["height"]):
            raise SelectionError("新成片的显示尺寸与原视频不符，未替换旧视频")
        generated_srt = clip_srt(captions, clip["start_ms"], clip["end_ms"])
        os.replace(output, target)
        os.replace(ass_path, directory / "captions.generated.ass")
        atomic_text(directory / "captions.generated.srt", generated_srt)
        if "events" in layout:
            layout["events"] = [e for e in layout["events"]
                                if e["end_ms"] > clip["start_ms"] and e["start_ms"] < clip["end_ms"]]
        write_json(directory / "captions.layout.json", dict(layout, style=style))
        write_json(directory / "captions.corrections.json", {
            "scope": "字幕显示纠错；原始转写、选片证据与时间保持不变",
            "replacements": style["text_replacements"],
            "changes": [c for c in display_changes if c["end_ms"] > clip["start_ms"] and c["start_ms"] < clip["end_ms"]]})
        write_json(receipt, {"fingerprint": identity, "clip": clip, "duration_ms": actual["duration_ms"],
                             "subtitle_style": style, "video_geometry": actual})
    return target
