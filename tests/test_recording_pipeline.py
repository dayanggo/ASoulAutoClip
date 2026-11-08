import contextlib
import copy
import hashlib
import http.server
import io
import importlib.machinery
import json
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from analyze_recording import DEFAULT_CONFIG, load_config, main, transcribe, write_report
from core.highlight_model import ChatModel, parse_json_response
from core.clip_publication import cover_composition, decorate_event_yellow, find_font, generate_publication, reaction_scene
from core.clip_editorial import edit_clip, validate_editorial
from PIL import Image
from core.highlight_render import (caption_display_text, caption_line_length, clip_srt,
                                   emphasize_ass, probe_video, render_clip)
from core.highlight_selection import (HighlightPipeline, SelectionError, Subtitle, build_windows,
                                      finalize_boundary, read_subtitles, selection_windows,
                                      validate_candidate, validate_excluded_ranges)
from core.local_transcription import transcribe_once


SUBS = [Subtitle(1, 700, 2000, "我给大家演示一下。"),
        Subtitle(2, 2400, 4200, "这次一定不会出错。"),
        Subtitle(3, 4700, 7000, "刚说完就出错了。")]


def candidate():
    return {"start_id": 1, "end_id": 3, "title": "演示反转", "score": 85,
            "setup": "开始演示", "turning_point": "自信保证", "payoff": "刚保证就出错",
            "reason": "承诺与结果形成反转", "evidence": [
                {"subtitle_id": 2, "quote": "这次一定不会出错。", "role": "trigger"},
                {"subtitle_id": 3, "quote": "刚说完就出错了。", "role": "payoff"}]}


def selection_config():
    config = copy.deepcopy(DEFAULT_CONFIG["selection"])
    config.update(min_clip_seconds=1, max_clip_seconds=10)
    return config


def editorial_result():
    return {"titles": [{"text": "刚保证不会出错，转头就出错了", "support_ids": [2, 3]}],
            "cover_lines": [{"text": "一定不会出错", "subtitle_id": 2, "quote": "这次一定不会出错。"},
                            {"text": "刚说完就出错了", "subtitle_id": 3, "quote": "刚说完就出错了。"}],
            "frame_subtitle_id": 3, "focus_subtitle_ids": [2, 3]}


class FakeModel:
    cache_identity = {"model": "offline-fixture"}

    def __init__(self, select=None, boundary=None):
        self.calls = 0
        self.select = select
        self.boundary = boundary

    def complete(self, prompt, payload):
        self.calls += 1
        if payload.get("task") == "publication_editorial":
            result = editorial_result()
            result["cover_text"] = "，".join(x["text"] for x in result["cover_lines"])
            return result
        if payload.get("task") == "publication_copy_review":
            return copy.deepcopy(payload["draft"])
        if "candidate" in payload:
            return self.boundary if self.boundary is not None else {
                "keep": True, "boundary_complete": True, "start_id": 1, "end_id": 3,
                "boundary_reason": "从演示开始到反转结果结束",
                "quality_review": {"score": 85, "reason": "明确的自信保证与实际结果形成反差，铺垫简短"}}
        return self.select if self.select is not None else {"clips": [candidate()]}


class SelectionTests(unittest.TestCase):
    def test_display_correction_is_explicit_single_pass_and_preserves_source(self):
        source = [Subtitle(1, 700, 2000, "冉总说然总大气")]
        corrected, changes = caption_display_text(source, {"冉总": "然总", "然总": "老板"})
        self.assertEqual(corrected[0].text, "然总说老板大气")
        self.assertEqual(source[0].text, "冉总说然总大气")
        self.assertEqual((corrected[0].id, corrected[0].start_ms, corrected[0].end_ms), (1, 700, 2000))
        self.assertEqual(changes[0]["before"], source[0].text)
        from core.highlight_render import DEFAULT_STYLE
        self.assertEqual(caption_line_length(dict(DEFAULT_STYLE, font_size=84, emphasis_mode="none")), 14)
        for invalid in ({"": "然总"}, {"冉总": ""}, {"冉总": 1}):
            with self.assertRaises(SelectionError):
                caption_display_text(source, invalid)

    def test_additional_batch_excludes_old_speech_without_joining_its_neighbors(self):
        config = selection_config()
        config["exclude_ranges_ms"] = [[2400, 4200]]
        windows = selection_windows(SUBS, config)
        self.assertEqual([[s.id for s in w.subtitles] for w in windows], [[1], [3]])
        self.assertEqual(windows[0].subtitles[0].start_ms, 700)
        self.assertEqual(windows[1].subtitles[0].end_ms, 7000)
        for value in (None, [[2, 1]], [[-1, 5]], [[0, True]], [[0, 1.5]], ["bad"]):
            with self.assertRaises(SelectionError):
                validate_excluded_ranges(value)

    def test_boundary_cannot_extend_a_new_clip_back_into_previously_exported_speech(self):
        config = selection_config()
        config.update(max_clip_seconds=20, exclude_ranges_ms=[[700, 2000]])
        raw = candidate()
        raw["start_id"] = 2
        # Candidate 2-3 is new; a boundary reviewer incorrectly re-adds old cue 1.
        model = FakeModel(select={"clips": [raw]})
        with tempfile.TemporaryDirectory() as folder:
            result = HighlightPipeline(model, config, folder).run(SUBS, 10000)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["clips"], [])
        self.assertEqual(result["rejected"][-1]["stage"], "exclude")

    def test_editorial_caption_is_distinct_from_verified_quotation(self):
        raw = editorial_result()
        raw["cover_lines"] = [{"kind": "caption", "text": "自信保证？", "support_ids": [2]},
                              {"kind": "quote", "text": "就出错了！", "subtitle_id": 3}]
        raw["focus_phrases"] = [{"subtitle_id": 3, "text": "出错了"}]
        before = copy.deepcopy(raw)
        value = validate_editorial(raw, SUBS)
        self.assertEqual(raw, before)
        self.assertNotIn("quote", value["cover_lines"][0])
        self.assertEqual(value["cover_lines"][0]["support_quotes"], [SUBS[1].text])
        self.assertEqual(value["cover_lines"][1]["quote"], SUBS[2].text)
        for changes in ({"support_ids": [99]}, {"text": "“自信保证”"}, {"text": "全场沸腾！"}):
            altered = copy.deepcopy(raw)
            altered["cover_lines"][0].update(changes)
            with self.assertRaises(SelectionError):
                validate_editorial(altered, SUBS)
        raw["focus_phrases"][0]["text"] = "观众破防了"
        with self.assertRaises(SelectionError):
            validate_editorial(raw, SUBS)

    def test_source_crop_and_reviewed_frame_are_bounded(self):
        clip = {"start_ms": 1000, "end_ms": 2000,
                "cover_composition": {"focus": [0, 1], "zoom": 3, "text_side": "right", "frame_ms": 1500}}
        composition = cover_composition(clip)
        # Crop must stay inside the source; padding would add black pixels.
        image = reaction_scene(Image.new("RGB", (640, 480), "red"), composition)
        self.assertEqual(image.size, (1280, 720))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        for invalid in ({"frame_ms": 2000}, {"zoom": float("nan")}, {"focus": [float("nan"), .5]},
                        {"text_side": "unknown"}):
            altered = copy.deepcopy(clip)
            altered["cover_composition"].update(invalid)
            with self.assertRaises(SelectionError):
                cover_composition(altered)

    def test_phrase_emphasis_preserves_words_times_and_wraps(self):
        text = "甲" * 22 + "谁叫你卖dlc的" + "乙" * 5
        subs = [Subtitle(1, 0, 5000, text)]
        clip = {"start_ms": 0, "end_ms": 5000, "editorial": {
            "focus_subtitle_ids": [1], "focus_phrases": [{"subtitle_id": 1, "text": "谁叫你卖dlc的"}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.ass"
            from core.subtitle_utils import SubtitleUtils
            from core.highlight_render import DEFAULT_STYLE
            SubtitleUtils({"subtitle": DEFAULT_STYLE}).create_ass_file(
                [{"start": 0, "end": 5, "text": text}], path, 0, 5, 24)
            before = path.read_text(encoding="utf-8-sig").split("Dialogue:")[1].strip().split(",", 9)[:9]
            emphasize_ass(path, subs, clip, "phrase")
            dialogue = path.read_text(encoding="utf-8-sig").split("Dialogue:")[1].strip()
            self.assertEqual(dialogue.split(",", 9)[:9], before)
            self.assertNotIn(r"\fscx112", dialogue)
            self.assertIn(r"\c&H00FFEB31&", dialogue)
            import re
            literal = re.sub(r"\{[^}]*\}", "", dialogue.split(",", 9)[9])
            self.assertEqual(literal.replace(r"\N", ""), text)
            self.assertEqual(literal.index(r"\N"), 24)

    def test_editorial_rejects_invented_quotes_and_outside_cues(self):
        for field in ("cover_lines", "titles", "focus_subtitle_ids"):
            raw = editorial_result()
            if field == "cover_lines":
                raw[field][0]["text"] = "全场笑疯了"
                raw[field][0]["quote"] = "全场笑疯了"
            elif field == "titles":
                raw[field][0]["text"] = "她说“绝对不可能失败”，然后出错了"
            else:
                raw[field] = [99]
            with self.assertRaises(SelectionError):
                validate_editorial(raw, SUBS)

    def test_single_quotation_marks_cannot_bypass_title_evidence_checks(self):
        for left, right in (("‘", "’"), ("『", "』"), ("'", "'")):
            raw = editorial_result()
            raw["titles"] = [{"text": f"她说{left}绝对不可能失败{right}，然后出错了", "support_ids": [2,3]}]
            with self.assertRaisesRegex(SelectionError, "引语与证据不一致"):
                validate_editorial(raw, SUBS)
            raw["titles"][0]["text"] = f"她说{left}一定不会出错{right}，然后出错了"
            self.assertTrue(validate_editorial(raw, SUBS)["titles"])

    def test_title_quotation_can_join_only_cited_consecutive_cues_without_a_pause(self):
        adjacent = [SUBS[0], SUBS[1], Subtitle(3, 4300, 7000, "刚说完就出错了。")]
        raw = editorial_result()
        raw["titles"] = [{"text": "她说“不会出错刚说完”，随后出错了", "support_ids": [2,3]}]
        self.assertTrue(validate_editorial(raw, adjacent)["titles"])
        with self.assertRaises(SelectionError):
            validate_editorial(raw, SUBS)
        raw["titles"][0]["support_ids"] = [2]
        with self.assertRaises(SelectionError):
            validate_editorial(raw, adjacent)

    def test_transcript_cannot_establish_visible_confusion_or_audience_reactions(self):
        for term in ("懵了", "全场问号", "敷衍了事"):
            raw = editorial_result()
            raw["titles"][0]["text"] = "她演示失误后" + term
            with self.assertRaisesRegex(SelectionError, "动作或情绪"):
                validate_editorial(raw, SUBS)

    def test_event_yellow_type_stays_in_safe_area_with_one_color(self):
        from PIL import ImageDraw
        original_text = ImageDraw.ImageDraw.text
        font_path = find_font()
        with tempfile.TemporaryDirectory() as folder:
            for side in ("left", "right", "bands"):
                with self.subTest(side=side):
                    drawn = []
                    def record(draw, xy, text, **kwargs):
                        box = draw.textbbox(xy, text, font=kwargs["font"], stroke_width=kwargs["stroke_width"])
                        drawn.append((box, kwargs["fill"], kwargs["stroke_fill"]))
                        return original_text(draw, xy, text, **kwargs)
                    path = Path(folder)/(side+".jpg")
                    with patch.object(ImageDraw.ImageDraw, "text", new=record):
                        decorate_event_yellow(Image.new("RGB",(1920,1080),"blue"),
                            ["具体事件和有来有回的互动原话", "一句关键回应", "这条标题最多可以有十六个汉字"],
                            font_path, path, {"focus":None,"zoom":1,"text_side":side})
                    self.assertEqual(len(drawn), 3)
                    for (left,top,right,bottom), fill, stroke in drawn:
                        self.assertTrue(0 <= left < right <= 1280)
                        self.assertTrue(0 <= top < bottom <= 720)
                        self.assertEqual((fill,stroke), ("#FFE100","#080808"))
                    with Image.open(path) as image:
                        self.assertEqual(image.size,(1280,720))

    def test_editorial_cache_retains_real_cue_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = dict(candidate(), start_ms=500, end_ms=7500)
            model = FakeModel()
            first = edit_clip(model, "录播.mp4", clip, SUBS, folder)
            second = edit_clip(model, "录播.mp4", clip, SUBS, folder)
            self.assertEqual(model.calls, 2)
            self.assertEqual(first, second)
            self.assertEqual(first["frame_ms"], 5850)
            self.assertEqual(first["focus_cues"][0]["start_ms"], 2400)

    def test_editorial_short_quote_fallback_and_review_are_evidence_checked(self):
        raw = editorial_result()
        raw["cover_lines"][0]["text"] = "随意改写的标题"
        fixed = validate_editorial(raw, SUBS)
        self.assertEqual(fixed["cover_lines"][0]["text"], SUBS[1].text)
        clip = dict(candidate(), start_ms=500, end_ms=7500)
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel()
            result = edit_clip(model, "录播.mp4", clip, SUBS, folder, editorial_result())
            self.assertEqual(model.calls, 0)
            self.assertEqual(result["origin"], "local_review")
            self.assertEqual(result["frame_ms"], 5850)

    def test_cover_quote_can_span_only_consecutive_continuous_cues(self):
        raw = editorial_result()
        raw["cover_lines"] = [{"text": "不会出错刚说完", "subtitle_id": 2, "quote": "故意误抄"}]
        adjacent = [SUBS[0], SUBS[1], Subtitle(3, 4300, 7000, "刚说完就出错了。")]
        fixed = validate_editorial(copy.deepcopy(raw), adjacent)
        self.assertEqual(fixed["cover_lines"][0]["subtitle_ids"], [2, 3])
        with self.assertRaises(SelectionError):
            validate_editorial(copy.deepcopy(raw), SUBS)

    def test_srt_bom_crlf_and_milliseconds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.srt"
            path.write_bytes(b"\xef\xbb\xbf7\r\n00:00:00,700 --> 00:00:02,000\r\nhello\r\n\r\n")
            result = read_subtitles(path, 10000)
            self.assertEqual((result[0].id, result[0].start_ms, result[0].end_ms), (1, 700, 2000))

    def test_bad_srt_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.srt"
            for text in ("bad input", "1\n00:00:00,000 --> 00:00:11,000\ntest"):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(SelectionError):
                    read_subtitles(path, 10000)

    def test_windows_cover_every_id_with_overlap(self):
        subs = [Subtitle(i + 1, i * 1000, i * 1000 + 800, "hello") for i in range(20)]
        windows = build_windows(subs, seconds=5, overlap_seconds=2)
        self.assertEqual({sub.id for window in windows for sub in window.subtitles}, set(range(1, 21)))
        self.assertTrue(set(sub.id for sub in windows[0].subtitles) & set(sub.id for sub in windows[1].subtitles))
        self.assertLess(len(windows), len(subs))

    def test_character_limited_windows_still_cover_all_ids(self):
        windows = build_windows(SUBS, max_chars=130)
        self.assertEqual([sub.id for window in windows for sub in window.subtitles], [1, 2, 3])

    def test_dense_transcripts_do_not_advance_one_cue_per_window(self):
        subs = [Subtitle(i + 1, i * 1000, i * 1000 + 800, "hello") for i in range(100)]
        windows = build_windows(subs, max_chars=500, overlap_seconds=180)
        self.assertLess(len(windows), 70)
        self.assertEqual({sub.id for window in windows for sub in window.subtitles}, set(range(1, 101)))

    def test_hallucinated_evidence_is_rejected(self):
        raw = candidate()
        raw["evidence"][0]["quote"] = "这里没有出现的话"
        with self.assertRaisesRegex(SelectionError, "不一致"):
            validate_candidate(raw, SUBS, 1000, 10000)

    def test_out_of_window_ids_and_boolean_ids_rejected(self):
        for end in (999, True, "3"):
            raw = dict(candidate(), end_id=end)
            with self.assertRaises(SelectionError):
                validate_candidate(raw, SUBS, 1000, 10000)

    def test_both_trigger_and_payoff_required(self):
        raw = candidate()
        raw["evidence"] = raw["evidence"][:1]
        with self.assertRaisesRegex(SelectionError, "同时"):
            validate_candidate(raw, SUBS, 1000, 10000)

    def test_handles_do_not_include_adjacent_speech(self):
        subs = [Subtitle(1, 0, 650, "previous"), *[Subtitle(s.id + 1, s.start_ms, s.end_ms, s.text) for s in SUBS],
                Subtitle(5, 7100, 8000, "next")]
        result = finalize_boundary({"start_id": 2, "end_id": 4}, subs, 10000)
        self.assertEqual((result["start_ms"], result["end_ms"]), (650, 7100))

    def test_subtitle_export_keeps_milliseconds(self):
        text = clip_srt(SUBS, 500, 7500)
        self.assertIn("00:00:00,200 --> 00:00:01,500", text)
        self.assertIn("刚说完就出错了。", text)

    def test_pipeline_caches_both_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel()
            first = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            second = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(model.calls, 2)
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["clips"][0]["timestamp"], "00:00:00.500-00:00:07.500")

    def test_prompt_changes_invalidate_only_affected_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            prompts = Path(folder) / "prompts"
            shutil.copytree(Path(__file__).resolve().parents[1] / "resources/prompts", prompts)
            model = FakeModel()
            HighlightPipeline(model, selection_config(), folder, prompts).run(SUBS, 10000)
            target = prompts / "highlight_boundary_zh.txt"
            target.write_text(target.read_text(encoding="utf-8") + "\n更严格地复核", encoding="utf-8")
            HighlightPipeline(model, selection_config(), folder, prompts).run(SUBS, 10000)
            self.assertEqual(model.calls, 3)

    def test_boundary_cannot_cut_away_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(boundary={"keep": True, "boundary_complete": True, "start_id": 1,
                                       "end_id": 2, "boundary_reason": "错误地删掉结果"})
            result = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["clips"], [])

    def test_rejected_event_is_successful_abstention(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(boundary={"keep": False, "boundary_reason": "缺少必要前因"})
            result = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["rejected"]), 1)
            self.assertEqual(result["clips"], [])

    def test_no_qualifying_event_can_return_zero_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(select={"clips": []})
            result = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["clips"], [])
            self.assertEqual(model.calls, 1)

    def test_duration_only_rejections_are_retried_then_cached_as_abstention(self):
        config = selection_config()
        config["min_clip_seconds"] = 8
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel()
            first = HighlightPipeline(model, config, folder).run(SUBS, 10000)
            second = HighlightPipeline(model, config, folder).run(SUBS, 10000)
            self.assertEqual(model.calls, config["validation_attempts"])
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["clips"], [])
            self.assertIn("6.300", first["rejected"][0]["reason"])

    def test_duration_repair_can_recover_valid_candidate(self):
        class RepairDurationModel(FakeModel):
            def complete(self, prompt, payload):
                if self.calls == 0:
                    self.calls += 1
                    return {"clips": [dict(candidate(), end_id=2)]}
                return super().complete(prompt, payload)

        config = selection_config()
        config["min_clip_seconds"] = 5
        with tempfile.TemporaryDirectory() as folder:
            model = RepairDurationModel()
            result = HighlightPipeline(model, config, folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["clips"]), 1)
            self.assertEqual(model.calls, 3)

    def test_duration_policy_does_not_hide_invalid_evidence(self):
        bad_evidence = candidate()
        bad_evidence["evidence"][0]["quote"] = "这不是原话"
        too_short = dict(candidate(), end_id=2)
        config = selection_config()
        config["min_clip_seconds"] = 5
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(select={"clips": [bad_evidence, too_short]})
            result = HighlightPipeline(model, config, folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["clips"], [])
            self.assertTrue(result["failures"])
            self.assertEqual(model.calls, config["validation_attempts"])

    def test_api_failures_are_not_empty_success(self):
        class Failed(FakeModel):
            def complete(self, *args):
                raise SelectionError("network unavailable")
        with tempfile.TemporaryDirectory() as folder:
            result = HighlightPipeline(Failed(), selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "incomplete")
            self.assertTrue(result["failures"])

    def test_invalid_cached_result_is_invalidated_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            model = FakeModel(select={"wrong": []})
            HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            model.select = None
            result = HighlightPipeline(model, selection_config(), folder).run(SUBS, 10000)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(model.calls, 4)

    def test_json_parser_rejects_prose_and_accepts_fences(self):
        self.assertEqual(parse_json_response('```json\n{"clips":[]}\n```'), {"clips": []})
        with self.assertRaises(SelectionError):
            parse_json_response('some reasoning {"clips":[]}')

    def test_secret_is_not_in_model_cache_identity(self):
        with patch.dict("os.environ", {"TEST_CLIP_KEY": "private-test-secret"}):
            model = ChatModel({"base_url": "http://localhost:1234/v1", "model": "test", "api_key_env": "TEST_CLIP_KEY"})
            self.assertNotIn("private-test-secret", json.dumps(model.cache_identity))

    def test_unknown_config_key_fails_before_processing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"selection":{"max_clip_second":30}}', encoding="utf-8")
            with self.assertRaisesRegex(SelectionError, "未知配置字段"):
                load_config(path)

    def test_report_escapes_untrusted_title(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
            clip.update(id="test", title="<script>alert(1)</script>", boundary_reason="完整")
            write_report(Path(folder), Path(folder) / "source#1.mp4", {"status": "complete", "clips": [clip]})
            content = (Path(folder) / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", content)
            self.assertIn("source%231.mp4#t=", content)

    def test_report_clears_stale_posting_manifest_when_no_clips_are_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "posting_manifest.json").write_text('{"items":[{"title":"old"}]}', encoding="utf-8")
            write_report(output, output / "source.mp4", {"status": "complete", "clips": []})
            manifest = json.loads((output / "posting_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"], [])

    def test_asr_cache_without_downloading_models(self):
        calls = []
        class ASR:
            def __init__(self, *args, **kwargs):
                calls.append("loaded")

            def transcribe(self, *args, **kwargs):
                segment = types.SimpleNamespace(start=0.7, end=2, text="测试字幕。", words=[])
                return iter([segment]), types.SimpleNamespace(language="zh")
        module = types.ModuleType("faster_whisper")
        module.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", loader=None)
        module.WhisperModel = ASR
        with tempfile.TemporaryDirectory() as folder, patch.dict("sys.modules", {"faster_whisper": module}), patch("core.local_transcription.run_worker", side_effect=transcribe_once):
            output = Path(folder)
            first = transcribe(output / "source.mp4", {"size": 1}, DEFAULT_CONFIG["asr"], output)
            second = transcribe(output / "source.mp4", {"size": 1}, DEFAULT_CONFIG["asr"], output)
            self.assertEqual(first, second)
            self.assertEqual(calls, ["loaded"])
            self.assertEqual(read_subtitles(first, 10000)[0].text, "测试字幕。")

    def test_asr_empty_output_is_not_a_successful_cache(self):
        class ASR:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, *args, **kwargs):
                return iter([]), types.SimpleNamespace(language="zh")
        module = types.ModuleType("faster_whisper")
        module.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", loader=None)
        module.WhisperModel = ASR
        with tempfile.TemporaryDirectory() as folder, patch.dict("sys.modules", {"faster_whisper": module}), patch("core.local_transcription.run_worker", side_effect=transcribe_once):
            with self.assertRaisesRegex(SelectionError, "有效语音"):
                transcribe(Path(folder) / "source.mp4", {}, DEFAULT_CONFIG["asr"], Path(folder))
            self.assertEqual(list(Path(folder).rglob("receipt.json")), [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
class RealVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="asoul-pipeline-")
        cls.folder = Path(cls.temporary.name) / "中文 O'Brien"
        cls.folder.mkdir()
        cls.video = cls.folder / "录播.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x180:r=10:d=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(cls.video)],
                       check=True, capture_output=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_render_and_failed_reencode_preserves_old_video(self):
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="atomic-test", boundary_reason="完整")
        output = self.folder / "输出 O'Brien"
        target = render_clip(self.video, {"test": 1}, SUBS, clip, output)
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        self.assertAlmostEqual(probe_video(target)["duration_ms"], 7000, delta=300)
        original_run = subprocess.run

        def fail_encode(command, **kwargs):
            if command[0] == "ffmpeg":
                raise subprocess.CalledProcessError(1, command, stderr=b"synthetic encode failure")
            return original_run(command, **kwargs)

        with patch("core.highlight_render.subprocess.run", side_effect=fail_encode):
            with self.assertRaisesRegex(SelectionError, "旧视频保留"):
                render_clip(self.video, {"test": 1}, SUBS, clip, output, {"font_size": 73})
        self.assertEqual(before, hashlib.sha256(target.read_bytes()).hexdigest())

    def test_emphasis_is_rendered_but_title_changes_reuse_video(self):
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="editorial-test", editorial=editorial_result())
        output = self.folder / "editorial-output"
        path = render_clip(self.video, {"test": 1}, SUBS, clip, output, {"emphasis_mode": "cue"})
        ass = path.with_name("captions.generated.ass").read_text(encoding="utf-8-sig")
        self.assertEqual(ass.count(r"\fscx112"), 2)
        self.assertAlmostEqual(probe_video(path)["duration_ms"], 7000, delta=300)
        clip["title"] = "只改文案，不改变镜头"
        with patch("core.highlight_render.SubtitleUtils.create_ass_file", side_effect=AssertionError("must reuse encoded video")):
            self.assertEqual(render_clip(self.video, {"test": 1}, SUBS, clip, output, {"emphasis_mode": "cue"}), path)

    def test_none_emphasis_reuses_video_when_editorial_focus_changes(self):
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="no-emphasis-focus", editorial=editorial_result())
        output = self.folder / "no-emphasis-output"
        path = render_clip(self.video, {"test":1}, SUBS, clip, output, {"emphasis_mode":"none"})
        clip["editorial"]["focus_subtitle_ids"] = [1]
        clip["editorial"]["focus_phrases"] = [{"subtitle_id":1,"text":"演示"}]
        with patch("core.highlight_render.SubtitleUtils.create_ass_file", side_effect=AssertionError("must reuse video")):
            self.assertEqual(render_clip(self.video, {"test":1}, SUBS, clip, output, {"emphasis_mode":"none"}),path)

    def test_old_none_emphasis_receipt_migrates_without_encoding(self):
        from core.caption_layout import layout_captions
        from core.highlight_selection import fingerprint
        from core.highlight_render import DEFAULT_STYLE
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="old-no-emphasis", editorial=editorial_result())
        output = self.folder / "old-no-emphasis-output"
        path = render_clip(self.video, {"test":1}, SUBS, clip, output)
        receipt = path.with_name("render.json")
        meta = json.loads(receipt.read_text(encoding="utf-8"))
        correct_fingerprint = meta["fingerprint"]
        captions,_ = layout_captions(SUBS,DEFAULT_STYLE,{"test":1})
        old_content = {name:clip[name] for name in ("id","start_ms","end_ms")}
        old_content.update(emphasis_ids=clip["editorial"]["focus_subtitle_ids"],emphasis_phrases=[])
        meta["fingerprint"] = fingerprint({"version":6,"source":{"test":1},"clip":old_content,
            "style":meta["subtitle_style"],"encoder":"libx264",
            "captions":[(s.start_ms,s.end_ms,s.text) for s in captions],
            "subtitles":[(s.start_ms,s.end_ms,s.text) for s in SUBS]})
        receipt.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
        clip["editorial"]["focus_subtitle_ids"] = [1]
        with patch("core.highlight_render.SubtitleUtils.create_ass_file", side_effect=AssertionError("must migrate receipt")):
            render_clip(self.video, {"test":1}, SUBS, clip, output)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["fingerprint"], correct_fingerprint)

    def test_uniform_large_subtitles_use_reviewed_spelling_without_colored_spans(self):
        source = [Subtitle(1, 700, 2000, "冉总我今天把前司开除了可以来拍卖行上班吗还有没有适合我的岗位")]
        clip = {"id": "subtitle-display-test", "start_ms": 500, "end_ms": 2500,
                "editorial": {"focus_subtitle_ids": [1],
                              "focus_phrases": [{"subtitle_id": 1, "text": "前司开除了"}]}}
        style = {"font_size": 84, "emphasis_mode": "none", "balanced_lines": True,
                 "text_replacements": {"冉总": "然总"}}
        path = render_clip(self.video, {"test": 1}, source, clip, self.folder / "display-output", style)
        text = path.with_name("captions.generated.ass").read_text(encoding="utf-8-sig")
        self.assertIn(",84,", text)
        self.assertNotIn("冉总", text)
        self.assertIn("然总", text)
        dialogue = next(line for line in text.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn(r"\c&", dialogue)
        self.assertNotIn(r"\fsc", dialogue)
        self.assertIn(r"\N", dialogue)
        rows = dialogue.split(",", 9)[9].split(r"\N")
        self.assertLessEqual(max(map(len, rows)) - min(map(len, rows)), 1)
        self.assertIn("然总", path.with_name("captions.generated.srt").read_text(encoding="utf-8"))
        changes = json.loads(path.with_name("captions.corrections.json").read_text(encoding="utf-8"))["changes"]
        self.assertEqual(changes[0]["subtitle_id"], 1)

    def test_stale_recording_override_cannot_move_subtitles_from_original_position(self):
        output = self.folder/'fixed-caption-position'
        output.mkdir()
        (output/'subtitle_style.overrides.json').write_text(json.dumps({
            'source': {'test': 1}, 'style': {'margin_v': 250, 'bold': False}}), encoding='utf-8')
        clip = {'id': 'fixed-position', 'start_ms': 500, 'end_ms': 2500}
        path = render_clip(self.video, {'test': 1}, SUBS, clip, output)
        ass = path.with_name('captions.generated.ass').read_text(encoding='utf-8-sig')
        fields = next(line for line in ass.splitlines() if line.startswith('Style:')).split(',')
        self.assertEqual((fields[2], fields[3], fields[7], fields[16], fields[17], fields[21]),
                         ('84', '&H0000E1FF', '-1', '4', '1', '50'))

    def test_publication_exports_real_images_and_preserves_previous_cover_on_failure(self):
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="aabbccddee11", boundary_reason="完整")
        output = self.folder / "封面输出"
        metadata = generate_publication(self.video, {"test": 1}, clip, output)
        directory = output / "clips" / clip["id"]
        self.assertEqual(len(metadata["cover_candidates"]), 3)
        self.assertIn("这次一定不会出错。", metadata["description"])
        self.assertNotIn(str(self.folder), metadata["description"])
        with Image.open(directory / "cover.jpg") as image:
            self.assertEqual(image.size, (1280, 720))
            image.verify()
        before = (directory / "cover.jpg").read_bytes()
        with patch("core.clip_publication.extract_frame", side_effect=AssertionError("cached cover must be reused")):
            self.assertEqual(generate_publication(self.video, {"test": 1}, clip, output), metadata)
        with patch("core.clip_publication.extract_frame", side_effect=SelectionError("synthetic capture failure")):
            with self.assertRaises(SelectionError):
                generate_publication(self.video, {"test": 1}, dict(clip, title="新标题"), output)
        self.assertEqual(before, (directory / "cover.jpg").read_bytes())

    def test_incomplete_copy_cannot_replace_a_previous_cover_even_in_package_only_path(self):
        clip = finalize_boundary(validate_candidate(candidate(), SUBS, 1000, 10000), SUBS, 10000)
        clip.update(id="copy-protection", editorial=editorial_result())
        output=self.folder/"copy-protection-output"
        generate_publication(self.video,{"test":1},clip,output)
        target=output/"clips"/clip["id"]/"cover.jpg"
        before=target.read_bytes()
        clip["editorial"]["cover_lines"]=[{"text":"这个声音像什么？"},{"text":"确实也像"}]
        with patch("core.clip_publication.extract_frame",side_effect=AssertionError("must reject before extraction")):
            with self.assertRaisesRegex(SelectionError,"缺少具体对象"):
                generate_publication(self.video,{"test":1},clip,output)
        self.assertEqual(target.read_bytes(),before)

    def test_cli_full_flow_with_local_http_fixture(self):
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                payload = json.loads(body["messages"][1]["content"])
                calls.append(payload)
                value = FakeModel().complete("", payload)
                response = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(value, ensure_ascii=False)}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            srt = self.folder / "source.srt"
            srt.write_text(clip_srt(SUBS, 0, 10000).replace("演示", "演试").replace("出错", "出錯"), encoding="utf-8")
            dictionary = self.folder / "纠错.txt"
            dictionary.write_text("演试 演示\n出錯 出错\n", encoding="utf-8")
            config = self.folder / "config.json"
            config.write_text(json.dumps({"llm": {"base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                                    "model": "fixture", "api_key_env": ""},
                                          "selection": selection_config(),
                                          "correction": {"dictionary_path": str(dictionary)}}), encoding="utf-8")
            output = self.folder / "cli-output"
            argv = ["--video", str(self.video), "--srt", str(srt), "--config", str(config), "--output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
                self.assertEqual(main(argv), 0)
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[0]["subtitles"][0]["text"], SUBS[0].text)
            self.assertEqual(calls[0]["subtitles"][2]["text"], SUBS[2].text)
            analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["clips"][0]["render_status"], "complete")
            self.assertTrue((output / "report.html").exists())
            clip_dir = output / "clips" / analysis["clips"][0]["id"]
            self.assertTrue((clip_dir / "description.txt").is_file())
            self.assertTrue((clip_dir / "cover.jpg").is_file())
            video_before = hashlib.sha256((clip_dir / "clip.mp4").read_bytes()).hexdigest()
            with patch("core.highlight_model.requests.post", side_effect=AssertionError("offline packaging cannot call API")), patch("analyze_recording.render_clip", side_effect=AssertionError("packaging cannot encode video")):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(argv + ["--package-only"]), 0)
            self.assertEqual(video_before, hashlib.sha256((clip_dir / "clip.mp4").read_bytes()).hexdigest())
            self.assertTrue(json.loads((output / "Data_source.txt").read_text(encoding="utf-8"))[0]["boundary_locked"])

            # Also exercise the user's video-only route, including ASR reuse.
            loaded = []
            class ASR:
                def __init__(self, *args, **kwargs):
                    loaded.append(True)

                def transcribe(self, *args, **kwargs):
                    segments = [types.SimpleNamespace(start=sub.start_ms / 1000, end=sub.end_ms / 1000,
                                                       text=sub.text.replace("演示", "演试").replace("出错", "出錯"), words=[]) for sub in SUBS]
                    return iter(segments), types.SimpleNamespace(language="zh")
            module = types.ModuleType("faster_whisper")
            module.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", loader=None)
            module.WhisperModel = ASR
            video_only_output = self.folder / "video-only-output"
            video_only_argv = ["--video", str(self.video), "--config", str(config), "--output", str(video_only_output)]
            with patch.dict("sys.modules", {"faster_whisper": module}), patch("core.local_transcription.run_worker", side_effect=transcribe_once), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(video_only_argv), 0)
                self.assertEqual(main(video_only_argv), 0)
            self.assertEqual(loaded, [True])
            self.assertEqual(len(calls), 8)
            video_only_analysis = json.loads((video_only_output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(video_only_analysis["clips"][0]["render_status"], "complete")

            # Config + local credential file are enough, without video/output CLI flags.
            config_only = json.loads(config.read_text(encoding="utf-8"))
            config_only["input"] = {"video": str(self.video), "srt": str(srt),
                                    "output_dir": str(self.folder / "config-only-output")}
            config_only["llm"]["api_key_env"] = "FIXTURE_SILICON_KEY"
            config_only_path = self.folder / "config-only.json"
            config_only_path.write_text(json.dumps(config_only), encoding="utf-8")
            (self.folder / ".env").write_text("FIXTURE_SILICON_KEY=local-fixture-key\n", encoding="utf-8")
            with patch("analyze_recording.PROJECT_ROOT", self.folder), patch.dict("os.environ", {"FIXTURE_SILICON_KEY": ""}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(config_only_path)]), 0)
            self.assertEqual(len(calls), 12)
            self.assertTrue((self.folder / "config-only-output/report.html").exists())

            # A dictionary change reuses raw ASR, feeds new text to selection,
            # and cannot silently restyle evidence selected against old text.
            dictionary.write_text("演试 展示\n出錯 出错\n", encoding="utf-8")
            with patch.dict("sys.modules", {"faster_whisper": module}), patch("core.local_transcription.run_worker", side_effect=AssertionError("dictionary edits must reuse raw ASR")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(video_only_argv + ["--prepare-only"]), 0)
                self.assertEqual(len(calls), 12)
                self.assertEqual(main(video_only_argv + ["--restyle"]), 1)
                self.assertEqual(main(video_only_argv), 0)
            self.assertEqual(len(calls), 16)
            self.assertEqual(calls[-4]["subtitles"][0]["text"], "我给大家展示一下。")
            self.assertEqual(loaded, [True])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
