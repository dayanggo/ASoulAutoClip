"""Compatibility entry point for existing generated regen_clip.py files."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("legacy.auto_clip", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("legacy.auto_clip")
