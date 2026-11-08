import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from core.cli import DEFAULT_CONFIG
from core.firered_transcription import (AlignmentError, read_region_checkpoint, recognize_region,
                                       region_checkpoint_identity)
from core.highlight_selection import SelectionError, fingerprint, write_json
from core.local_transcription import run_worker, transcribe


class FireRedRecoveryTests(unittest.TestCase):
    def test_low_energy_split_keeps_original_offsets(self):
        def infer(ids, audio):
            rate, samples = audio[0]
            if len(samples) > 3000:
                raise AlignmentError("fixture cannot align long region")
            return [{"text": "词", "timestamp": [["词", .1, .3]]}]

        adapter = SimpleNamespace(config=SimpleNamespace(use_gpu=False), transcribe=infer)
        item = recognize_region(adapter, "region", 1000, np.zeros(4000, dtype=np.int16))
        middle = item["alignment_recovery"]["split_sample"]
        self.assertEqual(item["timestamp"][0], ["词", .1, .3])
        self.assertEqual(item["timestamp"][1], ["词", round(middle/1000+.1, 3), round(middle/1000+.3, 3)])
        self.assertEqual(item["alignment_recovery"]["method"], "low_energy_split")

    def test_fp32_retry_restores_precision_and_keeps_real_timestamps(self):
        calls = []
        config = SimpleNamespace(use_gpu=True, use_half=True)
        def infer(ids, audio):
            calls.append(config.use_half)
            if config.use_half:
                raise AlignmentError("fixture precision failure")
            return [{"text": "你好", "timestamp": [["你好", .12, .48]]}]
        events = []
        adapter = SimpleNamespace(config=config, transcribe=infer,
                                  model=SimpleNamespace(float=lambda: events.append("float"),
                                                        bfloat16=lambda: events.append("bf16")))
        fake_torch = SimpleNamespace(OutOfMemoryError=MemoryError, cuda=SimpleNamespace(empty_cache=lambda: None))
        with patch.dict(sys.modules, {"torch": fake_torch}):
            item = recognize_region(adapter, "region", 1000, np.zeros(4000, dtype=np.int16))
        self.assertEqual(calls, [True, False])
        self.assertEqual(events, ["float", "bf16"])
        self.assertTrue(config.use_half)
        self.assertEqual(item["timestamp"], [["你好", .12, .48]])
        self.assertEqual(item["alignment_recovery"]["method"], "fp32")

    def test_unresolved_short_region_stops_without_estimated_time(self):
        def fail(*args):
            raise AlignmentError("fixture cannot align")
        adapter = SimpleNamespace(config=SimpleNamespace(use_gpu=False), transcribe=fail)
        with self.assertRaisesRegex(AlignmentError, "未生成估计时间"):
            recognize_region(adapter, "region", 1000, np.zeros(1000, dtype=np.int16))

    def test_runtime_error_is_not_relabelled_as_alignment(self):
        def fail(*args):
            raise RuntimeError("fixture unavailable device")
        adapter = SimpleNamespace(config=SimpleNamespace(use_gpu=False), transcribe=fail)
        with self.assertRaisesRegex(RuntimeError, "unavailable device"):
            recognize_region(adapter, "region", 1000, np.zeros(4000, dtype=np.int16))

    def test_checkpoint_reuses_only_matching_audio_region_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"region.json"
            region = {"start": 0, "end": 16000}
            item = {"text": "你好", "timestamp": [["你好", .1, .5]]}
            record = {"identity": "source-runtime", "region": region, "pcm_sha256": "pcm",
                      "result": item, "result_fingerprint": fingerprint(item)}
            write_json(path, record)
            self.assertEqual(read_region_checkpoint(path, "source-runtime", region, "pcm", 16000), item)
            self.assertIsNone(read_region_checkpoint(path, "other-source", region, "pcm", 16000))
            self.assertIsNone(read_region_checkpoint(path, "source-runtime", region, "other-pcm", 16000))
            self.assertIsNone(read_region_checkpoint(path, "source-runtime", {"start": 1,"end": 16000}, "pcm", 16000))
            record["result"]["text"] = "changed"
            write_json(path, record)
            self.assertIsNone(read_region_checkpoint(path, "source-runtime", region, "pcm", 16000))
            record["result"]["timestamp"] = [["changed", 2, 3]]
            record["result_fingerprint"] = fingerprint(record["result"])
            write_json(path, record)
            self.assertIsNone(read_region_checkpoint(path, "source-runtime", region, "pcm", 16000))

    def test_checkpoint_identity_tracks_source_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory)/"fixture.mp4"
            video.write_bytes(b"a")
            a = region_checkpoint_identity(video, {"beam_size": 3})
            b = region_checkpoint_identity(video, {"beam_size": 4})
            video.write_bytes(b"ab")
            c = region_checkpoint_identity(video, {"beam_size": 3})
            self.assertEqual(len({a,b,c}), 3)

    def test_alignment_failure_does_not_restart_entire_video_on_cpu(self):
        config = dict(copy.deepcopy(DEFAULT_CONFIG["asr"]), backend="firered")
        runtimes = []
        def fail(video, runtime, folder):
            runtimes.append(runtime["device"])
            raise AlignmentError("fixture unresolved speech")
        with tempfile.TemporaryDirectory() as directory, \
                patch("core.firered_transcription.worker_python", return_value=Path(__file__)), \
                patch("core.local_transcription.run_worker", side_effect=fail):
            with self.assertRaisesRegex(SelectionError, "未退回 CPU"):
                transcribe(Path(directory)/"fixture.mp4", {}, config, Path(directory))
        self.assertEqual(runtimes, ["cuda"])

    def test_worker_alignment_marker_survives_process_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            def start(*args, **kwargs):
                write_json(folder/"worker-failure.json", {"kind":"alignment", "reason":"fixture alignment"})
                return SimpleNamespace(stdout=io.StringIO("fixture progress\n"), wait=lambda: 1, pid=1)
            with patch("core.firered_transcription.worker_python", return_value=Path(__file__)), \
                    patch("core.local_transcription.subprocess.Popen", side_effect=start):
                with self.assertRaisesRegex(AlignmentError, "fixture alignment"):
                    run_worker(folder/"fixture.mp4", {"backend":"firered","device":"cuda"}, folder)


if __name__ == "__main__":
    unittest.main()
