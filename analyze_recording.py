"""Compatibility entry point; implementation lives in core.cli."""
import sys

if __name__ == "__main__":
    from core.cli import main
    raise SystemExit(main())
else:
    from core import cli as _implementation
    sys.modules[__name__] = _implementation
