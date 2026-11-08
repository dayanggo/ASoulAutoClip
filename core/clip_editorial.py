"""An independently cached publication pass; selection and ASR stay reusable."""

import json
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path

from core.cover_copy import validate_cover_copy
from core.highlight_selection import SelectionError, fingerprint, write_json


ROOT = Path(__file__).resolve().parents[1]
NAMES = ("嘉然", "贝拉", "乃琳", "心宜", "思诺")


def plain(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text)
                   if not unicodedata.category(c).startswith(("P", "Z")) and not c.isspace()).casefold()


def title_quotes(text):
    pairs = (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"))
    return [quote for left, right in pairs
            for quote in re.findall(re.escape(left) + "([^" + re.escape(left + right) + "]+)" + re.escape(right), text)]


def quote_supported(quote, ids, by_id):
    """Only exact source text, optionally across consecutive cues within 400ms."""
    needle = plain(quote)
    if not needle:
        return False
    allowed = set(ids)
    for sid in sorted(allowed):
        source = by_id[sid].text
        if needle in plain(source):
            return True
        next_id = sid + 1
        while next_id in allowed and 0 <= by_id[next_id].start_ms - by_id[next_id-1].end_ms <= 400:
            source += by_id[next_id].text
            if needle in plain(source):
                return True
            next_id += 1
    return False


def validate_editorial(raw, subtitles, require_cover_text=False):
    by_id = {s.id: s for s in subtitles}
    if not isinstance(raw, dict):
        raise SelectionError("投稿编辑结果必须是对象")
    titles = raw.get("titles")
    if not isinstance(titles, list) or not 1 <= len(titles) <= 3:
        raise SelectionError("投稿标题候选需为 1–3 条")
    # Do not mutate model caches or caller-owned review annotations during repair.
    import copy
    raw = copy.deepcopy(raw)
    titles = raw["titles"]
    valid_titles, title_errors = [], []
    for item in titles:
        if not isinstance(item, dict):
            raise SelectionError("投稿标题需为对象")
        text, ids = item.get("text"), item.get("support_ids")
        if not isinstance(text, str) or not 6 <= len(text) <= 80 or "\n" in text:
            raise SelectionError("投稿标题长度或格式无效")
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in by_id for i in ids):
            raise SelectionError("投稿标题引用了片段外的字幕")
        quoted_ok = True
        for quote in title_quotes(text):
            if not quote_supported(quote, ids, by_id):
                quoted_ok = False
                title_errors.append(f"引语 {quote!r} 不在所引字幕中，原文=" + repr([by_id[i].text for i in ids]))
        if quoted_ok:
            valid_titles.append(item)
    if not valid_titles:
        raise SelectionError("投稿标题中的引语与证据不一致：" + "；".join(title_errors))
    lines = raw.get("cover_lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 3:
        raise SelectionError("封面原话需为 1–3 行")
    if any(not isinstance(x, dict) for x in lines):
        raise SelectionError("封面原话需为对象")
    if sum(len(x.get("text", "")) for x in lines if isinstance(x.get("text"), str)) > 36:
        raise SelectionError("封面文案过长")
    for item in lines:
        kind = item.get("kind", "quote")
        if kind not in {"quote", "caption"}:
            raise SelectionError("封面文案 kind 只支持 quote 或 caption")
        item["kind"] = kind
        text = item.get("text")
        if kind == "caption":
            ids = item.get("support_ids")
            if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in by_id for i in ids):
                raise SelectionError("封面概括需引用片内 support_ids")
            if not isinstance(text, str) or not 2 <= len(text) <= 16 or "\n" in text or not plain(text):
                raise SelectionError("封面每行需为 2–16 字")
            if any(c in text for c in '“”‘’「」『』"\''):
                raise SelectionError("封面概括不能使用引号冒充原话；请使用 quote 类型")
            item.pop("quote", None)
            item.pop("subtitle_id", None)
            item["subtitle_ids"] = list(dict.fromkeys(ids))
            item["support_quotes"] = [by_id[i].text for i in item["subtitle_ids"]]
            continue
        sid, quote = item.get("subtitle_id"), item.get("quote")
        if type(sid) is not int or sid not in by_id:
            raise SelectionError("封面引语不属于片段")
        if not isinstance(text, str) or not 2 <= len(text) <= 16 or "\n" in text or not plain(text):
            raise SelectionError("封面每行需为 2–16 字")
        source_text, source_ids = by_id[sid].text, [sid]
        if plain(text) not in plain(source_text):
            # ASR may split one utterance at the length limit. A joined citation
            # must name both consecutive cues and cannot cross a real pause.
            nxt = by_id.get(sid + 1)
            if nxt and 0 <= nxt.start_ms - by_id[sid].end_ms <= 400 and plain(text) in plain(source_text + nxt.text):
                source_text += nxt.text
                source_ids.append(nxt.id)
            elif isinstance(quote, str) and 2 <= len(quote) <= 12 and plain(quote) and plain(quote) in plain(source_text):
                # Use the verified short quote instead of a model's paraphrase.
                item["text"] = quote
                text = quote
        if plain(text) not in plain(source_text):
            raise SelectionError(f"封面文案必须摘自所引用的字幕原话：字幕 {sid} 原文={by_id[sid].text!r}；"
                                 f"收到 quote={quote!r}, text={text!r}。请逐字复制，不能补标点或订正 ASR 到 quote。")
        # Python owns the citation, just as it owns the timestamps. Never retain a
        # model's miscopied quote when the chosen text can be verified directly.
        item["quote"] = source_text
        item["subtitle_ids"] = source_ids
    cover_text = validate_cover_copy([item["text"] for item in lines], raw.get("cover_text"), require_cover_text)
    frame = raw.get("frame_subtitle_id")
    focus = raw.get("focus_subtitle_ids")
    if type(frame) is not int or frame not in by_id:
        raise SelectionError("封面取帧字幕越界")
    if not isinstance(focus, list) or not 1 <= len(focus) <= 3 or any(type(i) is not int or i not in by_id for i in focus):
        raise SelectionError("强调字幕需引用片内 1–3 句")
    phrases = raw.get("focus_phrases", [])
    if not isinstance(phrases, list) or len(phrases) > 3:
        raise SelectionError("重点短语最多 3 条")
    for phrase in phrases:
        if not isinstance(phrase, dict):
            raise SelectionError("重点短语需为对象")
        sid, text = phrase.get("subtitle_id"), phrase.get("text")
        # Exact substrings keep caption color markup separate from ASR correction.
        if type(sid) is not int or sid not in focus or not isinstance(text, str) or not 2 <= len(text) <= 16:
            raise SelectionError("重点短语需引用强调字幕中的 2–16 字原话")
        if text.casefold() not in by_id[sid].text.casefold():
            raise SelectionError("重点短语必须逐字摘自字幕")
    # A transcript saying something funny does not establish a visible reaction.
    # This narrow filter is a guard, not a semantic verification model.
    visual_claims = ("笑场", "逗乐", "拍桌", "捂脸", "瞪大", "表情", "手却", "红温", "破防", "落泪",
                     "懵了", "全场问号", "敷衍了事", "点头", "摇头", "闭眼", "捂耳")
    all_text = "".join(s.text for s in subtitles)
    unsupported = visual_claims + ("抢疯", "全场沸腾", "全网", "史上最", "封神")
    grounded = [t for t in valid_titles if not any(term in t["text"] and term not in all_text for term in unsupported)]
    if not grounded:
        raise SelectionError("标题声称了字幕无法证明的动作或情绪，请改用原话和具体事件")
    if any(term in item["text"] and term not in all_text for item in lines for term in unsupported):
        raise SelectionError("封面声称了字幕无法证明的动作、情绪或规模")
    result = {"titles": grounded, "cover_text": cover_text, "cover_lines": lines, "frame_subtitle_id": frame,
              "focus_subtitle_ids": list(dict.fromkeys(focus))}
    if phrases:
        result["focus_phrases"] = phrases
    return result


def with_editorial_timing(result, clip, subtitles):
    by_id = {s.id: s for s in subtitles}
    result["frame_ms"] = max(clip["start_ms"], min(clip["end_ms"] - 1,
        (by_id[result["frame_subtitle_id"]].start_ms + by_id[result["frame_subtitle_id"]].end_ms) // 2))
    result["focus_cues"] = [asdict(by_id[i]) for i in result["focus_subtitle_ids"]]
    return result


def edit_clip(model, video, clip, subtitles, output, override=None, copy_review_enabled=True,
              recording_brief=""):
    inside = [s for s in subtitles if s.start_ms < clip["end_ms"] and s.end_ms > clip["start_ms"]]
    if override is not None:
        result = with_editorial_timing(validate_editorial(override, inside), clip, inside)
        result["origin"] = "local_review"
        return result
    prompt = (ROOT / "resources/prompts/clip_editorial_zh.txt").read_text(encoding="utf-8")
    review_prompt = (ROOT / "resources/prompts/clip_copy_review_zh.txt").read_text(encoding="utf-8") if copy_review_enabled else ""
    # A name may be mentioned in a group conversation even when the filename
    # contains no member names. This permits mentions, not speaker attribution.
    name_context = Path(video).stem + "\n" + recording_brief + "\n" + "\n".join(s.text for s in inside)
    payload = {"task": "publication_editorial", "recording": Path(video).stem,
               "recording_brief": recording_brief,
               "allowed_names": [n for n in NAMES if n in name_context],
               "speaker_attribution": "unavailable",
               "selection_label": clip["title"], "evidence": clip["evidence"],
               "subtitles": [asdict(s) for s in inside]}
    identity = fingerprint({"version": 2, "prompt": prompt, "review_prompt": review_prompt,
                            "payload": payload, "model": model.cache_identity})
    cache = Path(output) / "cache/editorial" / (identity + ".json")
    if cache.is_file():
        try:
            result = with_editorial_timing(validate_editorial(json.loads(cache.read_text(encoding="utf-8")), inside,
                                                           require_cover_text=True), clip, inside)
            if copy_review_enabled:
                result["copy_review"] = {"method": "independent_model_pass", "version": 1}
            return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
    draft_cache = cache.with_suffix(".draft.json")
    result = None
    if draft_cache.is_file():
        try:
            result = validate_editorial(json.loads(draft_cache.read_text(encoding="utf-8")), inside,
                                        require_cover_text=True)
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if result is None:
        last = None
        for _ in range(2):
            raw = model.complete(prompt, dict(payload, correction=str(last)) if last else payload)
            try:
                result = validate_editorial(raw, inside, require_cover_text=True)
                write_json(draft_cache, result)
                break
            except SelectionError as exc:
                last = exc
                write_json(cache.with_suffix(".failed.json"), {"stage": "draft", "result": raw, "error": str(exc)})
        else:
            raise SelectionError("投稿文案校验失败：" + str(last))
    if copy_review_enabled:
        last = None
        for _ in range(2):
            review_payload = dict(payload, task="publication_copy_review", draft=result)
            if last:
                review_payload["correction"] = str(last)
            raw = model.complete(review_prompt, review_payload)
            try:
                reviewed = validate_editorial(raw, inside, require_cover_text=True)
                # Review prose, not the subtitle or visual anchor annotations.
                result = dict(result, titles=reviewed["titles"], cover_text=reviewed["cover_text"],
                              cover_lines=reviewed["cover_lines"])
                break
            except SelectionError as exc:
                last = exc
                write_json(cache.with_suffix(".review.failed.json"), {"stage": "copy_review", "result": raw, "error": str(exc)})
        else:
            raise SelectionError("投稿文案通顺性复核未通过：" + str(last))
        result["copy_review"] = {"method": "independent_model_pass", "version": 1}
    result = with_editorial_timing(result, clip, inside)
    write_json(cache, result)
    return result
