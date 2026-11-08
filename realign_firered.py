"""Compatibility entry point; use python -m scripts.realign_firered."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("scripts.realign_firered", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("scripts.realign_firered")
