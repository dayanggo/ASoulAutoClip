import unittest

from audit_clips import token_boundary_shift
from core.highlight_selection import fingerprint
from core.local_transcription import asr_cache_key


class ClipAuditTests(unittest.TestCase):
    def test_boundary_comparison_requires_matching_token_identity_and_old_time(self):
        region = {"start": 16000, "end": 64000}
        old = [{"region": region, "result": {"timestamp": [["词", 0, 1], ["尾", 1, 2]]}}]
        new = [{"region": region, "result": {"timestamp": [["词", .2, .7], ["尾", 1.4, 1.9]]}}]
        word = {"text": "词", "start_ms": 1000, "end_ms": 2000}
        result = token_boundary_shift(word, old, new)
        self.assertEqual(result["ctc_start_ms"], 1200)
        self.assertEqual(result["ctc_end_ms"], 1700)
        self.assertIsNone(token_boundary_shift(dict(word, start_ms=2500), old, new))
        new[0]["result"]["timestamp"][0][0] = "不同的词"
        self.assertIsNone(token_boundary_shift(word, old, new))

    def test_firered_legacy_cache_cannot_be_reused_as_strict_ctc(self):
        config = {"backend": "firered", "device": "auto"}
        legacy = fingerprint({"version": 3, "source": {"test": 1}, "asr": config})
        self.assertNotEqual(asr_cache_key({"test": 1}, config), legacy)
        whisper = dict(config, backend="faster_whisper")
        self.assertEqual(asr_cache_key({"test": 1}, whisper),
                         fingerprint({"version": 3, "source": {"test": 1}, "asr": whisper}))


if __name__ == "__main__":
    unittest.main()
