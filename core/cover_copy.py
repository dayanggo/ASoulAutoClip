"""Cheap structural checks; fluency and reference resolution need editorial review."""

import re
import unicodedata

from core.highlight_selection import SelectionError


def compact(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text)
                   if not c.isspace() and not unicodedata.category(c).startswith(("P", "Z"))).casefold()


def validate_cover_copy(lines, cover_text=None, required=False):
    """Ensure layout preserves the complete copy; reject obvious dangling tails.

    Lines may split a sentence. Check their combined meaning, not every line as
    an independent sentence. These deliberately narrow rules are not an NLU model.
    """
    if (not isinstance(lines, list) or not 1 <= len(lines) <= 3 or
            any(not isinstance(line, str) or not 2 <= len(line) <= 16 or "\n" in line
                or not compact(line) for line in lines) or sum(map(len, lines)) > 36):
        raise SelectionError("封面需为 1–3 行，每行 2–16 字，总计不超过 36 字")
    if required and cover_text is None:
        raise SelectionError("请先提供完整、通顺的 cover_text，再按语义拆成 cover_lines")
    if cover_text is not None:
        if (not isinstance(cover_text, str) or not 2 <= len(cover_text) <= 80 or
                "\n" in cover_text or compact(cover_text) != compact("".join(lines))):
            raise SelectionError("cover_text 与封面分行内容不一致；分行不得删词、补词或改写完整文案")
    text = cover_text if cover_text is not None else "，".join(lines)
    combined = compact("".join(lines))
    if re.search(r"(?:因为|所以|但是|不过|如果|要是|虽然|为了|关于|对于|至于|以及|然后|原来是|我觉得|也太|有点)$", combined):
        raise SelectionError("封面文案没有说完：结尾缺少必要内容，请写完整再分行")
    reply = compact(lines[-1])
    if re.fullmatch(r"(?:确实|的确|真的|还真|真|还|也|好|挺|很|更|太|有点|不|并|一点|完全)*像(?:啊|呀|哦|呢|了)?", reply):
        context = compact("".join(lines[:-1]))
        # "像心跳吗？/确实很像" is a complete exchange. A generic "像什么？"
        # leaves the object unresolved even though the last line is a real quote.
        has_object = re.search(r"像(?!什么|啥|哪|不像|好像|吗|啊|呀|呢|哦|了)[\w\u4e00-\u9fff]+", context)
        if not has_object:
            raise SelectionError("封面里的“像”缺少具体对象；“像什么／确实也像”不能独立传达看点，请用带证据的完整概括")
    return text


def fallback_cover_lines(title):
    """Preserve the full selection label instead of truncating arbitrary quotes."""
    if not isinstance(title, str) or not 2 <= len(title) <= 36:
        raise SelectionError("没有合格的短封面文案，请启用投稿编辑或提供不超过 36 字的完整选片标题")
    import jieba
    lines, current = [], ""
    for token in jieba.cut(title):
        if current and len(current + token) > 16:
            lines.append(current)
            current = ""
        current += token
    if current:
        lines.append(current)
    validate_cover_copy(lines, title)
    return lines
