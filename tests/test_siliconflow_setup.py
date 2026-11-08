import argparse
import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests

from analyze_recording import DEFAULT_CONFIG, load_config, main, preflight, resolve_inputs
from core.highlight_model import ChatModel
from core.highlight_selection import HighlightPipeline, SelectionError, ModelAPIError, Subtitle
from core.job_lock import job_lock
from core.local_config import load_local_key
from core.local_transcription import run_worker, transcribe
from test_recording_pipeline import FakeModel, SUBS, candidate, selection_config


def response(status=200, content='{"clips":[]}', reasoning=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "content": content, "reasoning_content": reasoning}}]}).encode("utf-8")
    return result


class SiliconFlowSetupTests(unittest.TestCase):
    def model(self, **overrides):
        return ChatModel(dict(DEFAULT_CONFIG["llm"], model="test/model", api_key_env="", **overrides))

    def test_siliconflow_payload_json_and_optional_thinking(self):
        model = self.model(enable_thinking=False)
        with patch("core.highlight_model.requests.post", return_value=response()) as post:
            self.assertEqual(model.complete("请输出 JSON", {}), {"clips": []})
        self.assertEqual(post.call_args.args[0], "https://api.siliconflow.cn/v1/chat/completions")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIs(body["enable_thinking"], False)

    def test_optional_thinking_is_omitted_when_unset(self):
        with patch("core.highlight_model.requests.post", return_value=response()) as post:
            self.model().complete("JSON", {})
        self.assertNotIn("enable_thinking", post.call_args.kwargs["json"])

    def test_authentication_error_does_not_echo_service_body(self):
        error = response(401)
        error._content = b'{"message":"sensitive-sentinel-key"}'
        with patch("core.highlight_model.requests.post", return_value=error):
            with self.assertRaises(SelectionError) as caught:
                self.model().complete("JSON", {})
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("sensitive-sentinel-key", str(caught.exception))

    def test_rate_limit_is_retried_with_bounded_attempts(self):
        with patch("core.highlight_model.requests.post", side_effect=[response(429), response()]) as post, patch("core.highlight_model.time.sleep"):
            self.model().complete("JSON", {})
        self.assertEqual(post.call_count, 2)

    def test_balance_error_stops_requests_and_resumes_successful_cache(self):
        config = selection_config()
        config.update(window_seconds=5, overlap_seconds=1, max_clip_seconds=4)
        subs = [Subtitle(i + 1, i * 1000, i * 1000 + 800, "测试字幕") for i in range(20)]
        with tempfile.TemporaryDirectory() as folder:
            model = self.model()
            with patch("core.highlight_model.requests.post", side_effect=[response(), response(402)]) as post:
                first = HighlightPipeline(model, config, folder).run(subs, 20000)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(first["status"], "incomplete")
            self.assertEqual(len(first["failures"]), 1)
            self.assertIn("402", first["failures"][0]["reason"])
            self.assertEqual(len(list((Path(folder) / "cache/select").glob("*.json"))), 1)
            with patch("core.highlight_model.requests.post", return_value=response()) as post:
                second = HighlightPipeline(model, config, folder).run(subs, 20000)
            self.assertEqual(second["status"], "complete")
            self.assertEqual(post.call_count, second["windows"] - 1)

    def test_balance_error_during_boundary_stops_other_boundary_requests(self):
        config = selection_config()
        raw = candidate()
        second_raw = dict(raw, start_id=4, end_id=6, title="另一个反转",
                          evidence=[dict(item, subtitle_id=item["subtitle_id"] + 3) for item in raw["evidence"]])
        subs = SUBS + [Subtitle(s.id + 3, s.start_ms + 10000, s.end_ms + 10000, s.text) for s in SUBS]
        with tempfile.TemporaryDirectory() as folder:
            model = self.model()
            with patch("core.highlight_model.requests.post", side_effect=[
                    response(content=json.dumps({"clips": [raw, second_raw]})), response(402)]) as post:
                result = HighlightPipeline(model, config, folder).run(subs, 20000)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(result["failures"][0]["stage"], "boundary")
            self.assertEqual(result["status"], "incomplete")

    def test_reasoning_content_does_not_replace_missing_final_json(self):
        with patch("core.highlight_model.requests.post", return_value=response(content="", reasoning="private reasoning")):
            with self.assertRaisesRegex(SelectionError, "最终答案"):
                self.model().complete("JSON", {})

    def test_full_endpoint_is_rejected_instead_of_duplicated(self):
        with self.assertRaisesRegex(SelectionError, "重复"):
            self.model(base_url="https://api.siliconflow.cn/v1/chat/completions")

    def test_example_keeps_required_inputs_blank(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config/examples/highlights.example.json")
        self.assertEqual(config["input"]["video"], "")
        self.assertEqual(config["llm"]["model"], "")
        self.assertEqual(config["llm"]["base_url"], "https://api.siliconflow.cn/v1")

    def test_dotenv_only_loads_selected_key_without_expansion(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / ".env"
            path.write_text('UNRELATED=do-not-load\nTEST_KEY="literal-$(value)-#text"\n', encoding="utf-8")
            load_local_key("TEST_KEY", path)
            self.assertEqual(os.environ["TEST_KEY"], "literal-$(value)-#text")
            self.assertNotIn("UNRELATED", os.environ)

    def test_system_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"TEST_KEY": "existing"}):
            path = Path(folder) / ".env"
            path.write_text("TEST_KEY=file-value", encoding="utf-8")
            load_local_key("TEST_KEY", path)
            self.assertEqual(os.environ["TEST_KEY"], "existing")

    def test_auto_output_is_stable_and_config_paths_are_project_relative(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["input"]["video"] = "recordings/test.mp4"
        args = argparse.Namespace(video=None, srt=None, output=None)
        with tempfile.TemporaryDirectory() as folder, patch("analyze_recording.PROJECT_ROOT", Path(folder)):
            first = resolve_inputs(args, config)
            second = resolve_inputs(args, config)
            self.assertEqual(first, second)
            self.assertEqual(first[0], Path(folder) / "recordings/test.mp4")
            self.assertEqual(first[2].parent, Path(folder) / "outputs")

    def test_blank_config_stops_before_downloads_and_api(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text("{}", encoding="utf-8")
            with patch("core.highlight_model.requests.post") as post, patch("core.local_transcription.run_worker") as worker, contextlib.redirect_stderr(io.StringIO()) as errors:
                code = main(["--config", str(config), "--check"])
            self.assertEqual(code, 1)
            self.assertIn("input.video", errors.getvalue())
            self.assertIn("llm.model", errors.getvalue())
            post.assert_not_called()
            worker.assert_not_called()

    def test_incompatible_pyav_is_rejected_before_model_download(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["llm"].update(model="test/model", api_key_env="")
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.touch()
            with patch("analyze_recording.importlib.util.find_spec", return_value=object()), patch("analyze_recording.importlib.metadata.version", return_value="19.0.1"), patch("analyze_recording.shutil.which", return_value="program"):
                errors = preflight(config, video, None, False)
            self.assertTrue(any("PyAV 19" in error for error in errors))

    def test_task_lock_rejects_duplicate_and_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            with self.assertRaisesRegex(RuntimeError, "test exit"):
                with job_lock(path):
                    with self.assertRaisesRegex(SelectionError, "已有任务"):
                        with job_lock(path):
                            self.fail("duplicate task acquired lock")
                    raise RuntimeError("test exit")
            with job_lock(path):
                pass

    def test_invalid_evidence_gets_repaired_then_reused_from_cache(self):
        class RepairModel(FakeModel):
            def __init__(self):
                super().__init__()
                self.feedback = []

            def complete(self, prompt, payload):
                if "candidate" not in payload and "validation_feedback" not in payload:
                    self.calls += 1
                    bad = candidate()
                    bad["evidence"][0]["quote"] = "不是原文"
                    return {"clips": [bad]}
                if "validation_feedback" in payload:
                    self.feedback.append(payload["validation_feedback"])
                return super().complete(prompt, payload)

        with tempfile.TemporaryDirectory() as folder:
            model = RepairModel()
            result = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(model.calls, 3)
            self.assertTrue(model.feedback)
            HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(model.calls, 3)

    def test_gpu_failure_falls_back_to_cpu_and_caches_actual_runtime(self):
        devices = []

        def worker(video, runtime, folder):
            devices.append(runtime["device"])
            if runtime["device"] == "cuda":
                raise SelectionError("native CUDA process failed")
            (folder / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\ntest\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as folder, patch("core.local_transcription.importlib.util.find_spec", return_value=object()), patch("core.local_transcription.run_worker", side_effect=worker):
            target = transcribe(Path(folder) / "source.mp4", {}, DEFAULT_CONFIG["asr"], Path(folder))
            transcribe(Path(folder) / "source.mp4", {}, DEFAULT_CONFIG["asr"], Path(folder))
            self.assertEqual(devices, ["cuda", "cpu"])
            receipt = json.loads((target.parent / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["runtime"]["device"], "cpu")
            self.assertEqual(receipt["runtime"]["compute_type"], "int8")

    def test_forced_cuda_does_not_silently_switch_backend(self):
        config = dict(DEFAULT_CONFIG["asr"], device="cuda")
        with tempfile.TemporaryDirectory() as folder, patch("core.local_transcription.importlib.util.find_spec", return_value=object()), patch("core.local_transcription.run_worker", side_effect=SelectionError("CUDA unavailable")) as worker:
            with self.assertRaisesRegex(SelectionError, "未完成"):
                transcribe(Path(folder) / "source.mp4", {}, config, Path(folder))
            self.assertEqual(worker.call_count, 1)

    def test_native_worker_exit_is_reported_without_killing_parent(self):
        process = MagicMock()
        process.stdout = io.StringIO("worker progress\n")
        process.wait.return_value = -1073741515
        with tempfile.TemporaryDirectory() as folder, patch("core.local_transcription.subprocess.Popen", return_value=process) as run:
            with self.assertRaisesRegex(SelectionError, "退出码"):
                run_worker(Path(folder) / "source.mp4", dict(DEFAULT_CONFIG["asr"], device="cuda"), Path(folder))
            command = run.call_args.args[0]
            self.assertIn("core.asr_worker", command)
            self.assertTrue((Path(folder) / "asr-cuda-error.log").exists())
            self.assertIn("HF_HUB_DISABLE_XET", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
