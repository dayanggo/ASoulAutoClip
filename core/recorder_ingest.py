"""Durable BililiveRecorder v2 events, file readiness and a serial clip queue."""
import copy
import json
import math
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

from core.danmaku_signals import load_danmaku
from core.highlight_selection import SelectionError, fingerprint, write_json
from core.media_geometry import probe_video
from core.paths import PROJECT_ROOT, resolve_config_path


DEFAULT_RECORDER = {
    "recordings_dir": "", "base_config": "", "allowed_rooms": [],
    "state_dir": "outputs/recorder-service", "outputs_dir": "outputs/recordings",
    "port": 18765, "quiet_seconds": 120, "stable_seconds": 30,
    "poll_seconds": 5, "danmaku_offset_seconds": 0, "mode": "full",
    "danmaku_policy": "optional", "danmaku_wait_seconds": 60,
}
SESSION_EVENTS = {"SessionStarted", "SessionEnded", "FileOpening", "FileClosed"}
VIDEO_SUFFIXES = {".flv", ".mp4", ".mkv", ".ts"}


def project_path(value):
    return (PROJECT_ROOT / Path(value).expanduser()).resolve()


def load_recorder_config(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_RECORDER):
        raise SelectionError("录播接入配置包含未知字段或不是 JSON 对象")
    config = dict(DEFAULT_RECORDER, **raw)
    for key in ("recordings_dir", "base_config", "state_dir", "outputs_dir"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise SelectionError(f"请填写录播接入配置 {key}")
    for key, low, high in (("port", 1024, 65535), ("quiet_seconds", 10, 3600),
                           ("stable_seconds", 5, 600), ("poll_seconds", 1, 60),
                           ("danmaku_wait_seconds", 0, 3600)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise SelectionError(f"{key} 必须是 {low}–{high} 的整数")
    rooms = config["allowed_rooms"]
    if not isinstance(rooms, list) or not rooms or any(type(n) is not int or n <= 0 for n in rooms):
        raise SelectionError("allowed_rooms 需填写允许自动处理的直播间号数组")
    offset = config["danmaku_offset_seconds"]
    if type(offset) not in (int, float) or not math.isfinite(offset):
        raise SelectionError("danmaku_offset_seconds 必须为有限秒数")
    if not isinstance(config["mode"], str) or config["mode"] not in {"full", "analyze"}:
        raise SelectionError("mode 只能为 full（生成视频）或 analyze（只分析）")
    if not isinstance(config["danmaku_policy"], str) or config["danmaku_policy"] not in {"optional", "required"}:
        raise SelectionError("danmaku_policy 只能为 optional（缺失时只用视频）或 required（必须有 XML）")
    if not project_path(config["recordings_dir"]).is_dir():
        raise SelectionError("recordings_dir 不存在，需与录播姬工作目录一致")
    config["base_config"] = str(resolve_config_path(config["base_config"]))
    if not Path(config["base_config"]).is_file():
        raise SelectionError("base_config 对应的切片配置不存在")
    return config


def recording_path(root, relative):
    """Accept recorder-relative paths only, including Windows separators on POSIX."""
    if not isinstance(relative, str) or not relative or len(relative) > 4096:
        raise SelectionError("RelativePath 无效")
    parts = re.split(r"[\\/]", relative)
    if PureWindowsPath(relative).drive or any(p in {"", ".", ".."} or ":" in p for p in parts):
        raise SelectionError("RelativePath 必须是录制目录内的相对文件路径")
    root = Path(root).resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() not in VIDEO_SUFFIXES:
        raise SelectionError("录制路径越界或不是支持的视频格式")
    return candidate


def source_signature(video, xml):
    return {name: {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} if path is not None else None
            for name, path in (("video", video), ("xml", xml))}


class RecorderQueue:
    def __init__(self, config, profile, clock=time.time):
        self.config, self.profile, self.clock = config, copy.deepcopy(profile), clock
        self.root = project_path(config["recordings_dir"])
        self.state = project_path(config["state_dir"])
        self.outputs = project_path(config["outputs_dir"])
        self.state.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.state / "queue.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                payload TEXT NOT NULL, received REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, room INTEGER NOT NULL,
                sid TEXT NOT NULL, started INTEGER DEFAULT 0, ended INTEGER DEFAULT 0,
                last_received REAL NOT NULL, revision INTEGER DEFAULT 0, title TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                relative_path TEXT NOT NULL, opened INTEGER DEFAULT 0, closed TEXT,
                status TEXT DEFAULT 'waiting', signature TEXT, stable_since REAL,
                reason TEXT DEFAULT '等待录制结束');
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, file_id TEXT NOT NULL UNIQUE,
                config TEXT NOT NULL, source TEXT NOT NULL, output_dir TEXT NOT NULL,
                mode TEXT NOT NULL, status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0,
                error TEXT DEFAULT '', created REAL NOT NULL);
        """)

    def close(self):
        self.db.close()

    def receive(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("EventData"), dict):
            raise SelectionError("需要 Webhook v2 的 JSON 对象及 EventData")
        event_id, kind, data = event.get("EventId"), event.get("EventType"), event["EventData"]
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or not isinstance(kind, str):
            raise SelectionError("EventId / EventType 无效；请使用 Webhook v2")
        digest = fingerprint(event)
        room = data.get("RoomId")
        relevant = kind in SESSION_EVENTS and room in self.config["allowed_rooms"]
        sid, relative, closed = None, None, None
        if relevant:
            if type(room) is not int:
                raise SelectionError("RoomId 必须是整数")
            sid = data.get("SessionId")
            if not isinstance(sid, str) or not 1 <= len(sid) <= 128:
                raise SelectionError("SessionId 无效")
            if kind.startswith("File"):
                path = recording_path(self.root, data.get("RelativePath"))
                relative = path.relative_to(self.root).as_posix()
                if kind == "FileClosed":
                    size, duration = data.get("FileSize"), data.get("Duration")
                    if type(size) is not int or size <= 0 or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                        raise SelectionError("FileClosed 缺少有效的 FileSize / Duration")
                    closed = {k: data.get(k) for k in ("FileSize", "Duration", "FileOpenTime", "FileCloseTime")}
        now = self.clock()
        with self.lock, self.db:
            existing = self.db.execute("SELECT digest FROM events WHERE id=?", (event_id,)).fetchone()
            if existing:
                if existing["digest"] != digest:
                    raise SelectionError("同一 EventId 的内容发生变化，未覆盖既有事件")
                return "duplicate"
            self.db.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                            (event_id, digest, json.dumps(event, ensure_ascii=False), now))
            if not relevant:
                return "ignored"
            session_id = fingerprint([room, sid])[:24]
            self.db.execute("INSERT OR IGNORE INTO sessions (id, room, sid, last_received) VALUES (?, ?, ?, ?)",
                            (session_id, room, sid, now))
            self.db.execute("UPDATE sessions SET last_received=?, revision=revision+1 WHERE id=?", (now, session_id))
            if isinstance(data.get("Title"), str):
                self.db.execute("UPDATE sessions SET title=? WHERE id=?", (data["Title"][:1000], session_id))
            if kind in {"SessionStarted", "SessionEnded"}:
                column = "started" if kind == "SessionStarted" else "ended"
                self.db.execute(f"UPDATE sessions SET {column}=1 WHERE id=?", (session_id,))
            else:
                file_id = fingerprint([session_id, relative.casefold()])[:24]
                self.db.execute("INSERT OR IGNORE INTO files (id, session_id, relative_path) VALUES (?, ?, ?)",
                                (file_id, session_id, relative))
                if closed:
                    old = self.db.execute("SELECT closed FROM files WHERE id=?", (file_id,)).fetchone()["closed"]
                    serialized = json.dumps(closed, sort_keys=True)
                    if old and old != serialized:
                        raise SelectionError("同一录制文件出现冲突的关闭信息，请人工检查")
                    self.db.execute("UPDATE files SET closed=? WHERE id=?", (serialized, file_id))
                else:
                    # A delayed FileOpening must not undo an already received FileClosed.
                    self.db.execute("UPDATE files SET opened=1 WHERE id=?", (file_id,))
        return "accepted"

    def _session_reason(self, session, now):
        if not session["started"]:
            return "缺少 SessionStarted：服务可能启动过晚，需人工补跑本场"
        if not session["ended"]:
            return "等待 SessionEnded，录制尚未结束"
        if now - session["last_received"] < self.config["quiet_seconds"]:
            return "等待录制事件安静期，兼容结束事件先于文件关闭"
        unclosed = self.db.execute("SELECT 1 FROM files WHERE session_id=? AND (opened=0 OR closed IS NULL)",
                                   (session["id"],)).fetchone()
        return "等待所有已知分段的 FileOpening / FileClosed" if unclosed else ""

    def _reason(self, file_id, text):
        with self.lock, self.db:
            self.db.execute("UPDATE files SET reason=? WHERE id=?", (text, file_id))

    def refresh(self, probe=probe_video):
        """Only inspect event-registered files. Never discover and run old recordings."""
        now = self.clock()
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM files WHERE status='waiting'")]
        queued = 0
        for row in rows:
            with self.lock:
                session = dict(self.db.execute("SELECT * FROM sessions WHERE id=?", (row["session_id"],)).fetchone())
                reason = self._session_reason(session, now)
            if reason:
                self._reason(row["id"], reason)
                continue
            try:
                video = recording_path(self.root, row["relative_path"])
                expected_xml = video.with_suffix(".xml")
                if not expected_xml.resolve().is_relative_to(self.root):
                    raise SelectionError("XML 路径越界")
                # A malformed, unreadable or broken-link XML is an error, not an absent file.
                xml = expected_xml if expected_xml.exists() or expected_xml.is_symlink() else None
                if xml is None and self.config["danmaku_policy"] == "required":
                    raise SelectionError("required 模式缺少同名 XML，等待弹幕文件")
                signature = source_signature(video, xml)
                closed = json.loads(row["closed"])
                if signature["video"]["size"] != closed["FileSize"]:
                    raise SelectionError("视频大小与 FileClosed 不符；等待写完，不自动换成弹幕版")
                if xml is not None and not 0 < signature["xml"]["size"] <= 128 * 1024 * 1024:
                    raise SelectionError("XML 为空或超过 128 MiB，需检查原始弹幕文件")
                stamp = json.dumps(signature, sort_keys=True)
                if row["signature"] != stamp or row["stable_since"] is None:
                    with self.lock, self.db:
                        self.db.execute("UPDATE files SET signature=?, stable_since=?, reason=? WHERE id=?",
                                        (stamp, now, "等待视频与 XML 大小和修改时间稳定", row["id"]))
                    continue
                if now - row["stable_since"] < self.config["stable_seconds"]:
                    self._reason(row["id"], "等待视频与 XML 大小和修改时间稳定")
                    continue
                if xml is None:
                    deadline = session["last_received"] + self.config["quiet_seconds"] + self.config["danmaku_wait_seconds"]
                    if now < deadline:
                        self._reason(row["id"], f"等待可选 XML，剩余约 {math.ceil(deadline-now)} 秒；到期后只用视频切片")
                        continue
                media = probe(video)
                if not media["has_audio"]:
                    raise SelectionError("录制文件没有音轨，无法自动转写")
                if abs(media["duration_ms"] / 1000 - closed["Duration"]) > max(3, closed["Duration"] * .005):
                    raise SelectionError("视频时长与关闭事件不符，未猜测弹幕时间偏移")
                config = copy.deepcopy(self.profile)
                output = self.outputs / str(session["room"]) / session["id"] / row["id"]
                config["input"] = {"video": str(video), "srt": "", "output_dir": str(output)}
                config["danmaku"].update(path=str(xml) if xml is not None else "",
                                          offset_seconds=self.config["danmaku_offset_seconds"])
                # Exclusions belong to a particular old video, never a future recording.
                config["selection"]["exclude_ranges_ms"] = []
                config["selection"]["brief"] += (f"\n本次直播间标题：{session['title']}。"
                    "当前输入是本场的一个原始录制分段；不要虚构分段外的铺垫、结果或逐句说话人。")
                config["publication"]["source_url"] = f"https://live.bilibili.com/{session['room']}"
                reactions = load_danmaku(config["danmaku"], media["duration_ms"])
                if source_signature(video, xml) != signature or (xml is None and expected_xml.exists()):
                    raise SelectionError("校验期间源文件仍发生变化，继续等待")
                source = {"video": str(video), "xml": str(xml) if xml is not None else None, "signature": signature,
                          "room_id": session["room"], "session_id": session["sid"],
                          "file_closed": closed, "media": media, "danmaku": reactions.metadata if reactions is not None else {
                              "status": "not_configured", "reason": "xml_not_found_after_wait", "reaction_messages": 0,
                              "ocr_used": False, "expected_xml": str(expected_xml)},
                          "cross_segment_editing": False}
                with self.lock, self.db:
                    current = self.db.execute("SELECT revision FROM sessions WHERE id=?", (session["id"],)).fetchone()
                    if current["revision"] != session["revision"]:
                        continue
                    self.db.execute("INSERT OR IGNORE INTO jobs (id,file_id,config,source,output_dir,mode,created) VALUES (?,?,?,?,?,?,?)",
                                    (row["id"], row["id"], json.dumps(config, ensure_ascii=False),
                                     json.dumps(source, ensure_ascii=False), str(output), self.config["mode"], now))
                    self.db.execute("UPDATE files SET status='queued', reason='已校验，等待串行处理' WHERE id=?", (row["id"],))
                queued += 1
            except (SelectionError, OSError, ValueError) as exc:
                self._reason(row["id"], "等待或需检查：" + str(exc))
        return queued

    def recover_interrupted(self):
        # Do not start another GPU task after a hard service crash until the operator retries.
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET status='interrupted', error='服务中断；确认旧切片进程已停止后显式重试' WHERE status='running'")

    def retry(self, job_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in {"failed", "interrupted"}:
                raise SelectionError("只能重试 failed / interrupted 任务；使用 --status 查看 ID")
            self.db.execute("UPDATE jobs SET status='queued', error='' WHERE id=?", (job_id,))

    def status(self):
        with self.lock:
            return {"jobs": [dict(r) for r in self.db.execute("SELECT id,status,attempts,mode,output_dir,error FROM jobs ORDER BY created,id")],
                    "waiting": [dict(r) for r in self.db.execute("SELECT relative_path,reason FROM files WHERE status='waiting'")],
                    "queue_paused": bool(self.db.execute("SELECT 1 FROM jobs WHERE status='interrupted'").fetchone())}

    def run_next(self, runner):
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('running','interrupted')").fetchone():
                return False
            row = self.db.execute("SELECT j.*, f.session_id FROM jobs j JOIN files f ON j.file_id=f.id WHERE j.status='queued' ORDER BY j.created,j.id LIMIT 1").fetchone()
            if not row:
                return False
            job = dict(row)
            session = self.db.execute("SELECT * FROM sessions WHERE id=?", (job["session_id"],)).fetchone()
            if self._session_reason(session, self.clock()):
                return False
            self.db.execute("UPDATE jobs SET status='running', attempts=attempts+1 WHERE id=?", (job["id"],))
        source = json.loads(job["source"])
        status, error = "failed", ""
        try:
            video = recording_path(self.root, str(Path(source["video"]).relative_to(self.root)))
            xml = Path(source["xml"]) if source["xml"] is not None else None
            if (xml is not None and not xml.resolve().is_relative_to(self.root)) or source_signature(video, xml) != source["signature"]:
                raise SelectionError("源视频 / XML 在入队后变化，未调用转写或模型；请人工核对")
            output = Path(job["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "recorder-source.json", source)
            write_json(output / "recorder-config.json", json.loads(job["config"]))
            args = [sys.executable, "-u", "-X", "utf8", "-m", "core.cli", "--config", str(output / "recorder-config.json")]
            if job["mode"] == "analyze":
                args.append("--analyze-only")
            print(f"开始录播切片任务 {job['id']}：{video.name}", flush=True)
            print("选片输入：视频 + XML 弹幕辅助。" if xml is not None else "选片输入：仅视频，未找到独立 XML。", flush=True)
            code = runner(args, output / "pipeline.log")
            if source_signature(video, xml) != source["signature"]:
                raise SelectionError("处理期间源文件变化，结果需要人工核对")
            if code:
                raise SelectionError(f"切片进程退出码 {code}，请查看 pipeline.log；缓存保留，不自动重复付费请求")
            result = json.loads((output / "analysis.json").read_text(encoding="utf-8-sig"))
            receipt = json.loads((output / "job.json").read_text(encoding="utf-8-sig"))
            if result.get("status") != "complete" or receipt.get("status") != "complete" or result.get("failures"):
                raise SelectionError("切片进程没有给出完整分析，需查看报告后重试")
            expected = dict(path=str(video), **source["signature"]["video"])
            if result.get("source") != expected or receipt.get("source") != expected:
                raise SelectionError("切片结果来源与入队视频不符，未标记为成功")
            if not isinstance(result.get("clips"), list):
                raise SelectionError("切片结果缺少 clips 数组")
            status = "complete_empty" if not result["clips"] else ("analyzed" if job["mode"] == "analyze" else "complete")
        except KeyboardInterrupt:
            status, error = "interrupted", "用户中断；确认旧进程停止后重试，已完成缓存保留"
            raise
        except Exception as exc:
            error = str(exc)
        finally:
            with self.lock, self.db:
                self.db.execute("UPDATE jobs SET status=?, error=? WHERE id=?", (status, error, job["id"]))
            print(f"录播任务 {job['id']}：{status}" + (f"；{error}" if error else ""), flush=True)
        return True


def run_pipeline(args, log):
    """Own the child process group so Ctrl+C stops our ASR children as well."""
    import os
    import signal

    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    with Path(log).open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(args, cwd=PROJECT_ROOT, stdout=stream, stderr=subprocess.STDOUT, **options)
        try:
            return process.wait()
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=20)
            raise
