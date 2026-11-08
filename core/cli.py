"""An existing recording -> evidence-backed, semantically reviewed clips."""

import argparse
import copy
import html
import importlib.util
import importlib.metadata
import json
import os
import re
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from core.highlight_model import ChatModel
from core.highlight_render import probe_video, render_clip
from core.local_config import load_local_key
from core.local_transcription import transcribe
from core.job_lock import job_lock
from core.clip_publication import COVER_STYLES, DEFAULT_PUBLICATION, generate_publication
from core.clip_editorial import edit_clip
from core.subtitle_correction import (DEFAULT_CORRECTION, DictionaryCorrector, correct_subtitles,
                                      subtitle_fingerprint)
from core.file_utils import sanitize_filename
from core.paths import PROJECT_ROOT, default_config_path, resolve_config_path
from core.report_preview import preview_help, write_preview_launcher
from core.danmaku_signals import DEFAULT_DANMAKU, danmaku_path, load_danmaku, validate_danmaku_config
from core.highlight_selection import (HighlightPipeline, SelectionError, atomic_text, build_windows,
                                      DEFAULT_MIN_SCORE, ModelAPIError, fingerprint, format_time, read_subtitles, write_json,
                                      selection_windows, validate_excluded_ranges, validate_min_score)



DEFAULT_CONFIG = {
    "input": {"video": "", "srt": "", "output_dir": ""},
    "llm": {"base_url": "https://api.siliconflow.cn/v1", "model": "", "api_key_env": "SILICONFLOW_API_KEY",
            "json_mode": True, "enable_thinking": None, "max_tokens": 4096, "timeout_seconds": 180, "attempts": 2},
    "asr": {"backend": "faster_whisper", "model": "turbo", "device": "auto", "compute_type": "auto", "language": "zh",
            "chunk_seconds": 25, "beam_size": 3,
            "initial_prompt": "A-SOUL，嘉然，贝拉，乃琳，心宜，思诺。"},
    "selection": {"window_seconds": 600, "overlap_seconds": 180, "max_window_chars": 18000,
                  "min_clip_seconds": 15, "max_clip_seconds": 180, "candidates_per_window": 4,
                  "max_clips": 8, "boundary_context_seconds": 30, "lead_ms": 200, "tail_ms": 500,
                  "validation_attempts": 2, "min_score": DEFAULT_MIN_SCORE,
                  "exclude_ranges_ms": [],
                  "brief": "A-SOUL 中文直播。面向不了解主播的普通观众，优先不依赖粉丝背景也能理解的完整事件、反差和有前因的笑点。不要根据声音推测成员身份。"},
    "render": {"encoder": "libx264", "subtitle_style": {}},
    "correction": copy.deepcopy(DEFAULT_CORRECTION),
    "danmaku": copy.deepcopy(DEFAULT_DANMAKU),
    "publication": copy.deepcopy(DEFAULT_PUBLICATION),
}


def load_config(path):
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        updates = json.loads(resolve_config_path(path, PROJECT_ROOT).read_text(encoding="utf-8-sig"))
        if not isinstance(updates, dict):
            raise SelectionError("配置顶层必须是 JSON 对象")
        for section, value in updates.items():
            if section not in config or not isinstance(value, dict):
                raise SelectionError(f"未知配置段或格式错误：{section}")
            unknown = set(value) - set(config[section])
            if unknown:
                raise SelectionError(f"未知配置字段：{section}.{sorted(unknown)[0]}")
            config[section].update(value)
    cfg = config["selection"]
    validate_min_score(cfg["min_score"])
    for name in ("window_seconds", "max_window_chars", "min_clip_seconds", "max_clip_seconds",
                 "candidates_per_window", "max_clips"):
        if type(cfg[name]) is not int or cfg[name] <= 0:
            raise SelectionError(f"selection.{name} 必须是正整数")
    for name in ("overlap_seconds", "boundary_context_seconds", "lead_ms", "tail_ms"):
        if type(cfg[name]) is not int or cfg[name] < 0:
            raise SelectionError(f"selection.{name} 必须是非负整数")
    if cfg["overlap_seconds"] >= cfg["window_seconds"] or cfg["max_window_chars"] < 100:
        raise SelectionError("窗口重叠必须小于窗口长度，max_window_chars 至少为 100")
    if cfg["min_clip_seconds"] >= cfg["max_clip_seconds"]:
        raise SelectionError("最大片长必须大于最小片长")
    if cfg["max_clip_seconds"] > cfg["window_seconds"]:
        raise SelectionError("窗口应至少覆盖最大目标片长")
    validate_excluded_ranges(cfg["exclude_ranges_ms"])
    if not isinstance(config["render"]["subtitle_style"], dict):
        raise SelectionError("render.subtitle_style 必须是对象")
    if config["render"]["encoder"] not in {"libx264", "h264_nvenc"}:
        raise SelectionError("render.encoder 只支持 libx264 或 h264_nvenc")
    for name in ("attempts", "max_tokens", "timeout_seconds"):
        if type(config["llm"][name]) is not int or config["llm"][name] <= 0:
            raise SelectionError(f"llm.{name} 必须是正整数")
    if config["llm"]["attempts"] > 5:
        raise SelectionError("llm.attempts 不能超过 5")
    if config["asr"]["device"] not in {"auto", "cuda", "cpu"}:
        raise SelectionError("asr.device 需为 auto、cuda 或 cpu")
    asr = config["asr"]
    if asr["backend"] not in {"faster_whisper", "firered"}:
        raise SelectionError("asr.backend 需为 faster_whisper 或 firered")
    if not isinstance(asr["model"], str) or not asr["model"].strip():
        raise SelectionError("asr.model 需填写模型名或本地模型目录")
    if type(asr["chunk_seconds"]) is not int or not 5 <= asr["chunk_seconds"] <= 30:
        raise SelectionError("asr.chunk_seconds 需为 5–30 秒")
    if type(asr["beam_size"]) is not int or not 1 <= asr["beam_size"] <= 10:
        raise SelectionError("asr.beam_size 需为 1–10")
    if asr["backend"] == "firered" and asr["compute_type"] not in {"auto", "bfloat16", "float32"}:
        raise SelectionError("FireRed 的 asr.compute_type 需为 auto、bfloat16 或 float32")
    for name in ("video", "srt", "output_dir"):
        if not isinstance(config["input"][name], str):
            raise SelectionError(f"input.{name} 必须是路径字符串")
    for name in ("base_url", "model", "api_key_env"):
        if not isinstance(config["llm"][name], str):
            raise SelectionError(f"llm.{name} 必须是字符串")
    if type(config["llm"]["json_mode"]) is not bool or (config["llm"]["enable_thinking"] is not None and type(config["llm"]["enable_thinking"]) is not bool):
        raise SelectionError("llm.json_mode 需为布尔值；enable_thinking 需为布尔值或 null")
    if type(cfg["validation_attempts"]) is not int or not 1 <= cfg["validation_attempts"] <= 3:
        raise SelectionError("selection.validation_attempts 需为 1–3")
    publication = config["publication"]
    correction = config["correction"]
    if type(correction["enabled"]) is not bool:
        raise SelectionError("correction.enabled 需为布尔值")
    if not isinstance(correction["dictionary_path"], str) or (correction["enabled"] and not correction["dictionary_path"].strip()):
        raise SelectionError("correction.dictionary_path 需填写词典路径")
    if not isinstance(correction["ignore_sources"], list) or not all(isinstance(s, str) and s.strip() for s in correction["ignore_sources"]):
        raise SelectionError("correction.ignore_sources 需为非空词条组成的数组")
    if type(publication["enabled"]) is not bool:
        raise SelectionError("publication.enabled 需为布尔值")
    if type(publication["editorial_enabled"]) is not bool:
        raise SelectionError("publication.editorial_enabled 需为布尔值")
    if type(publication["copy_review_enabled"]) is not bool:
        raise SelectionError("publication.copy_review_enabled 需为布尔值")
    if publication["cover_style"] not in COVER_STYLES:
        raise SelectionError("publication.cover_style 支持 event_yellow、live_reaction 或 quote_poster")
    if type(publication["cover_count"]) is not int or not 1 <= publication["cover_count"] <= 5:
        raise SelectionError("publication.cover_count 需为 1–5")
    if not all(isinstance(publication[name], str) for name in ("font_path", "source_url")):
        raise SelectionError("publication.font_path 和 source_url 需为字符串")
    if not isinstance(publication["tags"], list) or not all(isinstance(tag, str) and tag.strip() for tag in publication["tags"]):
        raise SelectionError("publication.tags 需为非空标签组成的数组")
    validate_danmaku_config(config["danmaku"])
    return config


def source_identity(video):
    stat = video.stat()
    return {"path": str(video), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def caption_word_path(result):
    raw = result.get("correction", {}).get("raw_srt") or result.get("raw_srt") or result.get("srt")
    return Path(raw).with_name("transcript.words.json") if raw else None


def write_report(output, video, result):
    def esc(value):
        return html.escape(str(value), quote=True)

    def local_url(path):
        try:
            # Browser URL paths need percent escaping, including # in filenames.
            from urllib.parse import quote
            return quote(os.path.relpath(path, output).replace("\\", "/"), safe="/:")
        except ValueError:
            return Path(path).as_uri()

    cards = []
    for clip in result["clips"]:
        generated = output / "clips" / clip["id"] / "clip.mp4"
        # Use a rendered clip only when this run validated it; avoid stale output.
        has_render = clip.get("render_status") == "complete" and generated.exists()
        url = local_url(generated) if has_render else local_url(video) + f"#t={clip['start_ms']/1000:.3f},{clip['end_ms']/1000:.3f}"
        evidence = "".join(f"<li>{esc(item['role'])} · 字幕 {item['subtitle_id']} · "
                           f"{format_time(item['start_ms'])}：{esc(item['quote'])}</li>" for item in clip["evidence"])
        publication_html, poster = "", ""
        if clip.get("publication_status") == "complete":
            directory = output / "clips" / clip["id"]
            metadata = clip["publication"]
            cover_url = local_url(directory / "cover.jpg")
            poster = f" poster='{esc(cover_url)}'"
            alternatives = " · ".join(f"<a href='{esc(local_url(directory / name))}'>封面 {i}</a>"
                                       for i, name in enumerate(metadata["cover_candidates"], 1))
            publication_html = (f"<p><a href='{esc(cover_url)}'><img class='cover' src='{esc(cover_url)}' alt='切片封面'></a></p>"
                                f"<p><a href='{esc(cover_url)}'>默认封面</a> · {alternatives} · "
                                f"<a href='{esc(local_url(directory / '投稿文案.txt'))}'>投稿文案</a> · "
                                f"<a href='{esc(local_url(directory / 'metadata.json'))}'>投稿信息 JSON</a></p>"
                                f"<p><b>简介草稿</b></p><pre>{esc(metadata['description'])}</pre>"
                                f"<p>标签：{esc('、'.join(metadata['tags']))}</p>")
        display_title = clip.get("publication", {}).get("title", clip.get("editorial", {}).get("titles", [{"text":clip["title"]}])[0]["text"])
        alternatives_html = ""
        if clip.get("editorial"):
            alternatives_html = "<p>备选标题：" + " / ".join(esc(i["text"]) for i in clip["editorial"]["titles"][1:]) + "</p>"
        quality_html = ""
        if clip.get("quality_review"):
            review = clip["quality_review"]
            quality_html = (f"<p>看点分：{esc(clip['score'])}；初选 {esc(clip['selection_score'])} / "
                            f"复核 {esc(review['score'])}。{esc(review['reason'])}</p>")
        if clip.get("danmaku"):
            reaction = clip["danmaku"]
            quality_html += (f"<p>弹幕‘绷’：附近 {esc(reaction['reaction_messages'])} 条去重消息；"
                             f"排序加权 +{esc(reaction['bonus'])}，排序值 {esc(clip['priority_score'])}。"
                             "加权不改变看点分，也不表示独立观众数量。</p>")
        cards.append(f"<article><h2>{esc(display_title)}</h2>" + alternatives_html + f"<p>{esc(clip['timestamp'])}</p>"
                     f"<video controls preload='metadata'{poster} src='{esc(url)}'></video>" + publication_html +
                      f"<p>{esc(clip['reason'])}</p>" + quality_html + f"<p>边界复核：{esc(clip['boundary_reason'])}</p>"
                     f"<ul>{evidence}</ul><p>时间来源：字幕。人声和画面尚未核验。</p></article>")
    issues = "".join(f"<li>{esc(item.get('stage'))}：{esc(item.get('reason'))}</li>"
                     for item in result.get("failures", []) + result.get("rejected", []))
    correction_html = ""
    if result.get("correction"):
        corrected = result["correction"]
        correction_html = (f"<p>词典纠错：{corrected['changed_subtitles']} 条字幕、{corrected['replacements']} 处替换；"
                           "字幕时间保留。<a href='corrections.html'>查看原始文本与修改明细</a>。"
                           "以下证据引文来自本次使用的字幕版本，文字修正不等于已核对原音。</p>")
    quality_html = ""
    if result.get("quality_policy"):
        quality_html = (f"<p>看点门槛：{esc(result['quality_policy']['min_score'])} 分；初选和复核都需达标。"
                        "允许没有合格片段，不补齐数量。评分以语音字幕为证据，弹幕只作参考，不代表观众满意率。</p>")
        if result["quality_policy"].get("audience") == "general":
            quality_html += "<p>目标受众：不了解主播的普通观众；粉丝内梗不自动视为大众高光。</p>"
    if result.get("danmaku", {}).get("status") == "loaded":
        signal = result["danmaku"]
        quality_html += (f"<p>已读取时间轴弹幕：‘绷’ {esc(signal['reaction_messages'])} 条去重消息，"
                         f"偏移 {esc(signal['offset_seconds'])} 秒；用于提示与通过门槛后的排序。未使用视频 OCR。</p>")
        if "context_messages" in signal:
            quality_html += (f"<p>可参考的观众评论 {esc(signal['context_messages'])} 条；每个窗口最多抽样 "
                             f"{esc(signal['context_limit_per_window'])} 条辅助理解。弹幕不作为主播原话或已核实事实。</p>")
    elif result.get("danmaku"):
        quality_html += "<p>未配置独立弹幕时间轴，未进行‘绷’加权；MP4 中烧录的弹幕不会自动被读到。</p>"
    page = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>录播选片报告</title>"
            "<style>body{font-family:system-ui;max-width:1000px;margin:32px auto;padding:0 20px;line-height:1.7}"
            "article{border:1px solid #ccc;border-radius:10px;padding:18px;margin:20px 0}"
            "video{display:block;width:auto;height:auto;max-width:100%;max-height:640px;object-fit:contain;margin:0 auto;background:#111}"
            ".cover{display:block;width:auto;height:auto;max-width:100%;max-height:640px;margin:0 auto}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}li{overflow-wrap:anywhere}</style>"
            f"<h1>录播选片报告</h1><p>分析状态：{esc(result['status'])}；通过 {len(result['clips'])} 个片段。</p>"
            + quality_html + correction_html + preview_help() + "".join(cards)
            + f"<h2>失败及弃选原因</h2><ul>{issues or '<li>无</li>'}</ul></html>")
    atomic_text(output / "report.html", page)
    write_preview_launcher(output / "report.html", PROJECT_ROOT)
    items = []
    for clip in result["clips"]:
        if clip.get("publication_status") == "complete":
            metadata = clip["publication"]
            items.append(dict(metadata, id=clip["id"], video=f"clips/{clip['id']}/clip.mp4",
                              cover=f"clips/{clip['id']}/cover.jpg",
                              cover_candidates=[f"clips/{clip['id']}/{name}" for name in metadata["cover_candidates"]]))
    write_json(output / "posting_manifest.json", {"status": result["status"], "items": items})


def publish_clip(video, identity, clip, output, config, result):
    try:
        clip["publication"] = generate_publication(video, identity, clip, output, config["publication"])
        clip["publication_status"] = "complete"
        print(f"封面和投稿文案已生成：{output / 'clips' / clip['id']}", flush=True)
    except (SelectionError, OSError) as exc:
        clip["publication_status"] = "failed"
        result["failures"].append({"stage": "publication", "title": clip["title"], "reason": str(exc)})
        print(f"封面/文案生成失败：{exc}", flush=True)


def prepare_editorial(video, identity, clip, subtitles, output, config, model):
    if config["publication"]["enabled"] and config["publication"]["editorial_enabled"]:
        print(f"生成投稿标题及封面文案：{clip['title']}", flush=True)
        override, reviewed = None, output / "editorial.overrides.json"
        if reviewed.is_file():
            data = json.loads(reviewed.read_text(encoding="utf-8"))
            if data.get("source") != identity:
                raise SelectionError("投稿编辑复核与当前录播不匹配")
            if data.get("transcript_fingerprint") is not None and data["transcript_fingerprint"] != subtitle_fingerprint(subtitles):
                raise SelectionError("投稿编辑复核与当前字幕不匹配，请重新复核")
            override = data.get("clips", {}).get(clip["id"])
        clip["editorial"] = edit_clip(model, video, clip, subtitles, output, override,
                                      copy_review_enabled=config["publication"]["copy_review_enabled"],
                                      recording_brief=config["selection"]["brief"])
    # Optional local, source-bound annotations. Never ask a text model to locate a face.
    annotations = output / "cover_focus.json"
    if annotations.is_file():
        data = json.loads(annotations.read_text(encoding="utf-8"))
        if data.get("source") != identity:
            raise SelectionError("封面焦点标注与当前录播不匹配")
        if clip["id"] in data.get("clips", {}):
            annotation = data["clips"][clip["id"]]
            if isinstance(annotation, dict):
                clip["cover_composition"] = annotation
                clip.pop("cover_focus", None)
            else:
                clip["cover_focus"] = annotation
                clip.pop("cover_composition", None)


def package_existing(video, output, config, restyle=False):
    if video is None or output is None or not video.is_file():
        raise SelectionError("请先配置已有录播路径和输出目录")
    if not config["publication"]["enabled"]:
        raise SelectionError("publication.enabled 为 false，请启用后再补齐封面和文案")
    with job_lock(output / ".job.lock"):
        result = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
        identity = source_identity(video)
        if result.get("source") != identity:
            raise SelectionError("已有分析与当前录播文件不匹配，未生成封面或修改分析结果")
        model, subtitles = None, []
        if restyle:
            indexed = json.loads((output / "transcript.indexed.json").read_text(encoding="utf-8"))
            if indexed.get("source") != identity:
                raise SelectionError("字幕索引与当前录播不匹配")
            from core.highlight_selection import Subtitle
            subtitles = [Subtitle(**s) for s in indexed["subtitles"]]
            expected = result.get("transcript_fingerprint")
            if expected is not None:
                matches = expected == subtitle_fingerprint(subtitles)
            else:
                # Compatibility with earlier jobs, which stored only an SRT path.
                original = read_subtitles(result["srt"], max(s.end_ms for s in subtitles))
                matches = subtitle_fingerprint(original) == subtitle_fingerprint(subtitles)
            if not matches:
                raise SelectionError("字幕已变化，已有选片证据需要重新分析。请直接运行 run_clips.cmd，不要使用 --restyle")
            if config["publication"]["editorial_enabled"]:
                load_local_key(config["llm"]["api_key_env"], PROJECT_ROOT / ".env")
                model = ChatModel(config["llm"])
        reset_stages = {"publication", "editorial"} if restyle else {"publication"}
        result["failures"] = [item for item in result.get("failures", []) if item["stage"] not in reset_stages]
        count, eligible = 0, 0
        for clip in result["clips"]:
            if not re.fullmatch(r"[0-9a-f]{12}", clip["id"]):
                raise SelectionError("已有分析包含无效的切片 ID")
            if clip.get("render_status") != "complete":
                continue
            directory = output / "clips" / clip["id"]
            if not (directory / "clip.mp4").is_file():
                raise SelectionError("成片文件缺失，请先运行完整流程")
            rendered = json.loads((directory / "render.json").read_text(encoding="utf-8"))["clip"]
            if any(rendered[name] != clip[name] for name in ("id", "start_ms", "end_ms")):
                raise SelectionError("已有成片与选片时间不一致，未生成封面")
            eligible += 1
            if restyle:
                try:
                    prepare_editorial(video, identity, clip, subtitles, output, config, model)
                    render_clip(video, identity, subtitles, clip, output,
                                config["render"]["subtitle_style"], config["render"]["encoder"], caption_word_path(result))
                    clip["render_status"] = "complete"
                except (SelectionError, OSError) as exc:
                    clip["publication_status"] = "failed"
                    result["failures"].append({"stage": "editorial", "title": clip["title"], "reason": str(exc)})
                    print(f"风格更新失败，保留已有文件：{exc}", flush=True)
                    # Persist successful preceding clips even on quota/auth failure.
                    if isinstance(exc, ModelAPIError) and exc.blocks_remaining_requests:
                        break
                    continue
            publish_clip(video, identity, clip, output, config, result)
            count += clip.get("publication_status") == "complete"
        if not eligible:
            raise SelectionError("没有已完成的视频，请先运行完整流程")
        result["status"] = "complete" if not result["failures"] else "publication_incomplete"
        write_json(output / "analysis.json", result)
        write_report(output, video, result)
        write_json(output / "job.json", {"status": result["status"], "source": identity, "clips": count,
                                         "report": str(output / "report.html")})
        print(f"已补齐 {count} 个片段的封面与投稿文案。报告：{output / 'report.html'}")
        return 0 if result["status"] == "complete" else 2


def refresh_captions(video, output, config):
    """Restyle existing selections locally, preserving editorial/publication data."""
    if output is None:
        raise SelectionError("请通过 --output 指定已有切片的任务目录")
    with job_lock(output / ".job.lock"):
        result = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
        video = video or Path(result["source"]["path"])
        identity = source_identity(video)
        indexed = json.loads((output / "transcript.indexed.json").read_text(encoding="utf-8"))
        if result.get("source") != identity or indexed.get("source") != identity:
            raise SelectionError("已有分析或字幕索引与录播文件不匹配")
        from core.highlight_selection import Subtitle
        subtitles = [Subtitle(**s) for s in indexed["subtitles"]]
        expected = result.get("transcript_fingerprint")
        if expected is not None and expected != subtitle_fingerprint(subtitles):
            raise SelectionError("字幕索引已变化，请重新分析，不能直接更新字幕")
        if not result["clips"]:
            raise SelectionError("没有可更新的已有切片")
        result["failures"] = [f for f in result.get("failures", []) if f["stage"] != "render"]
        count = 0
        for i, clip in enumerate(result["clips"], 1):
            if not re.fullmatch(r"[0-9a-f]{12}", clip["id"]):
                raise SelectionError("已有分析包含无效的切片 ID")
            print(f"更新字幕 {i}/{len(result['clips'])}：{clip['title']}", flush=True)
            try:
                render_clip(video, identity, subtitles, clip, output, config["render"]["subtitle_style"],
                            config["render"]["encoder"], caption_word_path(result))
                clip["render_status"] = "complete"
                count += 1
            except (SelectionError, OSError) as exc:
                clip["render_status"] = "failed"
                result["failures"].append({"stage": "render", "title": clip["title"], "reason": str(exc)})
                print(f"字幕更新失败：{exc}", flush=True)
            write_json(output / "analysis.json", result)
        result["status"] = "complete" if not result["failures"] else "render_incomplete"
        write_json(output / "analysis.json", result)
        write_report(output, video, result)
        write_json(output / "job.json", {"status": result["status"], "source": identity,
                                         "clips": count, "report": str(output / "report.html")})
        print(f"已更新 {count} 条成片字幕，未调用 ASR 或大模型 API。报告：{output / 'report.html'}", flush=True)
        return 0 if not result["failures"] else 2


def resolve_inputs(args, config):
    def resolve(argument, configured):
        if argument is not None:
            return Path(argument).expanduser().resolve() if argument.strip() else None
        return (PROJECT_ROOT / Path(configured).expanduser()).resolve() if configured.strip() else None

    video = resolve(args.video, config["input"]["video"])
    srt = resolve(args.srt, config["input"]["srt"])
    output = resolve(args.output, config["input"]["output_dir"])
    if output is None and video is not None:
        suffix = fingerprint({"path": str(video)})[:8]
        output = PROJECT_ROOT / "outputs" / f"{sanitize_filename(video.stem)}_{suffix}"
    return video, srt, output


def preflight(config, video, srt, prepare_only):
    errors = []
    reactions_path = danmaku_path(config["danmaku"])
    if reactions_path is not None:
        if not reactions_path.is_file():
            errors.append("弹幕文件不存在，请核对 danmaku.path 或 --danmaku")
        elif reactions_path.suffix.lower() not in {".xml", ".ass"}:
            errors.append("弹幕文件只支持 XML/ASS；不能把 MP4 或语音 SRT 当成弹幕输入")
    if config["correction"]["enabled"]:
        try:
            DictionaryCorrector(config["correction"]["dictionary_path"], config["correction"]["ignore_sources"])
        except SelectionError as exc:
            errors.append(str(exc))
    if video is None:
        errors.append("尚未填写录播路径：config/local/highlights.json → input.video")
    elif not video.is_file():
        errors.append("录播文件不存在，请检查 input.video 或 --video")
    if srt is not None and not srt.is_file():
        errors.append("SRT 文件不存在，请清空 input.srt 或填写有效路径")
    for program in ("ffmpeg", "ffprobe"):
        if not shutil.which(program):
            errors.append(f"未找到 {program}，请将 FFmpeg 的 bin 目录加入 Path")
    firered = config["asr"].get("backend", "faster_whisper") == "firered"
    if srt is None and firered:
        from core.firered_transcription import REQUIRED_FILES, model_directory, worker_python
        if not worker_python().is_file():
            errors.append("FireRed 独立环境未安装，请运行 setup_firered.cmd")
        if not (PROJECT_ROOT / "third_party/FireRedASR2S/fireredasr2s").is_dir():
            errors.append("FireRed 官方推理代码未安装，请运行 setup_firered.cmd")
        if any(not (model_directory(config["asr"]) / name).is_file() for name in REQUIRED_FILES):
            errors.append("FireRed 模型文件不完整，请运行 download_firered_aed.py")
    elif srt is None and importlib.util.find_spec("faster_whisper") is None:
        errors.append("本地转写依赖未安装；run_clips.cmd 会自动安装，或手动安装 requirements-asr.txt")
    elif srt is None:
        try:
            if int(importlib.metadata.version("av").split(".")[0]) >= 19:
                errors.append("PyAV 19 与当前转写版本不兼容，请运行 run_clips.cmd 修复依赖，或重装 requirements-asr.txt")
        except importlib.metadata.PackageNotFoundError:
            pass
    if not prepare_only:
        if not config["llm"]["model"].strip():
            errors.append("尚未填写硅基流动模型名：config/local/highlights.json → llm.model")
        name = config["llm"]["api_key_env"]
        if name and not os.environ.get(name):
            errors.append(f"尚未填写 API Key：项目根目录 .env → {name}")
        if not config["llm"]["base_url"].startswith(("https://", "http://")):
            errors.append("llm.base_url 必须是 http(s) 接口地址")
    return errors


def execute_job(video, srt, config, output, model, media, args):
    identity = source_identity(video)
    with job_lock(output / ".job.lock"):
        write_json(output / "job.json", {"status": "preparing", "source": identity})
        try:
            return _execute_locked(video, srt, config, output, model, media, identity, args)
        except BaseException as exc:
            write_json(output / "job.json", {"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                             "source": identity, "reason": str(exc)})
            raise


def _execute_locked(video, srt, config, output, model, media, identity, args):
    reactions = load_danmaku(config["danmaku"], media["duration_ms"])
    reaction_metadata = reactions.metadata if reactions is not None else {
        "status": "not_configured", "reaction_messages": 0, "ocr_used": False}
    write_json(output / "danmaku.signals.json", dict(reaction_metadata,
                                                    signals=reactions.signals if reactions is not None else []))
    if srt is None:
        srt = transcribe(video, identity, config["asr"], output)
    raw_srt = srt
    subtitles = read_subtitles(raw_srt, media["duration_ms"])
    subtitles, correction = correct_subtitles(subtitles, raw_srt, output, config["correction"])
    srt = Path(correction["corrected_srt"])
    text_identity = subtitle_fingerprint(subtitles)
    write_json(output / "transcript.indexed.json", {"source": identity, "srt": str(srt), "raw_srt": str(raw_srt),
                                                   "transcript_fingerprint": text_identity, "correction": correction,
                                                   "subtitles": [asdict(sub) for sub in subtitles]})
    if args.prepare_only:
        cfg = config["selection"]
        windows = selection_windows(subtitles, cfg)
        write_json(output / "windows.json", [{"window": window.id, "subtitles": [asdict(sub) for sub in window.subtitles]}
                                             for window in windows])
        print(f"准备完成，未调用选片 API。字幕：{srt}\n索引与窗口：{output}")
        write_json(output / "job.json", {"status": "prepared", "source": identity})
        return 0
    write_json(output / "job.json", {"status": "analyzing", "source": identity})
    pipeline = HighlightPipeline(model, config["selection"], output, reactions=reactions)
    result = pipeline.run(subtitles, media["duration_ms"])
    result["source"] = identity
    result["srt"] = str(srt)
    result["correction"] = correction
    result["transcript_fingerprint"] = text_identity
    if result["status"] == "incomplete":
        write_json(output / "analysis.json", result)
        write_report(output, video, result)
        write_json(output / "job.json", {"status": "incomplete", "source": identity})
        reasons = list(dict.fromkeys(item["reason"] for item in result["failures"]))
        for reason in reasons[:3]:
            print(f"分析未完成的原因：{reason}", flush=True)
        print(f"转写和成功请求缓存已保留；修复后使用相同配置和输出目录重跑。详情：{output / 'analysis.json'}")
        return 2
    write_json(output / "Data_source.txt", [{"timestamp": clip["timestamp"], "title": clip["title"],
                                           "summary": clip["setup"] + clip["payoff"],
                                           "highlight_reason": clip["reason"], "boundary_locked": True}
                                          for clip in result["clips"]])
    if not args.analyze_only:
        write_json(output / "job.json", {"status": "rendering", "source": identity})
        editorial_block = None
        for clip in result["clips"]:
            editorial_ok = True
            try:
                if editorial_block is not None:
                    raise SelectionError("后续投稿编辑请求已停止：" + str(editorial_block))
                prepare_editorial(video, identity, clip, subtitles, output, config, model)
            except (SelectionError, OSError) as exc:
                if isinstance(exc, ModelAPIError) and exc.blocks_remaining_requests:
                    editorial_block = exc
                editorial_ok = False
                result["failures"].append({"stage": "editorial", "title": clip["title"], "reason": str(exc)})
                print(f"投稿文案未完成：{exc}", flush=True)
            try:
                path = render_clip(video, identity, subtitles, clip, output,
                                   config["render"]["subtitle_style"], config["render"]["encoder"], caption_word_path(result))
                clip["render_status"] = "complete"
                print(f"切片已验证：{path}")
            except (SelectionError, OSError) as exc:
                clip["render_status"] = "failed"
                result["failures"].append({"stage": "render", "title": clip["title"], "reason": str(exc)})
            if editorial_ok and clip.get("render_status") == "complete" and config["publication"]["enabled"]:
                publish_clip(video, identity, clip, output, config, result)
        if result["failures"]:
            result["status"] = ("render_incomplete" if any(item["stage"] == "render" for item in result["failures"])
                                else "publication_incomplete")
    write_json(output / "analysis.json", result)
    write_report(output, video, result)
    write_json(output / "job.json", {"status": result["status"], "source": identity,
                                     "clips": len(result["clips"]), "report": str(output / "report.html")})
    print(f"分析状态：{result['status']}；通过 {len(result['clips'])} 个片段。报告：{output / 'report.html'}")
    return 0 if result["status"] == "complete" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="已有录播 → 本地转写 → 硅基流动选片 → 带字幕切片")
    parser.add_argument("--video", help="录播文件；不传则读取配置 input.video")
    parser.add_argument("--srt", help="可选：对齐的 UTF-8 SRT；不填则本地转写")
    parser.add_argument("--config", default=str(default_config_path(PROJECT_ROOT)))
    parser.add_argument("--output", help="输出目录；不传则按视频自动创建任务目录")
    parser.add_argument("--min-score", type=int, help="本次选片的最低看点分，0–100；默认 85，越高越严格")
    parser.add_argument("--danmaku", help="与录播时间轴对应的 XML/ASS 弹幕文件；用于‘绷’的查找与排序加权")
    parser.add_argument("--check", action="store_true", help="只检查配置、素材和依赖；不下载模型、不调用 API")
    parser.add_argument("--prepare-only", action="store_true", help="只转写及准备窗口，不调用选片 API")
    parser.add_argument("--analyze-only", action="store_true", help="生成选片报告，暂不编码视频")
    parser.add_argument("--package-only", action="store_true", help="仅为已有成片补齐封面和投稿文案，不转写、不请求 API、不编码视频")
    parser.add_argument("--restyle", action="store_true", help="复用已有选片和字幕，重做投稿标题/封面并沿用当前字幕样式；未复核的文案调用 API，不重新转写或选片")
    parser.add_argument("--captions-only", action="store_true", help="仅更新已有成片字幕；不转写、不调用大模型 API、不改标题封面")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.danmaku is not None:
            if args.package_only or args.restyle or args.captions_only:
                raise SelectionError("--danmaku 需要重新选片，不能与仅打包、改文案或改字幕模式同时使用")
            config["danmaku"]["path"] = str(Path(args.danmaku).expanduser().resolve()) if args.danmaku.strip() else ""
        if args.min_score is not None:
            config["selection"]["min_score"] = validate_min_score(args.min_score)
            if args.package_only or args.restyle or args.captions_only or args.prepare_only:
                raise SelectionError("--min-score 需要重新选片，不能与仅打包、改文案、改字幕或转写模式同时使用；可搭配 --analyze-only")
        if args.captions_only:
            if any((args.check, args.prepare_only, args.analyze_only, args.package_only, args.restyle)):
                raise SelectionError("--captions-only 不能与其他阶段选项同时使用")
            _, _, output = resolve_inputs(args, config)
            video = Path(args.video).expanduser().resolve() if args.video else None
            return refresh_captions(video, output, config)
        if args.package_only or args.restyle:
            if args.check or args.prepare_only or args.analyze_only or (args.package_only and args.restyle):
                raise SelectionError("--package-only 不能和其他仅处理某阶段的选项同时使用")
            video, _, output = resolve_inputs(args, config)
            return package_existing(video, output, config, restyle=args.restyle)
        load_local_key(config["llm"]["api_key_env"], PROJECT_ROOT / ".env")
        video, srt, output = resolve_inputs(args, config)
        errors = preflight(config, video, srt, args.prepare_only)
        if errors:
            raise SelectionError("请补充以下配置或依赖：\n- " + "\n- ".join(errors))
        media = probe_video(video)
        if srt is None and not media["has_audio"]:
            raise SelectionError("录播没有音轨，无法本地转写")
        if args.check:
            print(f"前置检查通过。输出目录：{output}\n未下载模型、未调用 API；GPU 实际运行能力将在转写时验证。")
            return 0
        model = None if args.prepare_only else ChatModel(config["llm"])
        return execute_job(video, srt, config, output, model, media, args)
    except KeyboardInterrupt:
        print("任务已中断，完成的阶段缓存保留；重新运行可继续。", file=sys.stderr)
        return 130
    except (SelectionError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
