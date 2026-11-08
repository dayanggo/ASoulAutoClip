"""Dictionary correction before semantic analysis, with raw text and an audit."""

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import quote

from core.highlight_selection import SelectionError, atomic_text, fingerprint, format_time, read_subtitles, write_json
from core.paths import PROJECT_ROOT, resolve_dictionary_path


ROOT = PROJECT_ROOT
DEFAULT_CORRECTION = {"enabled": True, "dictionary_path": "resources/dictionaries/asr_dict.txt", "ignore_sources": []}


class DictionaryCorrector:
    def __init__(self, path, ignore_sources=()):
        self.path = resolve_dictionary_path(path)
        try:
            content = self.path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise SelectionError(f"无法读取纠错词典：{self.path}") from exc
        self.dictionary_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.rules, self.duplicates, self.noops = {}, 0, 0
        ignored = {s.casefold() for s in ignore_sources}
        self.ignored = []
        for line_number, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise SelectionError(f"纠错词典第 {line_number} 行格式错误：应为 错词 正词")
            wrong, right = (s.replace("_", " ") for s in parts)
            if not wrong.strip() or not right.strip():
                raise SelectionError(f"纠错词典第 {line_number} 行包含空词")
            key = wrong.casefold()
            if key in self.rules:
                if self.rules[key]["right"] != right:
                    raise SelectionError(f"纠错词典第 {line_number} 行冲突：{wrong} 有多个不同的正确写法")
                self.duplicates += 1
                continue
            self.rules[key] = {"wrong": wrong, "right": right, "line": line_number}
        active = []
        for key, rule in self.rules.items():
            if key in ignored:
                self.ignored.append(rule["wrong"])
            elif rule["wrong"] == rule["right"]:
                self.noops += 1
            else:
                active.append(rule)
        active.sort(key=lambda rule: len(rule["wrong"]), reverse=True)
        self.by_group = {f"r{i}": rule for i, rule in enumerate(active)}
        alternatives = []
        for name, rule in self.by_group.items():
            wrong = rule["wrong"]
            # English tokens must not rewrite pieces of unrelated words (ace in space).
            left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", wrong[0]) else ""
            right = r"(?![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", wrong[-1]) else ""
            alternatives.append(f"(?P<{name}>{left}{re.escape(wrong)}{right})")
        self.pattern = re.compile("|".join(alternatives), re.IGNORECASE | re.ASCII) if alternatives else None

    def correct_text(self, text):
        matches = []
        if not self.pattern:
            return text, matches

        def substitute(match):
            rule = self.by_group[match.lastgroup]
            if match.group() == rule["right"]:
                return match.group()
            matches.append(dict(rule, matched=match.group(), char_start=match.start(), char_end=match.end()))
            return rule["right"]

        # A single pass: replacement text is never fed back into the dictionary.
        return self.pattern.sub(substitute, text), matches


def subtitle_fingerprint(subtitles):
    return fingerprint([asdict(s) for s in subtitles])


def srt_text(subtitles):
    return "\n".join(f"{s.id}\n{format_time(s.start_ms, ',')} --> {format_time(s.end_ms, ',')}\n{s.text}\n"
                     for s in subtitles)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_correction_report(output, metadata, changes):
    output = Path(output)
    esc = lambda value: html.escape(str(value), quote=True)

    def link(path):
        return quote(os.path.relpath(path, output).replace("\\", "/"), safe="/:")

    rows = []
    for change in changes:
        rules = "；".join(f"{m['wrong']} → {m['right']}" for m in change["matches"])
        rows.append(f"<tr><td>{change['subtitle_id']}<br>{format_time(change['start_ms'])}</td>"
                    f"<td>{esc(change['before'])}</td><td>{esc(change['after'])}</td><td>{esc(rules)}</td></tr>")
    page = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>字幕词典纠错明细</title>"
            "<style>body{font-family:system-ui;margin:30px;line-height:1.7}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #ccc;padding:10px;text-align:left;vertical-align:top}td{overflow-wrap:anywhere}</style>"
            f"<h1>字幕词典纠错明细</h1><p>纠错{'已启用' if metadata['enabled'] else '已关闭'}；"
            f"修改 {metadata['changed_subtitles']} 条字幕，共 {metadata['replacements']} 处替换。</p>"
            "<p>这是按词典进行的文本替换，不代表已核对原音。字幕 ID 和起止时间保持不变；原始词级时间未重新对齐。</p>"
            f"<p><a href='{esc(link(metadata['raw_srt']))}'>原始字幕</a> · "
            f"<a href='{esc(link(metadata['corrected_srt']))}'>当前使用的字幕</a> · "
            "<a href='corrections.json'>完整 JSON 明细</a></p>"
            "<table><tr><th>ID / 时间</th><th>原始识别</th><th>修正后</th><th>规则</th></tr>"
            + "".join(rows) + "</table></html>")
    write_json(output / "corrections.json", dict(metadata, changes=changes))
    atomic_text(output / "corrections.html", page)


def correct_subtitles(subtitles, raw_srt, output, config=None):
    """Only edit cue text. Never touch ASR caches, cue IDs, or timestamps."""
    config = dict(DEFAULT_CORRECTION, **(config or {}))
    output, raw_srt = Path(output).resolve(), Path(raw_srt).resolve()
    if not subtitles:
        raise SelectionError("不能纠正空字幕")
    if not config["enabled"]:
        metadata = {"enabled": False, "changed_subtitles": 0, "replacements": 0,
                    "raw_srt": str(raw_srt), "corrected_srt": str(raw_srt),
                    "fingerprint": fingerprint({"enabled": False, "subtitles": subtitle_fingerprint(subtitles)})}
        write_correction_report(output, metadata, [])
        return subtitles, metadata
    corrector = DictionaryCorrector(config["dictionary_path"], config["ignore_sources"])
    identity = fingerprint({"version": 1, "raw": subtitle_fingerprint(subtitles),
                            "dictionary": corrector.dictionary_hash, "config": config})
    directory = output / "correction" / identity[:16]
    target, audit, receipt = directory / "transcript.corrected.srt", directory / "changes.json", directory / "receipt.json"
    corrected, changes = None, []
    if receipt.is_file():
        try:
            cached = json.loads(receipt.read_text(encoding="utf-8"))
            if cached["fingerprint"] == identity and set(cached["files"]) == {target.name, audit.name} and all(
                    file_hash(directory / name) == digest for name, digest in cached["files"].items()):
                corrected = read_subtitles(target, max(s.end_ms for s in subtitles))
                changes = json.loads(audit.read_text(encoding="utf-8"))
                if [(s.id, s.start_ms, s.end_ms) for s in corrected] != [(s.id, s.start_ms, s.end_ms) for s in subtitles]:
                    corrected = None
        except (OSError, ValueError, KeyError, TypeError):
            corrected = None
    if corrected is None:
        corrected, changes = [], []
        for sub in subtitles:
            text, matches = corrector.correct_text(sub.text)
            corrected.append(replace(sub, text=text))
            if matches:
                changes.append({"subtitle_id": sub.id, "start_ms": sub.start_ms, "end_ms": sub.end_ms,
                                "before": sub.text, "after": text, "matches": matches})
        atomic_text(target, srt_text(corrected))
        write_json(audit, changes)
        write_json(receipt, {"fingerprint": identity,
                             "files": {p.name: file_hash(p) for p in (target, audit)}})
    metadata = {"enabled": True, "fingerprint": identity, "raw_srt": str(raw_srt), "corrected_srt": str(target),
                "dictionary": str(corrector.path), "dictionary_sha256": corrector.dictionary_hash,
                "rules": len(corrector.rules), "active_rules": len(corrector.by_group),
                "no_op_rules": corrector.noops, "ignored_sources": corrector.ignored,
                "changed_subtitles": len(changes), "replacements": sum(len(c["matches"]) for c in changes),
                "audit_path": str(audit), "report": str(output / "corrections.html")}
    write_correction_report(output, metadata, changes)
    print(f"字幕词典纠错：{metadata['changed_subtitles']} 条字幕、{metadata['replacements']} 处替换；"
          f"原始字幕保留。明细：{output / 'corrections.html'}", flush=True)
    return corrected, metadata
