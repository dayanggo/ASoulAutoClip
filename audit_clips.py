"""Compatibility entry point; use python -m scripts.audit_clips."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("scripts.audit_clips", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("scripts.audit_clips")
