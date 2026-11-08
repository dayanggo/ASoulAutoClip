"""Shared subtitle preferences for the recording, legacy, and regeneration paths."""

from copy import deepcopy


CAPTION_PREFERENCES = {
    "font_family": "文悦新青年体 (须授权) W8-J",
    "font_size": 84,
    "primary_color": "&H0000E1FF",
    "emphasis_mode": "none",
    "caption_mode": "phrases",
    "max_phrase_chars": 14,
    "phrase_pause_ms": 180,
    "caption_hold_ms": 100,
}

DEFAULT_SUBTITLE_STYLE = {
    "orientation": "auto", "outline_width": 4, "shadow_depth": 1,
    "margin_v": 50, "outline_color": "&H00000000", "bold": True,
    "spacing": 0, "margin_h": 10, "balanced_lines": True, "text_replacements": {},
    **CAPTION_PREFERENCES,
}


def subtitle_style(overrides=None):
    return dict(deepcopy(DEFAULT_SUBTITLE_STYLE), **(overrides or {}))
