"""Subtitle-backed selection. The model chooses IDs; Python owns the clock."""

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from core.paths import PROMPTS_DIR


DEFAULT_MIN_SCORE = 85


class SelectionError(ValueError):
    pass


class CandidateDurationError(SelectionError):
    """A candidate violates the duration policy; it must never be rendered."""


class DurationRejectedWindow(SelectionError):
    def __init__(self, rejections):
        self.rejections = rejections
        super().__init__("本窗口候选全部超出时长范围：" + "; ".join(r["reason"] for r in rejections))


class ModelAPIError(SelectionError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code

    @property
    def blocks_remaining_requests(self):
        return self.status_code in {401, 402, 403, 404}


@dataclass(frozen=True)
class Subtitle:
    id: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Window:
    id: int
    subtitles: tuple


def fingerprint(value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def format_time(milliseconds, separator="."):
    milliseconds = max(0, int(milliseconds))
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def _parse_time(text):
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", text.strip())
    if not match:
        raise SelectionError(f"无效字幕时间：{text}")
    hours, minutes, seconds = map(int, match.groups()[:3])
    if minutes >= 60 or seconds >= 60:
        raise SelectionError(f"无效字幕时间：{text}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(match[4].ljust(3, "0"))


def read_subtitles(path, duration_ms):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    result = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        if len(lines) < 3 or not lines[0].strip().isdigit() or "-->" not in lines[1]:
            raise SelectionError(f"SRT 第 {len(result) + 1} 段格式错误，未静默跳过")
        start, end = lines[1].split("-->", 1)
        start_ms, end_ms = _parse_time(start), _parse_time(end)
        content = "\n".join(lines[2:]).strip()
        if not content or end_ms <= start_ms or start_ms >= duration_ms or end_ms > duration_ms + 500:
            raise SelectionError(f"SRT 第 {len(result) + 1} 段为空、时长无效或超出视频范围")
        if result and start_ms < result[-1].start_ms:
            raise SelectionError("SRT 时间顺序错误，请先检查是否与录播对齐")
        result.append(Subtitle(len(result) + 1, start_ms, min(end_ms, duration_ms), content))
    if not result:
        raise SelectionError("字幕为空")
    return result


def build_windows(subtitles, seconds=600, overlap_seconds=90, max_chars=18000):
    if seconds <= 0 or not 0 <= overlap_seconds < seconds or max_chars < 100:
        raise SelectionError("窗口需为正数，重叠需小于窗口，max_chars 至少为 100")
    windows = []
    cursor = 0
    while cursor < len(subtitles):
        first = subtitles[cursor]
        end = cursor
        chars = 0
        while end < len(subtitles):
            sub = subtitles[end]
            size = len(sub.text) + 100  # Reserve space for IDs and timestamps.
            if size > max_chars:
                raise SelectionError(f"字幕 {sub.id} 太长，需增加 max_window_chars 或拆句")
            if end > cursor and (sub.end_ms - first.start_ms > seconds * 1000 or chars + size > max_chars):
                break
            chars += size
            end += 1
        windows.append(Window(len(windows) + 1, tuple(subtitles[cursor:end])))
        if end == len(subtitles):
            break
        span = subtitles[end - 1].end_ms - first.start_ms
        # Character limits can make windows much shorter than the time limit.
        # Cap effective overlap to avoid advancing just one cue per API call.
        effective_overlap = min(overlap_seconds * 1000, span // 2)
        next_time = subtitles[end - 1].end_ms - effective_overlap
        next_cursor = cursor + 1
        while next_cursor < end and subtitles[next_cursor].start_ms < next_time:
            next_cursor += 1
        cursor = next_cursor
    return windows


def validate_excluded_ranges(value):
    """Original-video millisecond intervals already used by previous exports."""
    if not isinstance(value, list) or any(
            not isinstance(pair, list) or len(pair) != 2 or
            any(type(v) is not int for v in pair) or not 0 <= pair[0] < pair[1]
            for pair in value):
        raise SelectionError("selection.exclude_ranges_ms 需为 [开始毫秒,结束毫秒] 数组，且 0 <= 开始 < 结束")
    return value


def overlaps_excluded(start, end, ranges):
    return any(start < hi and end > lo for lo, hi in ranges)


def selection_windows(subtitles, config):
    """Keep IDs/times intact, and never join two sides of an excluded event."""
    excluded = validate_excluded_ranges(config.get("exclude_ranges_ms", []))
    groups, current = [], []
    for sub in subtitles:
        if overlaps_excluded(sub.start_ms, sub.end_ms, excluded):
            if current:
                groups.append(current)
                current = []
        else:
            current.append(sub)
    if current:
        groups.append(current)
    windows = []
    for group in groups:
        if excluded and group[-1].end_ms - group[0].start_ms < config["min_clip_seconds"] * 1000:
            continue
        for window in build_windows(group, config["window_seconds"], config["overlap_seconds"], config["max_window_chars"]):
            windows.append(Window(len(windows) + 1, window.subtitles))
    return windows


def _normal_text(text):
    text = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", text)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _integer(value, name):
    if type(value) is not int:
        raise SelectionError(f"{name} 必须是整数字幕 ID")
    return value


def validate_min_score(value):
    if type(value) is not int or not 0 <= value <= 100:
        raise SelectionError("selection.min_score 必须为 0–100 的整数")
    return value


def validate_quality_review(raw):
    if not isinstance(raw, dict):
        raise SelectionError("边界复核缺少 quality_review 看点评估")
    score, reason = raw.get("score"), raw.get("reason")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        raise SelectionError("quality_review.score 必须为 0–100 的有效分数")
    if not isinstance(reason, str) or not reason.strip():
        raise SelectionError("quality_review.reason 必须说明具体看点及拖沓或普通之处")
    return {"score": score, "reason": reason.strip()}


def validate_candidate(raw, allowed_subtitles, min_ms, max_ms):
    if not isinstance(raw, dict):
        raise SelectionError("候选必须是对象")
    by_id = {sub.id: sub for sub in allowed_subtitles}
    start = _integer(raw.get("start_id"), "start_id")
    end = _integer(raw.get("end_id"), "end_id")
    if start not in by_id or end not in by_id or start > end:
        raise SelectionError("候选引用不存在、越窗或倒序的字幕 ID")
    duration = by_id[end].end_ms - by_id[start].start_ms
    if not min_ms <= duration <= max_ms:
        raise CandidateDurationError(
            f"候选字幕 {start}–{end} 实际时长 {duration / 1000:.3f} 秒，"
            f"要求 {min_ms / 1000:g}–{max_ms / 1000:g} 秒；"
            "时长=end_id 对应 end_ms 减 start_id 对应 start_ms。"
            "请选择时长合规且事件完整的其他边界，无法满足则放弃该候选，禁止硬截断事件"
        )
    for key in ("title", "reason", "turning_point", "payoff"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise SelectionError(f"候选缺少 {key}")
    score = raw.get("score")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        raise SelectionError("score 必须为 0–100 的排序分数")
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise SelectionError("候选缺少原文证据")
    roles, checked = set(), []
    for item in evidence:
        if not isinstance(item, dict):
            raise SelectionError("证据必须是对象")
        sid = _integer(item.get("subtitle_id"), "evidence.subtitle_id")
        quote = item.get("quote")
        role = item.get("role")
        if sid not in by_id or not start <= sid <= end:
            raise SelectionError("证据不属于候选字幕区间")
        if not isinstance(quote, str) or not _normal_text(quote) or _normal_text(quote) not in _normal_text(by_id[sid].text):
            raise SelectionError(f"证据引文与字幕 {sid} 不一致；该条字幕原文={by_id[sid].text!r}；"
                                 f"收到 quote={quote!r}。请只复制这个 ID 内的连续原文，不能跨字幕拼接、补字或改写；"
                                 "需要相邻两句时分别提供两条 evidence。")
        if role not in {"context", "trigger", "payoff"}:
            raise SelectionError("证据 role 需为 context、trigger 或 payoff")
        roles.add(role)
        checked.append({"subtitle_id": sid, "quote": quote, "role": role,
                        "start_ms": by_id[sid].start_ms, "end_ms": by_id[sid].end_ms})
    if not {"trigger", "payoff"} <= roles:
        raise SelectionError("需同时提供触发点和结果/反应的原文证据，可来自同一句")
    return {"start_id": start, "end_id": end, "title": raw["title"].strip(),
            "reason": raw["reason"].strip(), "score": score,
            "event_type": str(raw.get("event_type", "dialogue")),
            "setup": str(raw.get("setup", "")), "turning_point": raw["turning_point"],
            "payoff": raw["payoff"], "evidence": checked}


def deduplicate(candidates, by_id, threshold=0.65):
    kept = []
    for candidate in sorted(candidates, key=lambda item: (item.get("priority_score", item["score"]), item["score"]), reverse=True):
        a, b = by_id[candidate["start_id"]].start_ms, by_id[candidate["end_id"]].end_ms
        duplicate = False
        for previous in kept:
            c, d = by_id[previous["start_id"]].start_ms, by_id[previous["end_id"]].end_ms
            overlap = max(0, min(b, d) - max(a, c))
            if overlap / min(b - a, d - c) >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def finalize_boundary(candidate, subtitles, duration_ms, lead_ms=200, tail_ms=500):
    by_id = {sub.id: sub for sub in subtitles}
    first, last = by_id[candidate["start_id"]], by_id[candidate["end_id"]]
    start, end = max(0, first.start_ms - lead_ms), min(duration_ms, last.end_ms + tail_ms)
    previous_end = max((sub.end_ms for sub in subtitles if sub.id < first.id), default=0)
    if previous_end <= first.start_ms:
        start = max(start, previous_end)
    following = [sub.start_ms for sub in subtitles if sub.id > last.id and sub.start_ms >= last.end_ms]
    if following:
        end = min(end, min(following))
    return dict(candidate, start_ms=start, end_ms=end,
                timestamp=f"{format_time(start)}-{format_time(end)}",
                boundary_status="semantic_reviewed", timing_source="subtitle_cues")


class HighlightPipeline:
    def __init__(self, model, config, output_dir, prompt_dir=None, reactions=None):
        self.model = model
        self.config = config
        self.output = Path(output_dir)
        self.prompt_dir = Path(prompt_dir or PROMPTS_DIR)
        self.rejections = []
        self.failures = []
        self.reactions = reactions

    def _reaction_context(self, payload, subtitles):
        if self.reactions is not None:
            payload["danmaku_source_fingerprint"] = self.reactions.identity
            payload["danmaku_signals"] = self.reactions.for_window(subtitles[0].start_ms, subtitles[-1].end_ms)
            payload["danmaku_context"] = self.reactions.context_for_window(subtitles[0].start_ms, subtitles[-1].end_ms)

    def _prioritize(self, candidate, start_ms, end_ms):
        if self.reactions is not None:
            candidate["danmaku"] = self.reactions.priority(start_ms, end_ms)
            candidate["priority_score"] = candidate["score"] + candidate["danmaku"]["bonus"]
        return candidate

    def _cache_location(self, stage, payload):
        prompt = (self.prompt_dir / f"highlight_{stage}_zh.txt").read_text(encoding="utf-8")
        key = fingerprint({"version": 1, "prompt": prompt, "payload": payload,
                           "model": self.model.cache_identity})
        return key, self.output / "cache" / stage / f"{key}.json"

    def _call(self, stage, payload, feedback=None):
        prompt = (self.prompt_dir / f"highlight_{stage}_zh.txt").read_text(encoding="utf-8")
        key, cache = self._cache_location(stage, payload)
        if feedback is None and cache.exists():
            try:
                value = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("identity") == key:
                    if stage == "select":
                        self.rejections.extend(value.get("policy_rejections", []))
                    return value["response"]
            except (ValueError, KeyError, OSError):
                pass
        request_payload = payload if feedback is None else dict(payload, validation_feedback=feedback,
                                                               retry_instruction="上次输出未通过校验，请根据原字幕修正；仍只输出任务要求的 JSON。")
        value = self.model.complete(prompt, request_payload)
        if not isinstance(value, dict):
            raise SelectionError("模型响应必须为 JSON 对象")
        write_json(cache, {"identity": key, "response": value})
        return value

    def _invalidate(self, stage, payload):
        _, cache = self._cache_location(stage, payload)
        cache.unlink(missing_ok=True)

    def _validated_call(self, stage, payload, validator):
        feedback = None
        last_error = None
        for _ in range(self.config.get("validation_attempts", 2)):
            response = self._call(stage, payload, feedback)
            try:
                return validator(response)
            except SelectionError as exc:
                last_error = exc
                feedback = str(exc)
                self._invalidate(stage, payload)
        if stage == "select" and isinstance(last_error, DurationRejectedWindow):
            # Only duration-policy rejections may become a checked abstention.
            # Malformed output, invented evidence and API failures remain failures.
            key, cache = self._cache_location(stage, payload)
            write_json(cache, {"identity": key, "response": {"clips": []},
                               "policy_rejections": last_error.rejections})
            print("本窗口候选经修正仍不符合时长要求，已记录弃选；继续处理其他合格片段。", flush=True)
            return [], last_error.rejections
        raise SelectionError(f"模型结果经修正仍未通过校验：{feedback}")

    def run(self, subtitles, duration_ms):
        cfg = self.config
        min_score = validate_min_score(cfg.get("min_score", DEFAULT_MIN_SCORE))
        min_ms, max_ms = int(cfg["min_clip_seconds"] * 1000), int(cfg["max_clip_seconds"] * 1000)
        excluded = validate_excluded_ranges(cfg.get("exclude_ranges_ms", []))
        windows = selection_windows(subtitles, cfg)
        by_id = {sub.id: sub for sub in subtitles}
        candidates = []
        api_blocked = False
        for window in windows:
            payload = {"brief": cfg.get("brief", ""), "min_clip_seconds": cfg["min_clip_seconds"],
                       "max_clip_seconds": cfg["max_clip_seconds"], "max_candidates": cfg["candidates_per_window"],
                       "subtitles": [asdict(sub) for sub in window.subtitles]}
            if excluded:
                payload["excluded_ranges_ms"] = excluded
            self._reaction_context(payload, window.subtitles)
            print(f"选片窗口 {window.id}/{len(windows)}，字幕 {window.subtitles[0].id}–{window.subtitles[-1].id}")
            def validate_window(response):
                if not isinstance(response.get("clips"), list):
                    raise SelectionError("模型未返回 clips 数组")
                if len(response["clips"]) > cfg["candidates_per_window"]:
                    raise SelectionError("模型返回候选数量超过 candidates_per_window")
                accepted, rejected = [], []
                duration_rejections = 0
                for raw in response["clips"]:
                    try:
                        accepted.append(validate_candidate(raw, window.subtitles, min_ms, max_ms))
                    except SelectionError as exc:
                        if isinstance(exc, CandidateDurationError):
                            duration_rejections += 1
                        rejected.append({"stage": "select", "window": window.id, "reason": str(exc)})
                if response["clips"] and not accepted:
                    if duration_rejections == len(response["clips"]):
                        raise DurationRejectedWindow(rejected)
                    raise SelectionError("本窗口候选全部未通过校验：" + "; ".join(item["reason"] for item in rejected[:3]))
                return accepted, rejected

            try:
                accepted, rejected = self._validated_call("select", payload, validate_window)
                self.rejections.extend(rejected)
                # A valid but ordinary event is a normal abstention. Never ask
                # the model to inflate its score until it passes the threshold.
                for item in accepted:
                    if item["score"] < min_score:
                        self.rejections.append({"stage": "quality", "phase": "select", "window": window.id,
                                                "title": item["title"], "start_id": item["start_id"],
                                                "end_id": item["end_id"], "score": item["score"],
                                                "min_score": min_score,
                                                "reason": f"初选看点分 {item['score']:g} 低于门槛 {min_score}，不为凑数出片"})
                    else:
                        candidates.append(self._prioritize(item, by_id[item["start_id"]].start_ms,
                                                           by_id[item["end_id"]].end_ms))
            except Exception as exc:
                self._invalidate("select", payload)
                self.failures.append({"stage": "select", "window": window.id, "reason": str(exc)})
                print(f"选片窗口 {window.id} 失败：{exc}", flush=True)
                if isinstance(exc, ModelAPIError) and exc.blocks_remaining_requests:
                    api_blocked = True
                    break

        selected = []
        for candidate in ([] if api_blocked else deduplicate(candidates, by_id)):
            if overlaps_excluded(by_id[candidate["start_id"]].start_ms, by_id[candidate["end_id"]].end_ms, excluded):
                self.rejections.append({"stage": "exclude", "title": candidate["title"], "reason": "与已有切片范围重叠"})
                continue
            start = by_id[candidate["start_id"]].start_ms - cfg["boundary_context_seconds"] * 1000
            end = by_id[candidate["end_id"]].end_ms + cfg["boundary_context_seconds"] * 1000
            context = [sub for sub in subtitles if sub.end_ms >= start and sub.start_ms <= end]
            size = sum(len(sub.text) + 100 for sub in context)
            while size > cfg["max_window_chars"]:
                can_left = context[0].id < candidate["start_id"]
                can_right = context[-1].id > candidate["end_id"]
                if not can_left and not can_right:
                    break
                left_distance = by_id[candidate["start_id"]].start_ms - context[0].start_ms
                right_distance = context[-1].end_ms - by_id[candidate["end_id"]].end_ms
                removed = context.pop(0) if can_left and (not can_right or left_distance >= right_distance) else context.pop()
                size -= len(removed.text) + 100
            if size > cfg["max_window_chars"]:
                self.failures.append({"stage": "boundary", "title": candidate["title"], "reason": "候选本身超过分析上下文预算"})
                continue
            # Keep the fixed rubric and hide the first score/recommendation so
            # review can judge the actual interval without a target-score hint.
            review_candidate = {k: v for k, v in candidate.items()
                                if k not in {"score", "reason", "priority_score", "danmaku"}}
            payload = {"brief": cfg.get("brief", ""), "candidate": review_candidate,
                       "min_clip_seconds": cfg["min_clip_seconds"], "max_clip_seconds": cfg["max_clip_seconds"],
                       "subtitles": [asdict(sub) for sub in context]}
            if excluded:
                payload["excluded_ranges_ms"] = excluded
            self._reaction_context(payload, context)
            def validate_boundary(response):
                if type(response.get("keep")) is not bool or not isinstance(response.get("boundary_reason"), str) or not response["boundary_reason"].strip():
                    raise SelectionError("边界复核需返回 keep 布尔值和 boundary_reason")
                if not response["keep"]:
                    return response, None
                if response.get("boundary_complete") is not True:
                    raise SelectionError("边界复核未确认事件完整")
                revised = validate_candidate(dict(candidate, start_id=response.get("start_id"),
                                                   end_id=response.get("end_id")), context, min_ms, max_ms)
                quality = validate_quality_review(response.get("quality_review"))
                revised.update(selection_score=candidate["score"], quality_review=quality,
                               score=min(candidate["score"], quality["score"]))
                return response, revised

            try:
                response, revised = self._validated_call("boundary", payload, validate_boundary)
                if revised is None:
                    self.rejections.append({"stage": "boundary", "title": candidate["title"], "reason": response["boundary_reason"]})
                    continue
                if revised["score"] < min_score:
                    self.rejections.append({"stage": "quality", "phase": "boundary", "title": revised["title"],
                                            "start_id": revised["start_id"], "end_id": revised["end_id"],
                                            "score": revised["score"], "selection_score": revised["selection_score"],
                                            "quality_review": revised["quality_review"], "min_score": min_score,
                                            "reason": f"复核看点分 {revised['quality_review']['score']:g} 低于门槛 {min_score}："
                                                      + revised["quality_review"]["reason"]})
                    continue
                revised["boundary_reason"] = response["boundary_reason"].strip()
                finalized = finalize_boundary(revised, subtitles, duration_ms, cfg["lead_ms"], cfg["tail_ms"])
                if finalized["end_ms"] - finalized["start_ms"] > max_ms:
                    # Keep measured speech; reduce only the optional handles.
                    finalized = finalize_boundary(revised, subtitles, duration_ms, 0, 0)
                if overlaps_excluded(finalized["start_ms"], finalized["end_ms"], excluded):
                    # Optional handles may touch an older clip; retain all speech.
                    finalized = finalize_boundary(revised, subtitles, duration_ms, 0, 0)
                if overlaps_excluded(finalized["start_ms"], finalized["end_ms"], excluded):
                    self.rejections.append({"stage": "exclude", "title": candidate["title"],
                                            "reason": "边界复核后与已有切片范围重叠；未截断事件来避让"})
                    continue
                selected.append(self._prioritize(finalized, finalized["start_ms"], finalized["end_ms"]))
            except Exception as exc:
                self._invalidate("boundary", payload)
                self.failures.append({"stage": "boundary", "title": candidate["title"], "reason": str(exc)})
                print(f"边界复核失败：{exc}", flush=True)
                if isinstance(exc, ModelAPIError) and exc.blocks_remaining_requests:
                    break

        selected = deduplicate(selected, by_id)[:cfg["max_clips"]]
        for clip in selected:
            clip["id"] = fingerprint({"subtitles": [asdict(sub) for sub in subtitles],
                                      "start_ms": clip["start_ms"], "end_ms": clip["end_ms"]})[:12]
        result = {"status": "incomplete" if self.failures else "complete", "windows": len(windows),
                  "clips": selected, "rejected": self.rejections, "failures": self.failures,
                  "quality_policy": {"min_score": min_score, "score_policy": "min(selection, review)",
                                     "rubric_version": 2, "audience": "general",
                                     "danmaku_bonus_applies_after_quality_gate": True},
                  "danmaku": self.reactions.metadata if self.reactions is not None else {
                      "status": "not_configured", "reaction_messages": 0, "ocr_used": False}}
        quality_rejections = sum(item["stage"] == "quality" for item in self.rejections)
        print(f"质量筛选：门槛 {min_score} 分，低分弃选 {quality_rejections} 个候选；最终保留 {len(selected)} 条，不补齐数量。", flush=True)
        write_json(self.output / "analysis.json", result)
        return result
