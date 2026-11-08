"""Export local frame-based covers and evidence-based posting drafts."""

import json
import hashlib
import os
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat, ImageFilter

from core.cover_copy import fallback_cover_lines, validate_cover_copy
from core.highlight_selection import SelectionError, atomic_text, fingerprint, write_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLICATION = {"enabled": True, "cover_count": 3, "font_path": "", "source_url": "",
                       "tags": ["直播切片"], "editorial_enabled": True, "copy_review_enabled": True,
                       "cover_style": "event_yellow"}
COVER_STYLES = {"event_yellow", "live_reaction", "quote_poster"}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_font(configured=""):
    if configured:
        path = (ROOT / Path(configured).expanduser()).resolve()
        if path.is_file():
            return path
        raise SelectionError("封面字体不存在，请检查 publication.font_path")
    candidates = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyhbd.ttc",
                  Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
                  Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
                  Path("/System/Library/Fonts/PingFang.ttc")]
    for path in candidates:
        if path.is_file():
            return path
    raise SelectionError("未找到中文字体，请在 publication.font_path 配置本地中文字体文件")


def posting_metadata(video, clip, config):
    editorial = clip.get("editorial", {})
    title = editorial.get("titles", [{"text": clip["title"]}])[0]["text"]
    quotes = list(dict.fromkeys(item["quote"] for item in clip["evidence"]
                               if item["role"] in {"trigger", "payoff"}))
    # Use verified quotations rather than turning ASR-based visual guesses into facts.
    description = (title + "\n\n片段对话摘录：\n" + "\n".join(f"「{quote}」" for quote in quotes)
                   + f"\n\n节选自：{Path(video).stem}\n原录播片段：{clip['timestamp']}")
    if config.get("source_url"):
        description += "\n原录播链接：" + config["source_url"]
    tags = list(config.get("tags", ["直播切片"]))
    for tag in ("A-SOUL", "嘉然", "贝拉", "乃琳", "心宜", "思诺"):
        if tag in Path(video).stem and tag not in tags:
            tags.append(tag)
    return {"title": title, "title_candidates": [i["text"] for i in editorial.get("titles", [])],
            "cover_lines": editorial.get("cover_lines", []), "cover_text": editorial.get("cover_text", ""),
            "description": description, "tags": tags,
            "video": "clip.mp4", "cover": "cover.jpg", "cover_candidates": [],
            "source": {"recording": Path(video).name, "timestamp": clip["timestamp"],
                       "url": config.get("source_url", "")}}


def wrap_title(text, font, width):
    lines, current = [], ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
        elif current and font.getlength(current + char) > width:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines or [""]


def decorate_quote_poster(frame, lines, font_path, target, focus=None, variant=0):
    """Use real dialogue and preserve scene context; no creator branding."""
    if frame.height>frame.width:
        return decorate_portrait(frame,lines,font_path,target,
                                 {"focus":focus,"zoom":1,"text_side":"bands"})
    image = ImageOps.fit(frame.convert("RGB"), (1280, 720), method=Image.Resampling.LANCZOS)
    portrait = focus is not None
    if portrait:
        # A focus point is an explicit local annotation, never inferred from ASR.
        x, y = focus
        width, height = frame.size
        crop_h = height / 2.1
        crop_w = crop_h * 610 / 720
        left = max(0, min(width - crop_w, x * width - crop_w / 2))
        top = max(0, min(height - crop_h, y * height - crop_h / 2))
        subject = frame.crop((round(left), round(top), round(left + crop_w), round(top + crop_h)))
        subject = ImageOps.fit(subject, (610, 720), method=Image.Resampling.LANCZOS)
        image = image.filter(ImageFilter.GaussianBlur(12))
        image.paste(subject, (670, 0))
    image = image.convert("RGBA")
    shade = Image.new("RGBA", image.size)
    d = ImageDraw.Draw(shade)
    if portrait:
        for x in range(870):
            alpha = 140 if x < 590 else round(140 * (870 - x) / 280)
            d.line((x, 0, x, 720), fill=(30, 16, 45, alpha))
    else:
        for y in range(330, 720):
            d.line((0, y, 1280, y), fill=(22, 12, 30, round(170 * (y - 330) / 390)))
    image = Image.alpha_composite(image, shade)
    draw = ImageDraw.Draw(image)
    text_width = 695 if portrait else 1160
    # Keep hand-picked quote lines intact where possible; fit at thumbnail scale.
    for size in range(106 if portrait else 104, 35, -2):
        font = ImageFont.truetype(str(font_path), size)
        fitted = [part for line in lines for part in wrap_title(line, font, text_width)]
        if len(fitted) == len(lines) and (size + 26) * len(fitted) <= (520 if portrait else 350):
            break
    height = len(fitted) * (size + 26)
    top = (720 - height) // 2 if portrait else 690 - height
    palettes = [("#FFE88A", "#FFACD7", "#FFFFFF"), ("#B9ECFF", "#FFFFFF", "#FFE88A"),
                ("#FFB8D9", "#FFE88A", "#FFFFFF")]
    colors = palettes[variant % len(palettes)]
    for index, line in enumerate(fitted):
        left = 42 if portrait else (1280 - font.getlength(line)) / 2
        pos = (left, top + index * (size + 26))
        # Dark outer stroke + white inner border + warm/colorful glyphs.
        draw.text(pos, line, font=font, fill=colors[index % 3], stroke_width=13, stroke_fill="#49304F")
        draw.text(pos, line, font=font, fill=colors[index % 3], stroke_width=8, stroke_fill="#FFF9EF")
        draw.text(pos, line, font=font, fill=colors[index % 3], stroke_width=2, stroke_fill="#9C527D")
    image.convert("RGB").save(target, quality=94, optimize=True)


def cover_composition(clip):
    """Optional source-bound human annotations, never coordinates invented by an LLM."""
    raw = clip.get("cover_composition", {})
    if not isinstance(raw, dict) or set(raw) - {"focus", "zoom", "text_side", "frame_ms"}:
        raise SelectionError("封面构图支持 focus、zoom、text_side、frame_ms")
    focus = raw.get("focus", clip.get("cover_focus"))
    if focus is not None and (not isinstance(focus, list) or len(focus) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in focus)):
        raise SelectionError("cover_focus 需为 0–1 范围内的 [x,y] 画面焦点")
    zoom = raw.get("zoom", 1.8 if focus is not None else 1)
    if type(zoom) not in (int, float) or not math.isfinite(zoom) or not 1 <= zoom <= 3.5:
        raise SelectionError("封面 zoom 需为 1–3.5")
    side = raw.get("text_side", "right" if focus is not None else "bands")
    if side not in {"left", "right", "bands"}:
        raise SelectionError("封面 text_side 支持 left、right、bands")
    frame_ms = raw.get("frame_ms")
    if frame_ms is not None and (type(frame_ms) is not int or not clip["start_ms"] <= frame_ms < clip["end_ms"]):
        raise SelectionError("封面 frame_ms 必须在本切片内")
    return {"focus": focus, "zoom": zoom, "text_side": side, "frame_ms": frame_ms}


def reaction_scene(frame, composition):
    """One real scene crop: no duplicate person, synthetic expression, or blur panel."""
    width, height = frame.size
    target_size=(720,1280) if height>width else (1280,720)
    ratio=target_size[0]/target_size[1]
    crop_w = min(width, height * ratio) / composition["zoom"]
    crop_h = crop_w / ratio
    focus = composition["focus"] or [.5, .5]
    # Leave room for type to one side of the annotated face.
    subject_x = {"right": .28, "left": .72, "bands": .5}[composition["text_side"]]
    left = max(0, min(width - crop_w, focus[0] * width - subject_x * crop_w))
    top = max(0, min(height - crop_h, focus[1] * height - .43 * crop_h))
    crop = frame.crop((round(left), round(top), round(left + crop_w), round(top + crop_h)))
    return ImageOps.fit(crop.convert("RGB"), target_size, method=Image.Resampling.LANCZOS)


def decorate_portrait(frame,lines,font_path,target,composition):
    """Keep a portrait scene intact and fit complete copy inside its width."""
    image=reaction_scene(frame,dict(composition,text_side="bands"))
    if not 1<=len(lines)<=3:
        raise SelectionError("竖版封面需为 1–3 行文案")
    draw=ImageDraw.Draw(image)
    width,height=image.size
    row_height=118
    top=height-72-row_height*len(lines)
    for size in range(104,19,-2):
        font=ImageFont.truetype(str(font_path),size)
        stroke=max(4,round(size*.085))
        boxes=[draw.textbbox((0,0),line,font=font,stroke_width=stroke) for line in lines]
        if all(b[2]-b[0]<=width-64 and b[3]-b[1]<=row_height-10 for b in boxes):
            break
    else:
        raise SelectionError("竖版封面文案无法完整显示，请缩短文案")
    for index,(line,bounds) in enumerate(zip(lines,boxes)):
        x=(width-(bounds[2]-bounds[0]))/2-bounds[0]
        y=top+index*row_height+(row_height-(bounds[3]-bounds[1]))/2-bounds[1]
        draw.text((x,y),line,font=font,fill="#FFE100",stroke_width=stroke,stroke_fill="#080808")
    image.save(target,quality=94,optimize=True)


def decorate_reaction(frame, lines, font_path, target, composition, variant=0):
    if frame.height>frame.width:
        return decorate_portrait(frame,lines,font_path,target,composition)
    image = reaction_scene(frame, composition)
    draw = ImageDraw.Draw(image)
    side = composition["text_side"]
    # Consistent, high contrast glyphs survive a 320px-wide recommendation card.
    palettes = [("#FFF02A", "#FF6BAB", "#FFFFFF"),
                ("#31EBFF", "#FFF02A", "#FFFFFF"),
                ("#FF6BAB", "#FFFFFF", "#FFF02A")]
    colors = palettes[variant % len(palettes)]
    if side == "bands":
        boxes = [(36, 24, 1208, 150)] if len(lines) == 1 else [(36, 24, 1208, 150)] + [
            (36, 720 - (len(lines)-1-i)*142 - 34, 1208, 132) for i in range(len(lines)-1)]
    else:
        x = 650 if side == "right" else 35
        total = 162 * len(lines)
        top = max(28, (720-total)//2)
        boxes = [(x, top + i*162, 595, 146) for i in range(len(lines))]
    for i, (text, box) in enumerate(zip(lines, boxes)):
        x, y, width, height = box
        for size in range(142, 27, -2):
            font = ImageFont.truetype(str(font_path), size)
            bounds = font.getbbox(text, stroke_width=10)
            if bounds[2]-bounds[0] <= width and bounds[3]-bounds[1] <= height:
                break
        # Center by actual glyph bounds, not baseline metrics (avoids bottom clipping).
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=10)
        pos = (x + (width-(bounds[2]-bounds[0]))/2-bounds[0],
               y + (height-(bounds[3]-bounds[1]))/2-bounds[1])
        draw.text(pos, text, font=font, fill=colors[i % 3], stroke_width=10, stroke_fill="#080808")
    image.save(target, quality=94, optimize=True)


def decorate_event_yellow(frame, lines, font_path, target, composition, variant=0):
    """A real scene, one concrete event and yellow type; preserve scene context."""
    if frame.height>frame.width:
        return decorate_portrait(frame,lines,font_path,target,composition)
    image = reaction_scene(frame, composition)
    draw = ImageDraw.Draw(image)
    side = composition["text_side"]
    if not 1 <= len(lines) <= 3:
        raise SelectionError("事件封面需为 1–3 行文案")
    # With no reviewed location, keep the full scene and use its lower band.
    # A text-only model cannot choose which face or reaction should be cropped.
    if side == "bands":
        width, left = 1180, 50
        row_height = 138
        top = 668 - row_height * len(lines)
    else:
        width, left = 600, (640 if side == "right" else 40)
        row_height = 158
        top = (720 - row_height * len(lines)) // 2
    # Fit all lines at one size; short secondary lines should not become louder
    # than the main hook. Include the outline in the safe-area calculation.
    for size in range(144, 19, -2):
        font = ImageFont.truetype(str(font_path), size)
        stroke = max(4, round(size * .085))
        bounds = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        if all(b[2]-b[0] <= width and b[3]-b[1] <= row_height-10 for b in bounds):
            break
    else:
        raise SelectionError("封面文案无法在安全区域内完整显示，请缩短文案")
    for index, (line, box) in enumerate(zip(lines, bounds)):
        text_width, text_height = box[2]-box[0], box[3]-box[1]
        x = left + (width-text_width)/2 - box[0]
        y = top + index*row_height + (row_height-text_height)/2 - box[1]
        draw.text((x, y), line, font=font, fill="#FFE100",
                  stroke_width=stroke, stroke_fill="#080808")
    image.save(target, quality=94, optimize=True)


def extract_frame(video, seconds, target):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{seconds:.3f}",
               "-i", str(Path(video).resolve()), "-map", "0:v:0", "-frames:v", "1", "-q:v", "2", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=90,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with Image.open(target) as image:
            image.load()
            return image.convert("RGB")
    except (OSError, subprocess.SubprocessError) as exc:
        raise SelectionError("封面截图失败，原视频与已生成封面保留") from exc


def generate_publication(video, source_identity, clip, output_dir, config=None):
    config = dict(DEFAULT_PUBLICATION, **(config or {}))
    if type(config["cover_count"]) is not int or not 1 <= config["cover_count"] <= 5:
        raise SelectionError("publication.cover_count 需为 1–5")
    if config["cover_style"] not in COVER_STYLES:
        raise SelectionError("publication.cover_style 支持 event_yellow、live_reaction 或 quote_poster")
    directory = Path(output_dir) / "clips" / clip["id"]
    directory.mkdir(parents=True, exist_ok=True)
    metadata = posting_metadata(video, clip, config)
    font_path = find_font(config["font_path"])
    count = config["cover_count"]
    editorial = clip.get("editorial", {})
    lines = [i["text"] for i in editorial.get("cover_lines", [])]
    if not lines:
        lines = fallback_cover_lines(clip["title"])
    metadata["cover_text"] = validate_cover_copy(lines, editorial.get("cover_text"))
    composition = cover_composition(clip)
    focus = composition["focus"]
    identity = fingerprint({"version": 8, "source": source_identity, "config": config,
                            "font": {"path": str(font_path), "mtime": font_path.stat().st_mtime_ns},
                            "title": clip["title"], "editorial": editorial, "composition": composition, "evidence": clip["evidence"],
                            "start_ms": clip["start_ms"], "end_ms": clip["end_ms"]})
    receipt = directory / "publication.receipt.json"
    if receipt.is_file():
        try:
            cached = json.loads(receipt.read_text(encoding="utf-8"))
            required = {"cover.jpg", "title.txt", "description.txt", "tags.txt", "投稿文案.txt", "metadata.json"}
            required.update(f"cover_{i:02}.jpg" for i in range(1, count + 1))
            if cached.get("fingerprint") == identity and set(cached["files"]) == required and all(
                    (directory / name).is_file() and file_hash(directory / name) == value
                    for name, value in cached["files"].items()):
                return json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            pass
    # Stage all artifacts; extraction/layout failures must not remove previous files.
    with tempfile.TemporaryDirectory(prefix=".publication-", dir=directory) as temporary:
        staging = Path(temporary)
        ranked = []
        anchor = editorial.get("frame_ms", next((i["start_ms"] + (i["end_ms"] - i["start_ms"]) // 2
                    for i in clip["evidence"] if i["role"] == "payoff"), (clip["start_ms"] + clip["end_ms"]) // 2))
        if composition["frame_ms"] is not None:
            anchor = composition["frame_ms"]
        offsets = [0, -.7, .7, -1.4, 1.4][:count]
        for index, offset in enumerate(offsets, 1):
            seconds = max(clip["start_ms"], min(clip["end_ms"] - 100, anchor + offset * 1000)) / 1000
            frame = extract_frame(video, seconds, staging / f"frame-{index}.jpg")
            stats = ImageStat.Stat(frame.convert("L").resize((160, 90)))
            score = stats.stddev[0] - max(0, 20 - stats.mean[0]) * 2 - abs(offset) * 3
            name = f"cover_{index:02}.jpg"
            if config["cover_style"] == "event_yellow":
                decorate_event_yellow(frame, lines, font_path, staging / name, composition, index - 1)
            elif config["cover_style"] == "live_reaction":
                decorate_reaction(frame, lines, font_path, staging / name, composition, index - 1)
            else:
                palette_offset = int(fingerprint(clip["id"])[:2], 16) % 3
                decorate_quote_poster(frame, lines, font_path, staging / name, focus, index - 1 + palette_offset)
            ranked.append((score, name, seconds))
        # A reviewed exact frame outranks a global contrast heuristic.
        _, best_name, best_time = ranked[0] if composition["frame_ms"] is not None else max(ranked)
        (staging / "cover.jpg").write_bytes((staging / best_name).read_bytes())
        metadata["cover_candidates"] = [name for _, name, _ in ranked]
        metadata["cover_source_seconds"] = best_time
        metadata["cover_frame_policy"] = ("人工复核的片内取帧时间" if composition["frame_ms"] is not None else
                                           "关键句附近取帧；对比度仅用于排除暗帧，不判断表情")
        metadata["cover_focus"] = focus
        metadata["cover_composition"] = composition
        metadata["cover_style"] = config["cover_style"]
        with Image.open(staging/"cover.jpg") as cover:
            metadata["cover_dimensions"]={"width":cover.width,"height":cover.height}
        atomic_text(staging / "title.txt", metadata["title"] + "\n")
        atomic_text(staging / "description.txt", metadata["description"] + "\n")
        atomic_text(staging / "tags.txt", ",".join(metadata["tags"]) + "\n")
        atomic_text(staging / "投稿文案.txt", "标题：" + metadata["title"] + "\n\n简介：\n" + metadata["description"]
                    + "\n\n标签：" + ",".join(metadata["tags"]) + "\n封面：cover.jpg\n视频：clip.mp4\n")
        write_json(staging / "metadata.json", metadata)
        files = [path for path in staging.iterdir() if not path.name.startswith("frame-")]
        hashes = {path.name: file_hash(path) for path in files}
        for path in files:
            os.replace(path, directory / path.name)
        write_json(receipt, {"fingerprint": identity, "files": hashes})
    return metadata
