import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.clip_editorial import edit_clip, validate_editorial
from core.cover_copy import fallback_cover_lines, validate_cover_copy
from core.highlight_selection import ModelAPIError, SelectionError, Subtitle


SUBS = [Subtitle(324,2251000,2253705,"这是什么你们觉得这个声音像什么"),
        Subtitle(325,2259504,2260889,"像搓麻将"),
        Subtitle(328,2280000,2282000,"核桃"),
        Subtitle(329,2282888,2283892,"确实也像")]
CLIP = {"id":"28c4a8820605", "title":"猜测道具声音像搓麻将", "start_ms":2250800,"end_ms":2284392,
        "evidence":[{"subtitle_id":324,"quote":SUBS[0].text,"role":"trigger"},
                    {"subtitle_id":329,"quote":SUBS[-1].text,"role":"payoff"}]}


def result():
    return {"titles":[{"text":"嘉然让大家猜ASMR的声音，答案从搓麻将聊到核桃", "support_ids":[324,325,328]}],
            "cover_text":"ASMR的声音，怎么像搓麻将？",
            "cover_lines":[{"kind":"caption","text":"ASMR的声音","support_ids":[324]},
                           {"kind":"caption","text":"怎么像搓麻将？","support_ids":[324,325]}],
            "frame_subtitle_id":324, "focus_subtitle_ids":[325], "focus_phrases":[]}


class CoverCopyTests(unittest.TestCase):
    def test_real_fragment_is_rejected_even_though_quotation_matches(self):
        raw = result()
        raw["cover_text"] = "ASMR道具声像什么？确实也像"
        raw["cover_lines"] = [{"kind":"caption","text":"ASMR道具声像什么？","support_ids":[324]},
                              {"kind":"quote","text":"确实也像","subtitle_id":329}]
        with self.assertRaisesRegex(SelectionError,"缺少具体对象"):
            validate_editorial(raw,SUBS)

    def test_line_breaks_preserve_full_copy_and_allow_normal_ellipsis(self):
        self.assertEqual(validate_cover_copy(["ASMR的声音","怎么像搓麻将？"], "ASMR的声音，怎么像搓麻将？"),
                         "ASMR的声音，怎么像搓麻将？")
        for lines, text in ((["ASMR的声音","确实也像"],"ASMR的声音，确实也像"),
                            (["这是因为"],None),(["这次一定","不会出错"],"这次一定出错")):
            with self.assertRaises(SelectionError):
                validate_cover_copy(lines,text)
        validate_cover_copy(["这声音像心跳吗？","确实很像"])
        validate_cover_copy(["声音好像","搓麻将"])

    def test_full_cover_text_is_required_for_new_automatic_results(self):
        raw = result()
        raw.pop("cover_text")
        with self.assertRaisesRegex(SelectionError,"cover_text"):
            validate_editorial(raw,SUBS,require_cover_text=True)
        self.assertIn("cover_text",validate_editorial(raw,SUBS))

    def test_title_cannot_add_a_nod_to_spoken_agreement(self):
        raw = result()
        raw["titles"][0]["text"] = "嘉然听到像搓麻将，听完也点头"
        with self.assertRaisesRegex(SelectionError,"动作或情绪"):
            validate_editorial(raw,SUBS)

    def test_fallback_preserves_full_title_instead_of_cutting_an_evidence_quote(self):
        label = "嘉然讨论小钢珠声音与忘记购买的小勺子"
        self.assertEqual("".join(fallback_cover_lines(label)),label)
        with self.assertRaises(SelectionError):
            fallback_cover_lines("甲"*37)

    def test_independent_review_can_fix_copy_and_is_cached(self):
        class Model:
            cache_identity = {"model":"copy-test"}
            def __init__(self):
                self.tasks = []
            def complete(self,prompt,payload):
                self.tasks.append(payload["task"])
                raw = result()
                if payload["task"] == "publication_editorial":
                    raw["cover_text"] = "ASMR初体验，声音猜一猜"
                    raw["cover_lines"] = [{"kind":"caption","text":"ASMR初体验","support_ids":[324]},
                                          {"kind":"caption","text":"声音猜一猜","support_ids":[324]}]
                else:
                    self_draft = payload["draft"]
                    assert self_draft["cover_text"] == "ASMR初体验，声音猜一猜"
                    # A prose reviewer must not change subtitle focus or frame.
                    raw["frame_subtitle_id"] = 325
                    raw["focus_subtitle_ids"] = [329]
                return raw
        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            first = edit_clip(model,"嘉然ASMR.mp4",CLIP,SUBS,folder)
            second = edit_clip(model,"嘉然ASMR.mp4",CLIP,SUBS,folder)
            self.assertEqual(model.tasks,["publication_editorial","publication_copy_review"])
            self.assertEqual(first,second)
            self.assertEqual(first["cover_text"],result()["cover_text"])
            self.assertEqual(first["frame_subtitle_id"],324)
            self.assertEqual(first["focus_subtitle_ids"],[325])

    def test_failed_review_keeps_draft_and_resume_only_requests_review(self):
        class Model:
            cache_identity = {"model":"resume-copy-test"}
            def __init__(self):
                self.tasks=[]
                self.fail=True
            def complete(self,prompt,payload):
                self.tasks.append(payload["task"])
                if payload["task"] == "publication_copy_review" and self.fail:
                    raise ModelAPIError(402,"quota unavailable")
                return result()
        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            with self.assertRaises(ModelAPIError):
                edit_clip(model,"嘉然ASMR.mp4",CLIP,SUBS,folder)
            self.assertEqual(len(list((Path(folder)/"cache/editorial").glob("*.draft.json"))),1)
            model.fail=False
            edit_clip(model,"嘉然ASMR.mp4",CLIP,SUBS,folder)
            self.assertEqual(model.tasks,["publication_editorial","publication_copy_review","publication_copy_review"])

    def test_failed_semantic_review_never_publishes_fragment_and_retains_draft(self):
        class Model:
            cache_identity={"model":"failed-review"}
            def complete(self,prompt,payload):
                raw=result()
                if payload["task"] == "publication_copy_review":
                    raw["cover_text"]="这声音像什么？确实也像"
                    raw["cover_lines"]=[{"kind":"caption","text":"这声音像什么？","support_ids":[324]},
                                        {"kind":"quote","text":"确实也像","subtitle_id":329}]
                return raw
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(SelectionError,"通顺性复核未通过"):
                edit_clip(Model(),"嘉然ASMR.mp4",CLIP,SUBS,folder)
            files=list((Path(folder)/"cache/editorial").glob("*.json"))
            self.assertTrue(any(f.name.endswith(".draft.json") for f in files))
            self.assertTrue(all(f.name.endswith((".draft.json",".review.failed.json")) for f in files))

    def test_local_review_and_disabling_model_review_do_not_add_requests(self):
        class Model:
            cache_identity={"model":"review-off"}
            def __init__(self):
                self.tasks=[]
            def complete(self,prompt,payload):
                self.tasks.append(payload["task"])
                return result()
        with tempfile.TemporaryDirectory() as folder:
            with patch("core.highlight_model.requests.post",side_effect=AssertionError("must be local")):
                local=edit_clip(None,"嘉然ASMR.mp4",CLIP,SUBS,folder,override=result())
            self.assertEqual(local["origin"],"local_review")
            model=Model()
            edit_clip(model,"嘉然ASMR.mp4",CLIP,SUBS,folder,copy_review_enabled=False)
            self.assertEqual(model.tasks,["publication_editorial"])
