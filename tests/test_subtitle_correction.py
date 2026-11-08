import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from core.highlight_selection import SelectionError, Subtitle, read_subtitles
from core.subtitle_correction import DictionaryCorrector, correct_subtitles, srt_text


class SubtitleCorrectionTests(unittest.TestCase):
    def test_project_dictionary_corrects_ranzong_without_changing_the_correct_name(self):
        corrector = DictionaryCorrector(Path(__file__).resolve().parents[1] / "resources/dictionaries/asr_dict.txt")
        text, changes = corrector.correct_text("冉总说白送了，然总大气")
        self.assertEqual(text, "然总说白送了，然总大气")
        self.assertEqual(len(changes), 1)

    def test_longest_match_single_pass_and_english_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            dictionary = Path(folder) / "dict.txt"
            dictionary.write_text("背景 北京\n北京图 背景图\n北京 上海\nace asoul\nasoul A-SOUL\n", encoding="utf-8")
            corrector = DictionaryCorrector(dictionary)
            text, changes = corrector.correct_text("北京图 背景 北京 space ace ASOUL")
            self.assertEqual(text, "背景图 北京 上海 space asoul A-SOUL")
            self.assertEqual(len(changes), 5)
            self.assertEqual(changes[0]["matched"], "北京图")

    def test_bom_duplicates_underscores_and_literal_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            dictionary = Path(folder) / "dict.txt"
            dictionary.write_text("# comment\n错词 正词\n错词 正词\n原文 原文\nFire_Red FireRed\n反斜杠 \\1\n", encoding="utf-8-sig")
            corrector = DictionaryCorrector(dictionary)
            text, _ = corrector.correct_text("错词 fire red 反斜杠")
            self.assertEqual(text, "正词 FireRed \\1")
            self.assertEqual(corrector.duplicates, 1)
            self.assertEqual(corrector.noops, 1)

    def test_invalid_and_conflicting_dictionary_stop_before_processing(self):
        with tempfile.TemporaryDirectory() as folder:
            dictionary = Path(folder) / "dict.txt"
            for content in ("只有一列", "错词 正词\n错词 另一词"):
                dictionary.write_text(content, encoding="utf-8")
                with self.assertRaises(SelectionError):
                    DictionaryCorrector(dictionary)

    def test_timecodes_raw_and_audit_preserved_and_dictionary_edits_invalidate(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            dictionary, source = root / "词典.txt", root / "original.srt"
            dictionary.write_text(". 点\n佳然 嘉然\n", encoding="utf-8")
            raw = [Subtitle(1, 1700, 4200, "佳然的2.0"), Subtitle(2, 6000, 7100, "没有修改")]
            source.write_text(srt_text(raw), encoding="utf-8")
            before = source.read_bytes()
            config = {"dictionary_path": str(dictionary)}
            corrected, meta = correct_subtitles(raw, source, root, config)
            self.assertEqual(corrected[0].text, "嘉然的2点0")
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual([(s.id, s.start_ms, s.end_ms) for s in corrected], [(1, 1700, 4200), (2, 6000, 7100)])
            self.assertEqual(read_subtitles(meta["corrected_srt"], 8000), corrected)
            audit = json.loads(Path(meta["audit_path"]).read_text(encoding="utf-8"))
            self.assertEqual(audit[0]["before"], "佳然的2.0")
            self.assertEqual((meta["changed_subtitles"], meta["replacements"]), (1, 2))
            with patch.object(DictionaryCorrector, "correct_text", side_effect=AssertionError("reuse correction cache")):
                self.assertEqual(correct_subtitles(raw, source, root, config), (corrected, meta))
            dictionary.write_text(". 点\n佳然 佳燃\n", encoding="utf-8")
            updated, new_meta = correct_subtitles(raw, source, root, config)
            self.assertEqual(updated[0].text, "佳燃的2点0")
            self.assertNotEqual(meta["fingerprint"], new_meta["fingerprint"])
            self.assertEqual(source.read_bytes(), before)

    def test_ignore_rules_and_disabled_correction(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            dictionary = root / "dict.txt"
            dictionary.write_text("背景 北京\n佳然 嘉然\n", encoding="utf-8")
            raw = [Subtitle(1, 0, 2000, "佳然的背景音乐")]
            source = root / "source.srt"
            source.write_text(srt_text(raw), encoding="utf-8")
            corrected, meta = correct_subtitles(raw, source, root, {"dictionary_path": str(dictionary), "ignore_sources": ["背景"]})
            self.assertEqual(corrected[0].text, "嘉然的背景音乐")
            self.assertEqual(meta["ignored_sources"], ["背景"])
            disabled, meta = correct_subtitles(raw, source, root, {"enabled": False, "dictionary_path": "missing.txt"})
            self.assertEqual(disabled, raw)
            self.assertFalse(meta["enabled"])

    def test_tampered_corrected_cache_is_rebuilt_from_raw(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            dictionary = root / "dict.txt"
            dictionary.write_text("佳然 嘉然", encoding="utf-8")
            raw = [Subtitle(1, 0, 2000, "佳然")]
            source = root / "source.srt"
            source.write_text(srt_text(raw), encoding="utf-8")
            config = {"dictionary_path": str(dictionary)}
            corrected, meta = correct_subtitles(raw, source, root, config)
            Path(meta["corrected_srt"]).write_text("corrupted", encoding="utf-8")
            self.assertEqual(correct_subtitles(raw, source, root, config)[0], corrected)


if __name__ == "__main__":
    unittest.main()
