import tempfile
import unittest

from core.clip_editorial import edit_clip
from core.highlight_selection import HighlightPipeline, Subtitle
from test_recording_pipeline import FakeModel, SUBS, candidate, selection_config


class RadioContextTests(unittest.TestCase):
    def test_names_from_context_or_transcript_do_not_assert_speaker_identity(self):
        class Model(FakeModel):
            def __init__(self):
                super().__init__()
                self.payloads=[]
            def complete(self,prompt,payload):
                self.payloads.append(payload)
                return super().complete(prompt,payload)
        subs=list(SUBS)+[Subtitle(4,7100,7300,"乃琳今天聊到这件事")]
        clip=dict(candidate(),start_ms=500,end_ms=7500)
        with tempfile.TemporaryDirectory() as folder:
            model=Model()
            edit_clip(model,"三人电台.mp4",clip,subs,folder,
                      recording_brief="用户确认参与者为嘉然、贝拉、乃琳。仅作背景。")
            self.assertEqual(len(model.payloads),2)
            for payload in model.payloads:
                self.assertEqual(set(payload["allowed_names"]),{"嘉然","贝拉","乃琳"})
                self.assertEqual(payload["speaker_attribution"],"unavailable")
                self.assertIn("仅作背景",payload["recording_brief"])
            no_names_model=Model()
            edit_clip(no_names_model,"未署名录播.mp4",clip,subs,folder)
            self.assertEqual(no_names_model.payloads[0]["allowed_names"],["乃琳"])

    def test_recording_brief_reaches_selection_and_boundary_review(self):
        class Model(FakeModel):
            def __init__(self):
                super().__init__()
                self.payloads=[]
            def complete(self,prompt,payload):
                self.payloads.append(payload)
                return super().complete(prompt,payload)
        config=selection_config()
        config["brief"]="三人电台；没有确认的逐句说话人标签。"
        with tempfile.TemporaryDirectory() as folder:
            model=Model()
            result=HighlightPipeline(model,config,folder).run(SUBS,10000)
            self.assertEqual(result["status"],"complete")
            self.assertEqual(len(model.payloads),2)
            self.assertTrue(all(p["brief"]==config["brief"] for p in model.payloads))

    def test_changing_recording_background_does_not_reuse_old_editorial(self):
        with tempfile.TemporaryDirectory() as folder:
            model=FakeModel()
            clip=dict(candidate(),start_ms=500,end_ms=7500)
            for brief in ("三人电台，嘉然、贝拉、乃琳。","三人电台，嘉然、贝拉、乃琳。"):
                edit_clip(model,"电台.mp4",clip,SUBS,folder,recording_brief=brief)
            self.assertEqual(model.calls,2)
            edit_clip(model,"电台.mp4",clip,SUBS,folder,recording_brief="多人对谈，名单尚未确认。")
            self.assertEqual(model.calls,4)
