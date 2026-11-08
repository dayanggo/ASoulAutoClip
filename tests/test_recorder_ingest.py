import contextlib
import copy
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock

from core.cli import DEFAULT_CONFIG, load_config
from core.highlight_selection import SelectionError, write_json
from core.recorder_ingest import (DEFAULT_RECORDER, RecorderQueue, load_recorder_config,
                                  recording_path, source_signature)
from core.recorder_webhook import make_server


class RecorderIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recordings = self.root / "recordings"
        self.recordings.mkdir()
        self.time = 1000.
        self.counter = 0
        self.config = dict(DEFAULT_RECORDER, recordings_dir=str(self.recordings),
                           state_dir=str(self.root / "state"), outputs_dir=str(self.root / "outputs"),
                           allowed_rooms=[123], quiet_seconds=10, stable_seconds=5, danmaku_wait_seconds=5)
        self.profile = copy.deepcopy(DEFAULT_CONFIG)
        self.profile["input"] = {"video": "old.mp4", "srt": "old.srt", "output_dir": "old-output"}
        self.profile["selection"]["exclude_ranges_ms"] = [[100, 500]]
        self.profile["danmaku"].update(path="old.xml", offset_seconds=999)
        self.profile["publication"]["source_url"] = "https://www.bilibili.com/video/old"
        self.queue = RecorderQueue(self.config, self.profile, clock=lambda: self.time)
        self.probe = Mock(return_value={"duration_ms": 10000, "has_audio": True, "width": 1080, "height": 1920})
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.queue.close()
        self.output.__exit__(None, None, None)
        self.temp.cleanup()

    def event(self, kind, relative="嘉然/原始录制.flv", session="session-1", **extra):
        self.counter += 1
        data = {"RoomId": 123, "SessionId": session, "Title": "三人电台"}
        if kind.startswith("File"):
            data["RelativePath"] = relative
        if kind == "FileClosed":
            data.update(FileSize=5, Duration=10, FileOpenTime="2026-11-08T12:00:00+08:00",
                        FileCloseTime="2026-11-08T12:00:10+08:00")
        data.update(extra)
        return {"EventId": f"event-{self.counter}", "EventType": kind,
                "EventTimestamp": "2026-11-08T12:00:10+08:00", "EventData": data}

    def files(self, relative="嘉然/原始录制.flv", xml="<i><d p='2,1,25,1,0,0,42,0'>绷</d></i>"):
        video = self.recordings / relative
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        if xml is not None:
            video.with_suffix(".xml").write_text(xml, encoding="utf-8")
        return video

    def finished(self, relative="嘉然/原始录制.flv", session="session-1"):
        for kind in ("SessionStarted", "FileOpening", "FileClosed", "SessionEnded"):
            self.queue.receive(self.event(kind, relative, session))

    def ready(self):
        self.time += 11
        self.queue.refresh(self.probe)
        self.time += 6
        self.queue.refresh(self.probe)

    def success_runner(self, clips=None):
        def run(args, log):
            config = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
            output = Path(config["input"]["output_dir"])
            video = Path(config["input"]["video"])
            xml = Path(config["danmaku"]["path"]) if config["danmaku"]["path"] else None
            source = dict(path=str(video), **source_signature(video, xml)["video"])
            write_json(output / "analysis.json", {"status": "complete", "source": source,
                                                 "clips": clips or [], "failures": []})
            write_json(output / "job.json", {"status": "complete", "source": source})
            return 0
        return run

    def test_ended_before_closed_and_opening_after_closed_waits_for_both_and_stability(self):
        self.files()
        for kind in ("SessionStarted", "SessionEnded", "FileClosed"):
            self.queue.receive(self.event(kind))
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.queue.receive(self.event("FileOpening"))
        self.ready()
        self.assertEqual(len(self.queue.status()["jobs"]), 1)
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "queued")

    def test_duplicate_event_is_durable_and_payload_change_is_rejected(self):
        event = self.event("SessionStarted")
        self.assertEqual(self.queue.receive(event), "accepted")
        self.time += 3
        self.assertEqual(self.queue.receive(event), "duplicate")
        self.queue.close()
        self.queue = RecorderQueue(self.config, self.profile, clock=lambda: self.time)
        self.assertEqual(self.queue.receive(event), "duplicate")
        changed = copy.deepcopy(event)
        changed["EventData"]["Title"] = "changed"
        with self.assertRaisesRegex(SelectionError, "EventId"):
            self.queue.receive(changed)
        self.assertEqual(self.queue.db.execute("SELECT last_received FROM sessions").fetchone()[0], 1000.)

    def test_neither_file_stability_nor_stream_end_replaces_session_end(self):
        self.files()
        for kind in ("SessionStarted", "FileOpening", "FileClosed", "StreamEnded"):
            self.queue.receive(self.event(kind))
        self.time += 10000
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("SessionEnded", self.queue.status()["waiting"][0]["reason"])

    def test_missing_start_history_is_not_silently_accepted(self):
        self.files()
        for kind in ("FileOpening", "FileClosed", "SessionEnded"):
            self.queue.receive(self.event(kind))
        self.ready()
        self.assertIn("缺少 SessionStarted", self.queue.status()["waiting"][0]["reason"])
        self.probe.assert_not_called()

    def test_required_mode_waits_for_xml(self):
        self.config["danmaku_policy"] = "required"
        self.files(xml=None)
        self.finished()
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("等待或需检查", self.queue.status()["waiting"][0]["reason"])
        self.probe.assert_not_called()

    def test_optional_missing_xml_runs_video_only_without_old_danmaku_path(self):
        self.files(xml=None)
        self.finished()
        self.ready()
        row = self.queue.db.execute("SELECT config, source FROM jobs").fetchone()
        config, source = json.loads(row["config"]), json.loads(row["source"])
        self.assertEqual(config["danmaku"]["path"], "")
        self.assertIsNone(source["xml"])
        self.assertIsNone(source["signature"]["xml"])
        self.assertEqual(source["danmaku"]["reason"], "xml_not_found_after_wait")
        self.assertEqual(source["danmaku"]["status"], "not_configured")
        self.queue.run_next(self.success_runner([{"id": "video-only-clip"}]))
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "complete")

    def test_optional_waits_for_late_xml_then_uses_it_after_stability(self):
        self.config["danmaku_wait_seconds"] = 30
        video = self.files(xml=None)
        self.finished()
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("等待可选 XML", self.queue.status()["waiting"][0]["reason"])
        video.with_suffix(".xml").write_text("<i><d p='2,1,25,1,0,0,42,0'>这是刚才的问题</d></i>", encoding="utf-8")
        self.queue.refresh(self.probe)
        self.time += 6
        self.assertEqual(self.queue.refresh(self.probe), 1)
        source = json.loads(self.queue.db.execute("SELECT source FROM jobs").fetchone()[0])
        self.assertEqual(source["xml"], str(video.with_suffix(".xml")))
        self.assertEqual(source["danmaku"]["context_messages"], 1)
        self.assertEqual(source["danmaku"]["reaction_messages"], 0)

    def test_missing_xml_does_not_bypass_video_stability_or_file_size_validation(self):
        video = self.files(xml=None)
        self.finished()
        self.time += 11
        self.queue.refresh(self.probe)
        self.time += 2
        self.queue.refresh(self.probe)
        self.assertEqual(self.queue.status()["jobs"], [])
        video.write_bytes(b"incorrect-size")
        self.time += 100
        self.queue.refresh(self.probe)
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("视频大小", self.queue.status()["waiting"][0]["reason"])

    def test_xml_arriving_after_video_only_enqueue_does_not_mutate_frozen_job(self):
        video = self.files(xml=None)
        self.finished()
        self.ready()
        video.with_suffix(".xml").write_text("<i/>", encoding="utf-8")
        self.queue.run_next(self.success_runner())
        source = json.loads(self.queue.db.execute("SELECT source FROM jobs").fetchone()[0])
        self.assertIsNone(source["xml"])
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "complete_empty")

    def test_malformed_or_unsafe_xml_and_wrong_duration_are_held(self):
        video = self.files(xml="<i>")
        self.finished()
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("XML 格式错误", self.queue.status()["waiting"][0]["reason"])
        video.with_suffix(".xml").write_text("<!DOCTYPE i><i/>", encoding="utf-8")
        self.ready()
        self.assertIn("DTD", self.queue.status()["waiting"][0]["reason"])
        video.with_suffix(".xml").write_text("<i/>", encoding="utf-8")
        self.probe.return_value["duration_ms"] = 100000
        self.ready()
        self.assertIn("时长", self.queue.status()["waiting"][0]["reason"])

    def test_file_change_resets_stability_timer(self):
        video = self.files()
        self.finished()
        self.time += 11
        self.queue.refresh(self.probe)
        self.time += 4
        video.with_suffix(".xml").write_text("<i/>", encoding="utf-8")
        self.queue.refresh(self.probe)
        self.time += 2
        self.queue.refresh(self.probe)
        self.assertEqual(self.queue.status()["jobs"], [])
        self.time += 4
        self.assertEqual(self.queue.refresh(self.probe), 1)

    def test_zero_comments_is_a_valid_pair_and_generated_config_is_isolated(self):
        self.files(xml="<i/>")
        self.finished()
        self.ready()
        row = self.queue.db.execute("SELECT config,source FROM jobs").fetchone()
        config, source = json.loads(row[0]), json.loads(row[1])
        self.assertEqual(config["input"]["srt"], "")
        self.assertEqual(config["danmaku"]["offset_seconds"], 0)
        self.assertEqual(config["selection"]["exclude_ranges_ms"], [])
        self.assertEqual(config["publication"]["source_url"], "https://live.bilibili.com/123")
        self.assertEqual(config["render"], self.profile["render"])
        self.assertEqual(config["selection"]["min_score"], 85)
        self.assertEqual(source["danmaku"]["reaction_messages"], 0)
        self.assertFalse(source["cross_segment_editing"])
        generated = self.root / "generated.json"
        write_json(generated, config)
        self.assertEqual(load_config(generated)["input"], config["input"])
        self.assertEqual(self.profile["danmaku"]["offset_seconds"], 999)

    def test_multiple_segments_wait_for_all_known_files_closed_and_are_independent(self):
        self.files()
        self.files("嘉然/第二段.flv")
        self.finished()
        self.queue.receive(self.event("FileOpening", "嘉然/第二段.flv"))
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.queue.receive(self.event("FileClosed", "嘉然/第二段.flv"))
        self.ready()
        self.assertEqual(len(self.queue.status()["jobs"]), 2)
        self.assertTrue(self.queue.run_next(self.success_runner()))
        self.assertEqual([j["status"] for j in self.queue.status()["jobs"]].count("queued"), 1)
        self.assertTrue(self.queue.run_next(self.success_runner()))
        self.assertFalse(self.queue.run_next(self.success_runner()))

    def test_source_change_after_enqueue_blocks_worker_before_calling_runner(self):
        video = self.files()
        self.finished()
        self.ready()
        video.write_bytes(b"changed-video")
        runner = Mock()
        self.queue.run_next(runner)
        runner.assert_not_called()
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "failed")

    def test_late_open_event_after_enqueue_defers_worker_until_all_parts_close(self):
        self.files()
        self.finished()
        self.ready()
        self.queue.receive(self.event("FileOpening", "嘉然/第二段.flv"))
        self.time += 100
        runner = Mock()
        self.assertFalse(self.queue.run_next(runner))
        runner.assert_not_called()

    def test_exit_zero_with_wrong_source_receipt_is_not_a_success(self):
        self.files()
        self.finished()
        self.ready()
        def runner(args, log):
            self.success_runner()(args, log)
            output = log.parent
            write_json(output / "job.json", {"status": "complete", "source": {"path": "wrong.mp4"}})
            return 0
        self.queue.run_next(runner)
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "failed")
        self.assertIn("来源", self.queue.status()["jobs"][0]["error"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_actual_portrait_video_and_xml_pass_readiness_without_asr_or_api(self):
        video = self.recordings / "portrait.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=120x200:r=5",
                        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
                       check=True, capture_output=True, timeout=30)
        video.with_suffix(".xml").write_text("<i><d p='1,1,25,1,0,0,42,0'>绷</d></i>", encoding="utf-8")
        for kind in ("SessionStarted", "FileOpening", "FileClosed", "SessionEnded"):
            extra = {"FileSize": video.stat().st_size, "Duration": 3} if kind == "FileClosed" else {}
            self.queue.receive(self.event(kind, "portrait.mp4", **extra))
        self.time += 11
        self.queue.refresh()
        self.time += 6
        self.assertEqual(self.queue.refresh(), 1)
        source = json.loads(self.queue.db.execute("SELECT source FROM jobs").fetchone()[0])
        self.assertGreater(source["media"]["height"], source["media"]["width"])
        self.assertTrue(source["media"]["has_audio"])
        self.assertEqual(source["danmaku"]["reaction_messages"], 1)

    def test_empty_success_and_failure_do_not_automatically_run_again(self):
        self.files()
        self.finished()
        self.ready()
        self.queue.run_next(self.success_runner())
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "complete_empty")
        self.assertFalse(self.queue.run_next(Mock()))
        with self.assertRaises(SelectionError):
            self.queue.retry(self.queue.status()["jobs"][0]["id"])

    def test_api_failure_requires_explicit_retry_and_reuses_same_output(self):
        self.files()
        self.finished()
        self.ready()
        first = self.queue.status()["jobs"][0]
        self.queue.run_next(lambda args, log: 2)
        runner = Mock()
        self.assertFalse(self.queue.run_next(runner))
        runner.assert_not_called()
        self.queue.retry(first["id"])
        self.queue.run_next(self.success_runner([{"id": "clip"}]))
        job = self.queue.status()["jobs"][0]
        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["attempts"], 2)
        self.assertEqual(job["output_dir"], first["output_dir"])

    def test_restart_pauses_queue_until_interrupted_job_is_explicitly_retried(self):
        self.files()
        self.finished()
        self.ready()
        with self.queue.db:
            self.queue.db.execute("UPDATE jobs SET status='running'")
        self.queue.recover_interrupted()
        self.assertTrue(self.queue.status()["queue_paused"])
        runner = Mock()
        self.assertFalse(self.queue.run_next(runner))
        runner.assert_not_called()
        self.queue.retry(self.queue.status()["jobs"][0]["id"])
        self.queue.run_next(self.success_runner())
        self.assertFalse(self.queue.status()["queue_paused"])

    def test_analyze_mode_is_frozen_at_enqueue_and_never_claims_video_was_rendered(self):
        self.queue.config["mode"] = "analyze"
        self.files()
        self.finished()
        self.ready()
        self.queue.config["mode"] = "full"
        def runner(args, log):
            self.assertIn("--analyze-only", args)
            return self.success_runner([{"id": "clip"}])(args, log)
        self.queue.run_next(runner)
        self.assertEqual(self.queue.status()["jobs"][0]["status"], "analyzed")

    def test_unlisted_rooms_unknown_events_and_old_unregistered_files_do_not_create_jobs(self):
        self.files()
        self.queue.receive(self.event("SessionStarted", RoomId=999))
        self.queue.receive(self.event("SomeFutureEvent"))
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertEqual(self.queue.status()["waiting"], [])

    def test_out_of_range_danmaku_requires_correction_not_automatic_offset_guessing(self):
        self.files(xml="<i><d p='12345,1,25,1,0,0,42,0'>绷</d></i>")
        self.finished()
        self.ready()
        self.assertEqual(self.queue.status()["jobs"], [])
        self.assertIn("offset_seconds", self.queue.status()["waiting"][0]["reason"])

    def test_recording_paths_cannot_escape_root(self):
        for path in ("../a.flv", "room/../../a.flv", "C:\\video.flv", "\\\\server\\share\\a.flv",
                     "/tmp/a.mp4", "room/a.flv:evil", "room/a.xml", "room//a.flv"):
            with self.subTest(path=path), self.assertRaises(SelectionError):
                recording_path(self.recordings, path)
        self.assertEqual(recording_path(self.recordings, "room\\a.flv"), self.recordings / "room/a.flv")

    def test_public_config_is_blank_and_private_config_validates_without_reading_video(self):
        from core.paths import PROJECT_ROOT
        public = json.loads((PROJECT_ROOT / "config/examples/recorder.example.json").read_text(encoding="utf-8"))
        self.assertFalse(public["recordings_dir"])
        self.assertFalse(public["base_config"])
        self.assertEqual(public["allowed_rooms"], [])
        path = self.root / "service.json"
        base = self.root / "base.json"
        write_json(base, DEFAULT_CONFIG)
        write_json(path, dict(self.config, base_config=str(base)))
        self.assertEqual(load_recorder_config(path)["base_config"], str(base))
        write_json(path, dict(self.config, base_config=str(base), allowed_rooms=[]))
        with self.assertRaisesRegex(SelectionError, "allowed_rooms"):
            load_recorder_config(path)

    def test_existing_service_config_defaults_to_optional_and_new_settings_are_validated(self):
        path, base = self.root / "service.json", self.root / "base.json"
        write_json(base, DEFAULT_CONFIG)
        config = dict(self.config, base_config=str(base))
        config.pop("danmaku_policy")
        config.pop("danmaku_wait_seconds")
        write_json(path, config)
        loaded = load_recorder_config(path)
        self.assertEqual(loaded["danmaku_policy"], "optional")
        self.assertEqual(loaded["danmaku_wait_seconds"], 60)
        for update in ({"danmaku_policy": []}, {"danmaku_policy": "invalid"},
                       {"danmaku_wait_seconds": -1}, {"danmaku_wait_seconds": True}):
            write_json(path, dict(config, **update))
            with self.subTest(update=update), self.assertRaises(SelectionError):
                load_recorder_config(path)

    def test_webhook_only_persists_events_and_rejects_browser_and_bad_requests(self):
        server = make_server(self.queue, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/bililive"
        def post(body, **headers):
            request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers})
            return urllib.request.urlopen(request, timeout=5)
        try:
            event = json.dumps(self.event("SessionStarted")).encode("utf-8")
            with post(event) as response:
                self.assertEqual(response.status, 204)
            with post(event) as response:
                self.assertEqual(response.status, 204)
            for body, headers, expected in ((b"not-json", {}, 400),
                                            (event, {"Origin": "https://example.invalid"}, 415),
                                            (event, {"Content-Type": "text/plain"}, 415),
                                            (b"x" * 65537, {}, 413)):
                with self.subTest(expected=expected), self.assertRaises(urllib.error.HTTPError) as context:
                    post(body, **headers)
                self.assertEqual(context.exception.code, expected)
            self.assertEqual(self.queue.status()["jobs"], [])
            self.assertEqual(self.queue.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
