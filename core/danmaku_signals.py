"""Timed audience context and reactions as hints, never spoken evidence."""
import bisect
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.paths import PROJECT_ROOT
from core.highlight_selection import SelectionError, fingerprint


DEFAULT_DANMAKU = {"path": "", "offset_seconds": 0, "lookback_seconds": 8,
                   "tail_seconds": 2, "max_priority_bonus": 5}
BUCKET_MS = 10000
POLICY_VERSION = 2
CONTEXT_LIMIT = 24
CONTEXT_TEXT_LIMIT = 120


def validate_danmaku_config(config):
    if not isinstance(config["path"], str):
        raise SelectionError("danmaku.path 必须为 XML/ASS 路径字符串")
    value = config["offset_seconds"]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SelectionError("danmaku.offset_seconds 必须为有限秒数，可为负数")
    for key in ("lookback_seconds", "tail_seconds", "max_priority_bonus"):
        value = config[key]
        limit = 60 if key != "max_priority_bonus" else 10
        if type(value) is not int or not 0 <= value <= limit:
            raise SelectionError(f"danmaku.{key} 必须是 0–{limit} 的整数")


def danmaku_path(config):
    return (PROJECT_ROOT / Path(config["path"]).expanduser()).resolve() if config["path"].strip() else None


def _seconds_ms(text):
    try:
        value = Decimal(text)
        if not value.is_finite() or value < 0:
            raise ValueError
        return round(value * 1000)
    except (InvalidOperation, ValueError, OverflowError):
        raise SelectionError("弹幕包含无效时间，未猜测或归零") from None


def _ass_time(text):
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text.strip())
    if not match or int(match[2]) >= 60 or Decimal(match[3]) >= 60:
        raise SelectionError("ASS 弹幕时间格式错误")
    return int(match[1])*3600000 + int(match[2])*60000 + _seconds_ms(match[3])


def _messages(path, raw):
    if path.suffix.lower() == ".xml":
        try:
            xml_text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise SelectionError("弹幕 XML 需为 UTF-8 编码") from None
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, re.I):
            raise SelectionError("弹幕 XML 不支持 DTD 或实体声明")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            raise SelectionError("弹幕 XML 格式错误") from None
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "d":
                continue
            parts = node.attrib.get("p", "").split(",")
            if len(parts) < 1 or not parts[0].strip():
                raise SelectionError("弹幕 XML 的 d 元素缺少 p 时间字段")
            uid = parts[6] if len(parts) > 6 and parts[6] not in ("", "0") else ""
            mid = parts[7] if len(parts) > 7 and parts[7] not in ("", "0") else ""
            yield _seconds_ms(parts[0]), "".join(node.itertext()), uid, mid
    elif path.suffix.lower() == ".ass":
        fields, events = None, False
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise SelectionError("弹幕 ASS 需为 UTF-8 编码") from None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("["):
                events = line.casefold() == "[events]"
            if not events:
                continue
            if line.lower().startswith("format:"):
                fields = [x.strip().lower() for x in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("dialogue:"):
                if not fields or "start" not in fields or fields[-1] != "text":
                    raise SelectionError("弹幕 ASS 的 Events Format 需含 Start，且 Text 在最后")
                values = line.split(":", 1)[1].lstrip().split(",", len(fields)-1)
                if len(values) != len(fields):
                    raise SelectionError("弹幕 ASS Dialogue 字段数量错误")
                clean = re.sub(r"\{[^}]*\}", "", values[-1]).replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", " ")
                yield _ass_time(values[fields.index("start")]), clean, "", ""
    else:
        raise SelectionError("danmaku.path 目前只支持 XML 或 ASS 弹幕文件")


def is_beng_reaction(text):
    # Do not interpret an ordinary reference to a bandage or tense muscles as
    # laughter. One message with many repeated characters still counts once.
    return "绷" in re.sub(r"绷带|紧绷|绷紧|绷直|绷断|绷弦|绷脸", "", text)


class DanmakuTimeline:
    def __init__(self, path, duration_ms, config=None):
        self.config = dict(DEFAULT_DANMAKU, **(config or {}))
        validate_danmaku_config(self.config)
        self.duration_ms = duration_ms
        if Path(path).stat().st_size > 128*1024*1024:
            raise SelectionError("弹幕文件超过128 MiB，请先按当前录播分段导出")
        raw = Path(path).read_bytes()
        self.times, self.comments, seen, seen_comments, in_range, out_of_range = [], [], set(), set(), 0, 0
        offset_ms = round(self.config["offset_seconds"]*1000)
        for time_ms, text, uid, mid in _messages(Path(path), raw):
            time_ms += offset_ms
            if not 0 <= time_ms < duration_ms:
                out_of_range += 1
                continue
            in_range += 1
            context_text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text)).strip()
            comment_key = ("id", mid) if mid else ("event", time_ms, context_text)
            if context_text and comment_key not in seen_comments:
                seen_comments.add(comment_key)
                self.comments.append({"time_ms": time_ms, "text": context_text[:CONTEXT_TEXT_LIMIT]})
            if not is_beng_reaction(text):
                continue
            keys = [("id", mid)] if mid else []
            if uid:
                keys.append(("user_bucket", uid, time_ms//BUCKET_MS))
            if not keys:
                keys = [("event", time_ms, text)]
            if any(key in seen for key in keys):
                continue
            seen.update(keys)
            self.times.append(time_ms)
        if out_of_range and not in_range:
            raise SelectionError("没有落在本视频时间范围内的弹幕；请核对文件与 danmaku.offset_seconds")
        self.times.sort()
        self.comments.sort(key=lambda comment: comment["time_ms"])
        self.comment_times = [comment["time_ms"] for comment in self.comments]
        bins = defaultdict(list)
        for t in self.times:
            bins[t//BUCKET_MS].append(t)
        self.signals = []
        for times in bins.values():
            self.signals.append({"keyword": "绷", "reaction_count": len(times),
                                 "reaction_start_ms": min(times), "reaction_end_ms": max(times),
                                 "review_start_ms": max(0, min(times)-self.config["lookback_seconds"]*1000),
                                 "review_end_ms": min(duration_ms, max(times)+self.config["tail_seconds"]*1000)})
        self.identity = fingerprint({"version": POLICY_VERSION, "sha256": hashlib.sha256(raw).hexdigest(),
                                     "offset_ms": offset_ms, "lookback_seconds": self.config["lookback_seconds"],
                                     "tail_seconds": self.config["tail_seconds"]})
        self.metadata = {"status": "loaded", "path": str(Path(path).resolve()),
                         "sha256": hashlib.sha256(raw).hexdigest(), "fingerprint": self.identity,
                         "offset_seconds": self.config["offset_seconds"], "in_range_messages": in_range,
                         "out_of_range_messages": out_of_range, "reaction_messages": len(self.times),
                         "reaction_buckets": len(self.signals), "max_priority_bonus": self.config["max_priority_bonus"],
                         "keyword": "绷", "bucket_seconds": BUCKET_MS//1000,
                         "count_unit": "deduplicated_messages_not_unique_viewers", "ocr_used": False}
        self.metadata.update(context_messages=len(self.comments), context_limit_per_window=CONTEXT_LIMIT,
                             context_text_limit=CONTEXT_TEXT_LIMIT, context_source="viewer_comments_unverified",
                             policy_version=POLICY_VERSION)

    def for_window(self, start_ms, end_ms):
        found = [s for s in self.signals if s["review_start_ms"] <= end_ms and s["review_end_ms"] >= start_ms]
        found = sorted(found, key=lambda s: s["reaction_count"], reverse=True)[:64]
        return sorted(found, key=lambda s: s["reaction_start_ms"])

    def priority(self, start_ms, end_ms):
        lo = bisect.bisect_left(self.times, start_ms-self.config["tail_seconds"]*1000)
        hi = bisect.bisect_right(self.times, end_ms+self.config["lookback_seconds"]*1000)
        counts = Counter(t//BUCKET_MS for t in self.times[lo:hi])
        peak = max(counts.values(), default=0)
        return {"reaction_messages": hi-lo, "peak_bucket_count": peak,
                "bonus": min(self.config["max_priority_bonus"], peak), "keyword": "绷"}

    def context_for_window(self, start_ms, end_ms):
        """Bound the API payload; omit IDs and collapse repeated text within a window."""
        lo = bisect.bisect_left(self.comment_times, start_ms-self.config["tail_seconds"]*1000)
        hi = bisect.bisect_right(self.comment_times, end_ms+self.config["lookback_seconds"]*1000)
        unique, texts = [], set()
        for comment in self.comments[lo:hi]:
            if comment["text"] not in texts:
                texts.add(comment["text"])
                unique.append(comment)
        selected = unique
        if len(unique) > CONTEXT_LIMIT:
            selected = [unique[round(i*(len(unique)-1)/(CONTEXT_LIMIT-1))] for i in range(CONTEXT_LIMIT)]
        return {"source": "viewer_comments_unverified", "is_spoken_evidence": False,
                "sampling": "unique_text_evenly_sampled", "available_messages": hi-lo,
                "unique_texts": len(unique), "comments": selected}


def load_danmaku(config, duration_ms):
    validate_danmaku_config(config)
    path = danmaku_path(config)
    return DanmakuTimeline(path, duration_ms, config) if path is not None else None
