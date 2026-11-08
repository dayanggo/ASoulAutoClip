import unittest
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch

from analyze_recording import DEFAULT_CONFIG
from core.firered_transcription import map_words, subtitle_records, strict_timestamps, install_strict_timestamps
from types import SimpleNamespace
from core.highlight_selection import SelectionError
from core.local_transcription import transcribe


class FireRedTimelineTests(unittest.TestCase):
    def test_strict_ctc_rejects_uniform_fallback_and_preserves_real_pauses(self):
        tokenizer = SimpleNamespace(detokenize=lambda ids, *args: str(ids[0]), merge_spm_timestamp=lambda t: t)
        adapter = SimpleNamespace(tokenizer=tokenizer)
        with self.assertRaisesRegex(SelectionError, "拒绝"):
            strict_timestamps(adapter, {"timestamp": None}, [1, 2], 3)
        times = strict_timestamps(adapter, {"timestamp": ([.12, 2.16], [.6, 2.8])}, [1, 2], 3)
        self.assertEqual(times, [["1", .06, .54], ["2", 2.1, 2.74]])
        with self.assertRaises(SelectionError):
            strict_timestamps(adapter, {"timestamp": ([float("nan")], [1])}, [1], 2)

    def test_ctc_hook_casts_alignment_logits_to_float32(self):
        callbacks = []
        adapter = SimpleNamespace(model=SimpleNamespace(ctc=SimpleNamespace(register_forward_hook=callbacks.append)))
        install_strict_timestamps(adapter)
        result = callbacks[0](None, None, SimpleNamespace(float=lambda: "float32 logits"))
        self.assertEqual(result, "float32 logits")
        self.assertTrue(callable(adapter._get_and_fix_timestamp))

    def test_vad_gap_is_preserved_on_original_timeline(self):
        words = map_words({"timestamp": [["你", .1, .3], ["好", .3, .6]]}, 16000 * 120, 16000 * 125)
        self.assertEqual(words[0]["start_ms"], 120100)
        self.assertEqual(words[-1]["end_ms"], 120600)
        record = subtitle_records(words)[0]
        self.assertEqual(record["text"], "你好")
        self.assertEqual(record["start_ms"], 120100)

    def test_missing_timestamps_are_rejected_instead_of_invented(self):
        with self.assertRaises(SelectionError):
            map_words({"text": "你好"}, 0, 16000)

    def test_invalid_time_and_out_of_range_words_are_rejected(self):
        for timestamp in ([["词", float("nan"), 1]], [["词", 2, 1]], [["词", 5, 6]]):
            with self.subTest(timestamp=timestamp), self.assertRaises(SelectionError):
                map_words({"timestamp": timestamp}, 0, 16000)

    def test_pauses_split_captions_without_collapsing_time(self):
        words = [{"text": "hello", "start_ms": 0, "end_ms": 300},
                 {"text": "world", "start_ms": 300, "end_ms": 700},
                 {"text": "你好", "start_ms": 2000, "end_ms": 2400}]
        records = subtitle_records(words)
        self.assertEqual([r["text"] for r in records], ["hello world", "你好"])
        self.assertEqual(records[1]["start_ms"], 2000)

    def test_firered_auto_selects_native_precision_and_reuses_cache(self):
        config = dict(copy.deepcopy(DEFAULT_CONFIG["asr"]), backend="firered", model="models/FireRedASR2-AED")
        runtimes = []

        def worker(video, runtime, folder):
            runtimes.append(runtime)
            if runtime["device"] == "cuda":
                raise SelectionError("simulated GPU failure")
            (folder / "transcript.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory, patch("core.firered_transcription.worker_python", return_value=Path(__file__)), patch("core.local_transcription.run_worker", side_effect=worker):
            output = Path(directory)
            first = transcribe(output / "video.mp4", {}, config, output)
            second = transcribe(output / "video.mp4", {}, config, output)
            self.assertEqual(first, second)
        self.assertEqual([r["compute_type"] for r in runtimes], ["bfloat16", "float32"])


if __name__ == "__main__":
    unittest.main()
