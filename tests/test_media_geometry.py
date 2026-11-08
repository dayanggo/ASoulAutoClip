import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.clip_publication import generate_publication, reaction_scene
from core.highlight_render import probe_video,render_clip
from core.highlight_selection import Subtitle
from core.media_geometry import resolve_subtitle_style,stream_geometry


class GeometryTests(unittest.TestCase):
    def test_display_orientation_accounts_for_rotation_and_pixel_ratio(self):
        geometry=stream_geometry({"width":1920,"height":1080,"sample_aspect_ratio":"1:1",
                                  "side_data_list":[{"rotation":-90}]})
        self.assertEqual((geometry["width"],geometry["height"]),(1080,1920))
        anamorphic=stream_geometry({"width":720,"height":576,"sample_aspect_ratio":"16:15"})
        self.assertEqual((anamorphic["width"],anamorphic["height"]),(768,576))

    def test_auto_style_keeps_preferences_but_shortens_portrait_cards(self):
        portrait=resolve_subtitle_style({}, {"width":1080,"height":1920})
        landscape=resolve_subtitle_style({}, {"width":1920,"height":1080})
        self.assertEqual((portrait["orientation"],portrait["max_phrase_chars"]),("vertical",11))
        self.assertEqual((landscape["orientation"],landscape["max_phrase_chars"]),("horizontal",14))
        for style in (portrait,landscape):
            self.assertEqual((style["font_size"],style["primary_color"],style["margin_v"],style["bold"]),
                             (84,"&H0000E1FF",50,True))

    def test_portrait_cover_preserves_top_and_bottom_of_original_scene(self):
        frame=Image.new("RGB",(720,1280),"red")
        frame.paste("blue",(0,640,720,1280))
        scene=reaction_scene(frame,{"focus":None,"zoom":1,"text_side":"bands"})
        self.assertEqual(scene.size,(720,1280))
        self.assertEqual(scene.getpixel((360,5)),(255,0,0))
        self.assertEqual(scene.getpixel((360,1275)),(0,0,255))


class RealPortraitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="asoul-orientation-")
        cls.root=Path(cls.temp.name)
        for name,size in (("portrait","180x320"),("landscape","320x180")):
            subprocess.run(["ffmpeg","-v","error","-nostdin","-y","-f","lavfi","-i",
                            f"color=c=blue:s={size}:r=10:d=5","-c:v","libx264","-preset","ultrafast",
                            str(cls.root/(name+".mp4"))],check=True,capture_output=True,timeout=30)
        subprocess.run(["ffmpeg","-v","error","-nostdin","-y","-display_rotation:v:0","90",
                        "-i",str(cls.root/"landscape.mp4"),"-c","copy",str(cls.root/"rotated.mp4")],
                       check=True,capture_output=True,timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def fixture(self,name):
        video=self.root/(name+".mp4")
        stat=video.stat()
        source={"path":str(video.resolve()),"size":stat.st_size,"mtime_ns":stat.st_mtime_ns}
        text="我们一起回忆线下演出还有大家合唱的故事"
        end=500+len(text)*120
        sub=Subtitle(1,500,end,text)
        folder=self.root/(name+"-words")
        folder.mkdir(exist_ok=True)
        words=folder/"transcript.words.json"
        words.write_text(json.dumps({"runtime":{"backend":"firered"},"timestamp_policy":2,"segments":[{
            "start_ms":500,"end_ms":end,"text":text,"words":[
                {"text":char,"start_ms":500+i*120,"end_ms":500+(i+1)*120} for i,char in enumerate(text)]}]}),encoding="utf-8")
        (folder/"receipt.json").write_text(json.dumps({"source":source}),encoding="utf-8")
        clip={"id":name,"title":"三人电台完整互动","start_ms":0,"end_ms":4000,"timestamp":"00:00:00-00:00:04",
              "evidence":[{"quote":text,"role":"trigger","start_ms":500,"end_ms":end,"subtitle_id":1}]}
        return video,source,sub,words,clip

    def test_real_portrait_render_has_correct_canvas_single_line_and_dimensions(self):
        video,source,sub,words,clip=self.fixture("portrait")
        target=render_clip(video,source,[sub],clip,self.root/"output",word_timing_path=words)
        media=probe_video(target)
        self.assertEqual((media["width"],media["height"]),(180,320))
        ass=target.with_name("captions.generated.ass").read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1080",ass)
        self.assertIn("PlayResY: 1920",ass)
        dialogues=[s for s in ass.splitlines() if s.startswith("Dialogue:")]
        self.assertTrue(all(r"\N" not in line for line in dialogues))
        rendered="".join(line.split(",",9)[-1] for line in dialogues)
        self.assertEqual(rendered,sub.text)
        receipt=json.loads(target.with_name("render.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["subtitle_style"]["orientation"],"vertical")

    def test_rotated_input_is_not_mistaken_for_a_landscape_source_or_rotated_twice(self):
        video,source,sub,words,clip=self.fixture("rotated")
        self.assertEqual((probe_video(video)["width"],probe_video(video)["height"]),(180,320))
        target=render_clip(video,source,[sub],clip,self.root/"output",word_timing_path=words)
        media=probe_video(target)
        self.assertEqual((media["width"],media["height"]),(180,320))
        self.assertEqual(media["rotation"],0)

    def test_all_cover_styles_export_real_portrait_images(self):
        video,source,sub,words,clip=self.fixture("portrait")
        for style in ("event_yellow","live_reaction","quote_poster"):
            with self.subTest(style=style):
                out=self.root/("covers-"+style)
                metadata=generate_publication(video,source,clip,out,{"cover_style":style,"cover_count":1})
                with Image.open(out/"clips"/clip["id"]/"cover.jpg") as image:
                    self.assertEqual(image.size,(720,1280))
                self.assertEqual(metadata["cover_dimensions"],{"width":720,"height":1280})

    def test_landscape_render_still_uses_landscape_canvas(self):
        video,source,sub,words,clip=self.fixture("landscape")
        target=render_clip(video,source,[sub],clip,self.root/"output",word_timing_path=words)
        ass=target.with_name("captions.generated.ass").read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1920",ass)
        self.assertIn("PlayResY: 1080",ass)
        self.assertEqual((probe_video(target)["width"],probe_video(target)["height"]),(320,180))
