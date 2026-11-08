import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyze_recording import DEFAULT_CONFIG, load_config, main, write_report
from core.highlight_selection import HighlightPipeline, SelectionError, validate_min_score
from test_recording_pipeline import FakeModel, SUBS, candidate, selection_config


def reviewed(score=85):
    return {"keep": True, "boundary_complete": True, "start_id": 1, "end_id": 3,
            "boundary_reason": "保留演示前因与失败回应",
            "quality_review": {"score": score, "reason": "具体保证与结果形成反差，铺垫简短"}}


class HighlightQualityTests(unittest.TestCase):
    def run_pipeline(self, model, threshold=85):
        config = selection_config()
        config["min_score"] = threshold
        with tempfile.TemporaryDirectory() as folder:
            return HighlightPipeline(model, config, folder).run(SUBS, 10000)

    def test_low_initial_score_abstains_without_review_or_retry(self):
        model = FakeModel(select={"clips": [dict(candidate(), score=70)]})
        result = self.run_pipeline(model)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["clips"], [])
        self.assertEqual(result["failures"], [])
        self.assertEqual(model.calls, 1)
        self.assertEqual(result["rejected"][0]["phase"], "select")
        self.assertEqual(result["rejected"][0]["score"], 70)

    def test_complete_but_ordinary_event_can_fail_quality_review(self):
        model = FakeModel(select={"clips": [dict(candidate(), score=95)]}, boundary=reviewed(62))
        result = self.run_pipeline(model)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["clips"], [])
        self.assertEqual(result["failures"], [])
        self.assertEqual(model.calls, 2)
        rejection = result["rejected"][0]
        self.assertEqual(rejection["phase"], "boundary")
        self.assertEqual(rejection["quality_review"]["score"], 62)
        self.assertEqual(rejection["selection_score"], 95)

    def test_exact_threshold_passes_and_review_cannot_inflate_initial_score(self):
        result = self.run_pipeline(FakeModel(boundary=reviewed(95)))
        clip = result["clips"][0]
        self.assertEqual(clip["score"], 85)
        self.assertEqual(clip["selection_score"], 85)
        self.assertEqual(clip["quality_review"]["score"], 95)
        self.assertEqual(len(result["clips"]), 1)  # max_clips is only an upper bound.
        self.assertEqual(result["quality_policy"]["min_score"], 85)

    def test_missing_or_invalid_review_remains_failure_not_empty_success(self):
        invalid = [None, {}, {"score": True, "reason": "普通"},
                   {"score": float("nan"), "reason": "普通"},
                   {"score": 101, "reason": "普通"}, {"score": 85, "reason": " "}]
        for quality in invalid:
            with self.subTest(quality=quality):
                response = reviewed()
                response["quality_review"] = quality
                model = FakeModel(boundary=response)
                result = self.run_pipeline(model)
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["clips"], [])
                self.assertEqual(result["failures"][0]["stage"], "boundary")
                self.assertIn("quality_review", result["failures"][0]["reason"])
                self.assertEqual(model.calls, 3)

    def test_low_score_does_not_bypass_evidence_validation(self):
        raw = candidate()
        raw["score"] = 12
        raw["evidence"][0]["quote"] = "原文中根本没有这句话"
        model = FakeModel(select={"clips": [raw]})
        result = self.run_pipeline(model)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("引文", result["failures"][0]["reason"])
        self.assertEqual(model.calls, 2)

    def test_cross_cue_quote_retry_receives_actual_single_cue_text(self):
        class RepairModel(FakeModel):
            def complete(self, prompt, payload):
                if "candidate" in payload:
                    return super().complete(prompt, payload)
                self.calls += 1
                if "validation_feedback" in payload:
                    self.feedback = payload["validation_feedback"]
                    return {"clips": [candidate()]}
                raw = candidate()
                raw["evidence"][0]["quote"] = SUBS[1].text + SUBS[2].text
                return {"clips": [raw]}

        model = RepairModel()
        result = self.run_pipeline(model)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(model.calls, 3)
        self.assertIn(repr(SUBS[1].text), model.feedback)
        self.assertIn("不能跨字幕拼接", model.feedback)
        self.assertEqual(result["clips"][0]["evidence"][0]["quote"], SUBS[1].text)

    def test_missing_initial_score_is_invalid_instead_of_a_low_quality_abstention(self):
        raw = candidate()
        del raw["score"]
        result = self.run_pipeline(FakeModel(select={"clips": [raw]}))
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("score", result["failures"][0]["reason"])

    def test_threshold_changes_reuse_scores_without_new_model_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(select={"clips": [dict(candidate(), score=95)]}, boundary=reviewed(88))
            config = selection_config()
            results = []
            for threshold in (85, 90, 85):
                config["min_score"] = threshold
                results.append(HighlightPipeline(model, config, folder).run(SUBS, 10000))
            self.assertEqual([len(r["clips"]) for r in results], [1, 0, 1])
            self.assertEqual(model.calls, 2)
            self.assertEqual(results[0]["clips"][0]["score"], 88)

    def test_review_does_not_receive_initial_score_or_threshold(self):
        class Capture(FakeModel):
            def complete(self, prompt, payload):
                if "candidate" in payload:
                    self.review_payload = payload
                return super().complete(prompt, payload)

        model = Capture()
        self.run_pipeline(model)
        self.assertNotIn("score", model.review_payload["candidate"])
        self.assertNotIn("reason", model.review_payload["candidate"])
        self.assertNotIn("min_score", model.review_payload)

    def test_threshold_validated_for_configs_and_direct_pipeline_callers(self):
        self.assertEqual(DEFAULT_CONFIG["selection"]["min_score"], 85)
        for valid in (0, 85, 100):
            self.assertEqual(validate_min_score(valid), valid)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for invalid in (-1, 101, True, 85.5, "85", None):
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps({"selection": {"min_score": invalid}}), encoding="utf-8")
                    with self.assertRaisesRegex(SelectionError, "min_score"):
                        load_config(path)
                    with self.assertRaisesRegex(SelectionError, "min_score"):
                        self.run_pipeline(FakeModel(), invalid)

    def test_cli_override_and_restyling_cannot_pretend_to_reselect(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text("{}", encoding="utf-8")
            with patch("analyze_recording.load_local_key"), patch("analyze_recording.preflight", return_value=[]) as check, \
                    patch("analyze_recording.probe_video", return_value={"has_audio": True}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(path), "--min-score", "90", "--check"]), 0)
            self.assertEqual(check.call_args.args[0]["selection"]["min_score"], 90)
            for mode in ("--package-only", "--restyle", "--captions-only", "--prepare-only"):
                with self.subTest(mode=mode), patch("analyze_recording.package_existing") as package, \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--config", str(path), "--min-score", "90", mode]), 1)
                    package.assert_not_called()

    def test_report_shows_threshold_scores_and_escaped_review(self):
        result = self.run_pipeline(FakeModel())
        result["clips"][0]["quality_review"]["reason"] = "<script>unsafe</script>"
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            write_report(output, Path("source.mp4").resolve(), result)
            page = (output / "report.html").read_text(encoding="utf-8")
            self.assertIn("看点门槛：85", page)
            self.assertIn("初选 85 / 复核 85", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertNotIn("<script>", page)

    def test_zero_qualifying_clips_never_reach_encoding_or_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            srt = root / "input.srt"
            srt.write_text("1\n00:00:00,700 --> 00:00:02,000\n普通问候\n", encoding="utf-8")
            output = root / "output"
            config = root / "config.json"
            config.write_text(json.dumps({"llm": {"api_key_env": ""},
                                          "selection": {"min_clip_seconds": 1},
                                          "correction": {"enabled": False}}), encoding="utf-8")
            video = root / "input.mp4"
            video.touch()
            raw = dict(candidate(), start_id=1, end_id=1, score=40,
                       evidence=[{"subtitle_id": 1, "quote": "普通问候", "role": role}
                                 for role in ("trigger", "payoff")])
            model = FakeModel(select={"clips": [raw]})
            with patch("analyze_recording.preflight", return_value=[]), \
                    patch("analyze_recording.probe_video", return_value={"duration_ms": 10000, "has_audio": True}), \
                    patch("analyze_recording.ChatModel", return_value=model), \
                    patch("analyze_recording.render_clip") as render, \
                    patch("analyze_recording.prepare_editorial") as editorial, \
                    patch("analyze_recording.publish_clip") as publish, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(config), "--video", str(video),
                                       "--srt", str(srt), "--output", str(output)]), 0)
            render.assert_not_called()
            editorial.assert_not_called()
            publish.assert_not_called()
            result = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(result["clips"], [])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(json.loads((output / "posting_manifest.json").read_text(encoding="utf-8"))["items"], [])
            self.assertEqual(model.calls, 1)


if __name__ == "__main__":
    unittest.main()
