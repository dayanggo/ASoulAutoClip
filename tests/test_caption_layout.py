import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.caption_layout import aligned_units, layout_captions
from core.highlight_render import DEFAULT_STYLE
from core.highlight_selection import SelectionError, Subtitle


class CaptionLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = {"path": "fixture.mp4", "size": 100, "mtime_ns": 42}
        self.style = dict(DEFAULT_STYLE, caption_mode="phrases")

    def words(self, records):
        path = self.folder / "transcript.words.json"
        path.write_text(json.dumps({"runtime": {"backend": "firered"}, "timestamp_policy": 2,
                                    "segments": records}, ensure_ascii=False), encoding="utf-8")
        path.with_name("receipt.json").write_text(json.dumps({"source": self.source}), encoding="utf-8")
        return path

    def record(self, text, start=1000, step=180):
        return {"start_ms": start, "end_ms": start+len(text)*step, "text": text,
                "words": [{"text": char, "start_ms": start+i*step, "end_ms": start+(i+1)*step}
                          for i, char in enumerate(text)]}

    def test_list_is_shown_as_timed_phrases_without_losing_words(self):
        text = "我看一下每天早睡早起加坚持锻炼加学习新技能"
        record = self.record(text)
        sub = Subtitle(7, record["start_ms"], record["end_ms"], text)
        captions, audit = layout_captions([sub], self.style, self.source, self.words([record]))
        self.assertEqual([c.text for c in captions], ["我看一下", "每天早睡早起", "加坚持锻炼", "加学习新技能"])
        self.assertEqual("".join(c.text for c in captions), text)
        source_starts = {w["start_ms"] for w in record["words"]}
        self.assertTrue(all(c.start_ms in source_starts for c in captions))
        self.assertTrue(all(a.end_ms <= b.start_ms for a, b in zip(captions, captions[1:])))
        self.assertEqual(audit["fallback_cue_ids"], [])

    def test_word_split_by_asr_cue_is_rejoined_before_layout(self):
        a, b = self.record("应该没空看直"), self.record("播吧", start=1000+6*180)
        subs = [Subtitle(i+1, r["start_ms"], r["end_ms"], r["text"]) for i, r in enumerate([a, b])]
        captions, _ = layout_captions(subs, self.style, self.source, self.words([a, b]))
        self.assertEqual([c.text for c in captions], ["应该没空看直播吧"])

    def test_corrected_name_keeps_source_span_instead_of_estimated_char_times(self):
        record = self.record("冉总说话")
        sub = Subtitle(1, record["start_ms"], record["end_ms"], "然总说话")
        units = aligned_units(sub, record)
        self.assertEqual("".join(u.text for u in units), "然总说话")
        self.assertEqual((units[0].start_ms, units[0].end_ms), (1000, 1180))
        # A longer reviewed replacement receives one atomic ORIGINAL span.
        sub = Subtitle(1, record["start_ms"], record["end_ms"], "嘉然总说话")
        units = aligned_units(sub, record)
        self.assertEqual(units[0].text, "嘉然")
        self.assertEqual((units[0].start_ms, units[0].end_ms), (1000, 1180))

    def test_unverified_or_legacy_times_never_generate_guessed_card_times(self):
        record = self.record("一整句应该保留原时间")
        sub = Subtitle(1, record["start_ms"], record["end_ms"], record["text"])
        path = self.words([record])
        for source in (dict(self.source, size=101), self.source):
            if source == self.source:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["timestamp_policy"] = 1
                path.write_text(json.dumps(data), encoding="utf-8")
            captions, audit = layout_captions([sub], self.style, source, path)
            self.assertEqual([(c.start_ms, c.text) for c in captions], [(sub.start_ms, sub.text)])
            self.assertEqual(audit["fallback_cue_ids"], [1])

    def test_bad_word_time_falls_back_and_does_not_drop_text(self):
        record = self.record("不丢字也不估计时间")
        sub = Subtitle(1, record["start_ms"], record["end_ms"], record["text"])
        record["words"][2]["start_ms"] = 0
        captions, audit = layout_captions([sub], self.style, self.source, self.words([record]))
        self.assertEqual(captions[0].text, sub.text)
        self.assertEqual(audit["fallback_cue_ids"], [1])

    def test_legacy_and_modern_defaults_share_only_the_accepted_preferences(self):
        from Auto_clip import CONFIG
        from core.subtitle_style import CAPTION_PREFERENCES
        from core.subtitle_utils import SubtitleUtils
        for style in (DEFAULT_STYLE, CONFIG['subtitle'], SubtitleUtils({}).config['subtitle']):
            for key, value in CAPTION_PREFERENCES.items():
                self.assertEqual(style[key], value)
            self.assertEqual(style['margin_v'], 50)
        self.assertTrue(DEFAULT_STYLE['bold'])
        self.assertEqual((CONFIG['subtitle']['outline_width'], CONFIG['subtitle']['shadow_depth']), (7, 2))

    def test_existing_ass_is_resegmented_with_word_times_at_the_original_position(self):
        from core.subtitle_utils import SubtitleUtils
        text = '我看一下每天早睡早起加坚持锻炼加学习新技能'
        record = self.record(text)
        words = self.words([record])
        path = self.folder/'existing.ass'
        SubtitleUtils({'subtitle': {'caption_mode': 'cue'}}).create_ass_file(
            [{'start': record['start_ms']/1000, 'end': record['end_ms']/1000, 'text': text}],
            path, .2, 6, 30)
        utility = SubtitleUtils({})
        utility.reformat_ass_file(path, 24, .2, self.source, words)
        ass = path.read_text(encoding='utf-8-sig')
        lines = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(r'\N' not in line for line in lines))
        fields = next(line for line in ass.splitlines() if line.startswith('Style:')).split(',')
        self.assertEqual((fields[2], fields[3], fields[7], fields[21]), ('84', '&H0000E1FF', '-1', '50'))
        self.assertEqual(utility.last_layout['fallback_cue_ids'], [])

    def test_ass_centisecond_rounding_uses_real_word_times(self):
        record = self.record('每天早睡早起加坚持锻炼加学习新技能', start=1003)
        sub = Subtitle(1, 1000, record['end_ms']-3, record['text'])
        captions, audit = layout_captions([sub], self.style, self.source, self.words([record]))
        self.assertEqual(captions[0].start_ms, 1003)
        self.assertGreater(len(captions), 1)
        self.assertEqual(audit['fallback_cue_ids'], [])

    def test_regeneration_script_preserves_preferences_and_legacy_outline(self):
        import ast
        from core.regen_script import write_regen_script
        from core.subtitle_style import CAPTION_PREFERENCES
        path = self.folder/'regen_clip.py'
        clip = {'timestamp': '00:00:01-00:00:05', 'title': '测试', 'boundary_locked': True}
        write_regen_script(path, clip, {'subtitle': {'outline_width': 7, 'shadow_depth': 2}})
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        assignments = {node.targets[0].id: node.value for node in tree.body
                       if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        self.assertEqual(ast.literal_eval(assignments['CLIP_DATA']), clip)
        generated = eval(compile(ast.Expression(assignments['CONFIG_OVERRIDES']), str(path), 'eval'),
                         {'USE_DEFAULT': '__USE_DEFAULT__'})['subtitle']
        for key, value in CAPTION_PREFERENCES.items():
            self.assertEqual(generated[key], value)
        self.assertEqual((generated['outline_width'], generated['shadow_depth'], generated['margin_v']), (7, 2, 50))

    def test_legacy_video_processor_uses_word_layout_for_creation_and_existing_ass(self):
        from core.video_processor import VideoProcessor
        from core.highlight_render import clip_srt
        video = self.folder/'source.mp4'
        video.write_bytes(b'fixture')
        stat = video.stat()
        self.source = {'path': str(video.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        record = self.record('我看一下每天早睡早起加坚持锻炼加学习新技能')
        self.words([record])
        srt = self.folder/'source.srt'
        srt.write_text(clip_srt([Subtitle(1, record['start_ms'], record['end_ms'], record['text'])], 0, 5000),
                       encoding='utf-8')
        config = {'output_dir': str(self.folder/'output'), 'source_video': str(video), 'srt_file': str(srt),
                  'padding': {'pre_sentences': 0, 'post_sentences': 0},
                  'subtitle': {'outline_width': 7, 'shadow_depth': 2}}
        with patch('core.video_processor.probe_video',return_value={'width':1920,'height':1080}):
            processor = VideoProcessor(config)
        with patch('core.video_processor.subprocess.run'):
            for _ in range(2):
                processor.process_clip(1, {'title': '测试', 'timestamp': '00:00:00-00:00:05', 'boundary_locked': True},
                                       generate_cover=False)
                ass = self.folder/'output/01_测试/测试.ass'
                lines = [s for s in ass.read_text(encoding='utf-8-sig').splitlines() if s.startswith('Dialogue:')]
                self.assertEqual(len(lines), 4)
                self.assertTrue(all(r'\N' not in line for line in lines))
                self.assertEqual(processor.subtitle_utils.last_layout['fallback_cue_ids'], [])

    def test_corrected_srt_finds_its_matching_raw_word_cache(self):
        from core.caption_layout import find_word_timing_path
        root = self.folder/'job'
        raw = root/'asr/raw'
        raw.mkdir(parents=True)
        words = raw/'transcript.words.json'
        words.write_text('{}', encoding='utf-8')
        corrected = root/'correction/hash/transcript.corrected.srt'
        corrected.parent.mkdir(parents=True)
        corrected.write_text('', encoding='utf-8')
        (root/'transcript.indexed.json').write_text(json.dumps({'srt': str(corrected), 'raw_srt': str(raw/'transcript.srt')}),
                                                   encoding='utf-8')
        self.assertEqual(find_word_timing_path(corrected), words.resolve())
        self.assertIsNone(find_word_timing_path(corrected.with_name('unrelated.srt')))

    def test_caption_cli_does_not_load_api_key_or_request_models(self):
        from analyze_recording import main
        config = self.folder / "config.json"
        config.write_text(json.dumps({"input": {"video": "fixture.mp4", "output_dir": str(self.folder)}}), encoding="utf-8")
        with patch("analyze_recording.refresh_captions", return_value=0) as refresh, \
                patch("analyze_recording.load_local_key", side_effect=AssertionError("must be offline")), \
                patch("analyze_recording.ChatModel", side_effect=AssertionError("must be offline")):
            self.assertEqual(main(["--config", str(config), "--captions-only"]), 0)
        self.assertIsNone(refresh.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
