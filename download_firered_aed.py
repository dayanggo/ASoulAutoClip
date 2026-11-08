"""Compatibility entry point; use python -m scripts.download_firered_aed."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("scripts.download_firered_aed", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("scripts.download_firered_aed")
