"""Readable display captions, using existing word times without changing evidence."""

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from dataclasses import replace

from core.highlight_selection import SelectionError, Subtitle, fingerprint


@dataclass(frozen=True)
class TimedText:
    text: str
    start_ms: int
    end_ms: int
    source_ids: tuple


@lru_cache(maxsize=1)
def tokenizer():
    try:
        import jieba
    except ImportError:
        raise SelectionError("短句字幕需要 jieba，请使用项目虚拟环境安装 requirements.txt") from None
    jieba.setLogLevel(logging.WARNING)
    instance = jieba.Tokenizer()
    for term in ("嘉然", "小然", "然总", "乃琳", "贝拉", "拉姐", "直播间", "早睡早起", "新技能", "锅包肉"):
        instance.add_word(term, freq=100000)
    return instance


def compact(text):
    return re.sub(r"\s+", "", text)


def load_word_records(path, source):
    """Only use source-bound, non-legacy word data; never guess missing times."""
    if path is None or not Path(path).is_file():
        return {}, {"status": "unavailable", "reason": "没有可验证的词时间，保留原字幕时间"}
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        receipt = json.loads(path.with_name("receipt.json").read_text(encoding="utf-8"))
        if receipt.get("source") != source:
            raise ValueError("词时间所属录播不匹配")
        if data.get("runtime", {}).get("backend") == "firered" and data.get("timestamp_policy") != 2:
            raise ValueError("旧 FireRed 词时间未经严格对齐")
        records = {}
        for record in data["segments"]:
            key = (record["start_ms"], record["end_ms"])
            if key in records:
                raise ValueError("重复的词时间区间")
            records[key] = record
        return records, {"status": "verified", "path": str(path.resolve()),
                         "fingerprint": fingerprint(data), "source": data.get("timestamp_source", "asr_words")}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {}, {"status": "unavailable", "reason": str(exc)}


def aligned_units(sub, record):
    """Map approved text corrections onto original spans, keeping replacements atomic."""
    words = record.get("words", [])
    if not words:
        return None
    original, spans = "", []
    previous = sub.start_ms
    for word in words:
        start, end = word.get("start_ms"), word.get("end_ms")
        if (type(start) is not int or type(end) is not int or
                not sub.start_ms <= start < end <= sub.end_ms or start < previous):
            return None
        text = compact(word.get("text", ""))
        original += text
        spans.extend([(start, end)] * len(text))
        previous = end
    if compact(record.get("text", "")) != original or not original:
        return None
    display = compact(sub.text)
    mapped = [None] * len(display)
    matcher = SequenceMatcher(None, original, display, autojunk=False)
    # Large unrelated edits cannot inherit a previous recording's word timings.
    if matcher.ratio() < .55:
        return None
    for tag, lo, hi, left, right in matcher.get_opcodes():
        if tag == "equal":
            mapped[left:right] = spans[lo:hi]
        elif tag in {"insert", "replace"}:
            span = (spans[lo][0], spans[hi-1][1]) if hi > lo else spans[min(lo, len(spans)-1)]
            mapped[left:right] = [span] * (right-left)
    if any(span is None for span in mapped):
        return None
    # Reinsert original display whitespace without manufacturing a new timestamp.
    units, cursor = [], 0
    for char in sub.text.replace("\r", "").replace("\n", ""):
        if char.isspace():
            span = mapped[max(0, cursor-1)] if mapped else (sub.start_ms, sub.end_ms)
        else:
            span = mapped[cursor]
            cursor += 1
        if units and (units[-1].start_ms, units[-1].end_ms) == span:
            old = units[-1]
            units[-1] = TimedText(old.text + char, *span, (sub.id,))
        else:
            units.append(TimedText(char, *span, (sub.id,)))
    return units


def split_run(units, max_chars, pause_ms):
    """Choose short cards at word/phrase boundaries; all cut times are source times."""
    text = "".join(unit.text for unit in units)
    offsets, cursor = [0], 0
    for unit in units:
        cursor += len(unit.text)
        offsets.append(cursor)
    word_ends = {end for _, _, end in tokenizer().tokenize(text, HMM=False)}
    word_at = {start: token for token, start, _ in tokenizer().tokenize(text, HMM=False)}
    word_ends.add(len(text))
    allowed = {i for i in range(1, len(units)+1) if offsets[i] in word_ends}
    best = {0: (0.0, [])}
    for end in sorted(allowed):
        for start in range(end-1, -1, -1):
            if start not in best:
                continue
            phrase = text[offsets[start]:offsets[end]]
            length = len(phrase.strip())
            # A long original ASR word/correction stays atomic, rather than receiving fake times.
            if length > max_chars and end-start > 1:
                continue
            duration = units[end-1].end_ms - units[start].start_ms
            cost = 10 + ((length-10)/4)**2
            if length < 4:
                cost += (4-length)*2
            if duration < 450:
                cost += (450-duration)/60
            if duration > 3400:
                cost += (duration-3400)/170
            if start and phrase[:1] in "的地得了着过吗呢吧呀们么":
                cost += 12
            if end < len(units):
                after = text[offsets[end]:]
                before = phrase.rstrip()
                gap = units[end].start_ms - units[end-1].end_ms
                if before[-1:] in "，。！？；、,.!?;":
                    cost -= 13
                if gap >= pause_ms:
                    cost -= min(10, 5 + gap/150)
                if re.match(r"(?:但是|不过|然后|所以|因为|还有|而且|你们|我们|我觉得|我看|这也|这都|现在|每天|加|那就)", after):
                    cost -= 7
                if word_at.get(offsets[end]) == "加":
                    cost -= 6
                if before.endswith(("一下", "知道吗", "是不是", "好不好")):
                    cost -= 12 if length >= 4 else 6
                if before[-1:] in "的地得和跟给把被在让对从比很更最不就也才还都但而与及或加":
                    cost += 16
                if before.endswith(("应该", "可以", "可能", "不会", "不能", "需要", "想要", "为了", "因为", "如果")):
                    cost += 12
            value = best[start][0] + cost
            if end not in best or value < best[end][0]:
                best[end] = (value, best[start][1] + [(start, end)])
    ranges = best.get(len(units), (0, [(0, len(units))]))[1]
    return [TimedText("".join(u.text for u in units[a:b]).strip(), units[a].start_ms, units[b-1].end_ms,
                      tuple(dict.fromkeys(sid for u in units[a:b] for sid in u.source_ids))) for a, b in ranges]


def layout_captions(subtitles, style, source, word_timing_path=None):
    mode = style.get("caption_mode", "cue")
    if mode not in {"cue", "phrases"}:
        raise SelectionError("subtitle_style.caption_mode 支持 cue、phrases")
    if mode == "cue":
        return subtitles, {"version": 1, "mode": mode, "fallback_cue_ids": []}
    max_chars, pause = style.get("max_phrase_chars", 14), style.get("phrase_pause_ms", 180)
    hold = style.get("caption_hold_ms", 100)
    if type(max_chars) is not int or not 6 <= max_chars <= 24:
        raise SelectionError("subtitle_style.max_phrase_chars 需为 6–24")
    if type(pause) is not int or not 80 <= pause <= 1000:
        raise SelectionError("subtitle_style.phrase_pause_ms 需为 80–1000")
    if type(hold) is not int or not 0 <= hold <= 300:
        raise SelectionError("subtitle_style.caption_hold_ms 需为 0–300")
    records, provenance = load_word_records(word_timing_path, source)
    cards, run, fallback = [], [], []

    def flush():
        if run:
            cards.extend(split_run(run, max_chars, pause))
            run.clear()

    for sub in subtitles:
        record = records.get((sub.start_ms, sub.end_ms))
        timed_sub = sub
        if record is None and records:
            # Existing ASS stores centiseconds; recover the matching original span.
            nearby = [r for (start, end), r in records.items()
                      if abs(start-sub.start_ms) <= 10 and abs(end-sub.end_ms) <= 10]
            if len(nearby) == 1:
                record = nearby[0]
                timed_sub = replace(sub, start_ms=record["start_ms"], end_ms=record["end_ms"])
            else:
                # An already split ASS card can cover a verified subset of a raw cue.
                words = [word for r in records.values()
                         if r['start_ms'] < sub.end_ms and r['end_ms'] > sub.start_ms
                         for word in r.get('words', [])
                         if word.get('start_ms', -100) >= sub.start_ms-10 and
                         word.get('end_ms', float('inf')) <= sub.end_ms+10]
                text = ''.join(word.get('text', '') for word in words)
                if (words and abs(words[0]['start_ms']-sub.start_ms) <= 10 and
                        compact(text).casefold() == compact(sub.text).casefold()):
                    record = {'start_ms': words[0]['start_ms'], 'end_ms': words[-1]['end_ms'],
                              'text': text, 'words': words}
                    timed_sub = replace(sub, start_ms=record['start_ms'], end_ms=record['end_ms'])
        units = aligned_units(timed_sub, record) if record is not None else None
        if units is None:
            flush()
            cards.append(TimedText(sub.text, sub.start_ms, sub.end_ms, (sub.id,)))
            fallback.append(sub.id)
            continue
        if run and units[0].start_ms - run[-1].end_ms > 120:
            flush()
        run.extend(units)
    flush()
    captions, events = [], []
    for i, card in enumerate(cards):
        end = card.end_ms + (0 if set(card.source_ids).intersection(fallback) else hold)
        if i + 1 < len(cards):
            end = min(end, cards[i+1].start_ms)
        end = max(card.end_ms, end)
        sub = Subtitle(i+1, card.start_ms, end, card.text)
        captions.append(sub)
        events.append({"id": sub.id, "start_ms": sub.start_ms, "end_ms": sub.end_ms,
                       "text": sub.text, "source_ids": list(card.source_ids)})
    return captions, {"version": 1, "mode": mode, "word_timing": provenance,
                      "fallback_cue_ids": fallback, "events": events}


def find_word_timing_path(srt_path):
    """Locate matching cached words for a raw or corrected SRT, without a global scan."""
    if not srt_path:
        return None
    srt = Path(srt_path).resolve()
    direct = srt.with_name("transcript.words.json")
    if direct.is_file():
        return direct
    for parent in list(srt.parents)[:4]:
        index = parent / "transcript.indexed.json"
        if not index.is_file():
            continue
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            if data.get("srt") and Path(data["srt"]).resolve() == srt:
                raw = data.get("raw_srt") or data.get("correction", {}).get("raw_srt")
                candidate = Path(raw).with_name("transcript.words.json") if raw else None
                if candidate is not None and candidate.is_file():
                    return candidate
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
