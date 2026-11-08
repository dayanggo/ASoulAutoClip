"""Canonical repository paths and compatibility for pre-organization configs."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCES_DIR = PROJECT_ROOT / "resources"
PROMPTS_DIR = RESOURCES_DIR / "prompts"
DICTIONARY_PATH = RESOURCES_DIR / "dictionaries" / "asr_dict.txt"


def resolve_config_path(path, root=None):
    root = Path(root or PROJECT_ROOT).resolve()
    candidate = (root / Path(path).expanduser()).resolve()
    if candidate.is_file() or candidate.parent != root / "config":
        return candidate
    if candidate.name == "highlights.example.json":
        return root / "config/examples/highlights.example.json"
    if candidate.name.endswith(".local.json"):
        scene = candidate.name[:-len(".local.json")]
        if scene != "highlights" and scene.startswith("highlights."):
            scene = scene[len("highlights."):]
        return root / "config/local" / (scene + ".json")
    return candidate


def default_config_path(root=None):
    root = Path(root or PROJECT_ROOT)
    for relative in ("config/local/highlights.json", "config/highlights.local.json"):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return root / "config/examples/highlights.example.json"


def resolve_dictionary_path(path):
    candidate = (PROJECT_ROOT / Path(path).expanduser()).resolve()
    if not candidate.is_file() and candidate == PROJECT_ROOT / "utils/asr_dict.txt":
        return DICTIONARY_PATH
    return candidate
