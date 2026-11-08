import importlib
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cli import load_config
from core.paths import DICTIONARY_PATH, PROJECT_ROOT, PROMPTS_DIR, default_config_path, resolve_config_path
from core.subtitle_correction import DictionaryCorrector
from scripts.check_repository import candidate_files, inspect_repository, private_path


class ProjectLayoutTests(unittest.TestCase):
    def test_compatibility_entries_share_the_actual_module(self):
        for old, new in (("analyze_recording", "core.cli"), ("audit_clips", "scripts.audit_clips"),
                         ("Auto_clip", "legacy.auto_clip"), ("download_firered_aed", "scripts.download_firered_aed"),
                         ("realign_firered", "scripts.realign_firered")):
            with self.subTest(entry=old):
                self.assertIs(importlib.import_module(old), importlib.import_module(new))

    def test_old_config_names_resolve_without_copying_local_settings(self):
        for old, new in (("highlights.local.json", "highlights.json"),
                         ("highlights.radio.local.json", "radio.json"),
                         ("highlights.aaa-20261106-p2.local.json", "aaa-20261106-p2.json")):
            with self.subTest(path=old):
                self.assertEqual(resolve_config_path("config/"+old), PROJECT_ROOT / "config/local" / new)
        self.assertEqual(resolve_config_path("config/highlights.example.json"), PROJECT_ROOT / "config/examples/highlights.example.json")

    def test_missing_external_config_is_not_redirected_to_private_project_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            requested = Path(folder) / "config/highlights.local.json"
            self.assertEqual(resolve_config_path(requested), requested)

    def test_clean_clone_uses_blank_public_example(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(default_config_path(folder), Path(folder) / "config/examples/highlights.example.json")
        for path in (PROJECT_ROOT / "config/examples").glob("highlights*.json"):
            config = load_config(path)
            self.assertFalse(config["input"]["video"])
            self.assertFalse(config["llm"]["model"])
            self.assertEqual(config["selection"]["min_score"], 85)

    def test_resources_and_legacy_dictionary_alias_are_available(self):
        self.assertEqual(DictionaryCorrector("utils/asr_dict.txt").path, DICTIONARY_PATH)
        self.assertTrue(DICTIONARY_PATH.is_file())
        for name in ("highlight_select_zh.txt", "highlight_boundary_zh.txt", "clip_editorial_zh.txt", "clip_copy_review_zh.txt"):
            self.assertTrue((PROMPTS_DIR / name).is_file())

    def test_legacy_downloader_preserves_explicit_dictionary_priority(self):
        with patch.dict("sys.modules", {"yt_dlp": types.ModuleType("yt_dlp")}):
            module = importlib.import_module("legacy.tools.download_recording")
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "custom.txt"
            path.write_text("测试词 自定义词", encoding="utf-8")
            corrector = module.FileBasedCorrector(str(path))
            self.assertEqual(corrector.correct_text("测试词"), "自定义词")

    def test_publish_policy_excludes_runtime_data_and_all_local_profiles(self):
        for name in (".env", "config/local/radio.json", "config/example.local.json", "ouput/analysis.json",
                     "outputs/clips/a.mp4", "models/weights.pt", "core/__pycache__/cli.pyc", "Data_source.txt"):
            self.assertTrue(private_path(Path(name)), name)
        for name in (".env.example", "config/local/.gitkeep", "config/examples/highlights.example.json", "resources/images/image1.png"):
            self.assertFalse(private_path(Path(name)), name)

    def test_publish_check_never_echoes_matched_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "core").mkdir()
            secret = "sk-" + "a"*32
            (root / "core/example.py").write_text("API_KEY = " + repr(secret), encoding="utf-8")
            result = inspect_repository(root, ["core/example.py"])
            self.assertEqual(result["status"], "failed")
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(result["issues"][0]["line"], 1)

    def test_publish_check_detects_literal_session_cookie_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = "private-session-value"
            (root / "tool.py").write_text("SESSDATA = " + repr(value), encoding="utf-8")
            result = inspect_repository(root, ["tool.py"])
            self.assertEqual(result["status"], "failed")
            self.assertNotIn(value, json.dumps(result))
            self.assertIn("硬编码", result["issues"][0]["reason"])

    def test_publish_check_catches_tracked_local_files_and_missing_document_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text("", encoding="utf-8")
            (root / "README.md").write_text("[missing](docs/missing.md)", encoding="utf-8")
            result = inspect_repository(root, [".env", "README.md"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(len(result["issues"]), 2)

    @unittest.skipUnless(shutil.which("git"), "git unavailable")
    def test_source_copy_does_not_accidentally_use_its_parent_repository_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            source = root / "snapshot"
            source.mkdir()
            (source / "README.md").write_text("A clean source copy", encoding="utf-8")
            self.assertEqual(candidate_files(source), [Path("README.md")])


if __name__ == "__main__":
    unittest.main()
