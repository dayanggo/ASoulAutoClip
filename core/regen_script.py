import json
from string import Template

def write_regen_script(script_path, clip_data, config):
    clip_json = json.dumps(clip_data, ensure_ascii=False, indent=2)
    padding = (config or {}).get("padding", {})
    subtitle = (config or {}).get("subtitle", {})

    pre_sentences = padding.get("pre_sentences", 5)
    post_sentences = padding.get("post_sentences", 2)

    orientation = subtitle.get("orientation", "horizontal")
    font_family = subtitle.get("font_family", "WenYue XinQingNianTi (Authorization Required) W8-J")
    font_size = subtitle.get("font_size", 120)
    outline_width = subtitle.get("outline_width", 7)
    shadow_depth = subtitle.get("shadow_depth", 2)
    margin_v = subtitle.get("margin_v", 50)
    primary_color = subtitle.get("primary_color", "&H0000E1FF")
    outline_color = subtitle.get("outline_color", "&H00000000")

    orientation_literal = json.dumps(orientation, ensure_ascii=False)
    font_family_literal = json.dumps(font_family, ensure_ascii=False)
    primary_color_literal = json.dumps(primary_color, ensure_ascii=False)
    outline_color_literal = json.dumps(outline_color, ensure_ascii=False)

    config_overrides_text = Template("""CONFIG_OVERRIDES = {
    # --- 字幕缓冲配置 (按句子数量) ---
    "padding": {
        "pre_sentences": $pre_sentences,   # 片段向前延伸的句子数量
        "post_sentences": $post_sentences   # 片段向后延伸的句子数量
    },

    # --- [封面字体配置] ---
    # 不需要填目录，默认沿用 Auto_clip.py 的字体路径
    "font_path": USE_DEFAULT,

    # --- [视频字幕样式 (ASS)] ---
    "subtitle": {
        # 视频方向设置，填写：
        # "horizontal" = 横屏 (1920x1080)
        # "vertical"   = 竖屏 (1080x1920)
        "orientation": $orientation,

        # 字幕字体（确保系统已安装）
        "font_family": $font_family,  # 新青年体（推荐）
        # "font_family": "084-SSZhuangYuanTi",  # 上首状元体
        # "font_family": "Jiyucho",  # 自由体

        "font_size": $font_size,
        "outline_width": $outline_width,
        "shadow_depth": $shadow_depth,
        "margin_v": $margin_v,

        # 当前字幕样式（如果不满意，可以在下面注释的字幕样式里面选择一个修改）
        "primary_color": $primary_color,
        "outline_color": $outline_color,
                                     
        # 黄字黑色描边（通用）
        # "primary_color": "&H0000E1FF",
        # "outline_color": "&H00000000",
                                     
        # 白字黑色描边（通用）
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00000000",

        # 嘉然专属
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H009972F0",

        # "primary_color": "&H009972F0",
        # "outline_color": "&H00FFFFFF",

        # 贝拉专属
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00747DDB",

        # "primary_color": "&H00747DDB",
        # "outline_color": "&H00FFFFFF",

        # 乃琳专属
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00906657",

        # "primary_color": "&H00906657",
        # "outline_color": "&H00FFFFFF",

        # A-SOUL团体
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00006AFF",

        # "primary_color": "&H00006AFF",
        # "outline_color": "&H00FFFFFF",

        # 心宜专属
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H009555FF",

        # "primary_color": "&H009555FF",
        # "outline_color": "&H00FFFFFF",

        # 思诺专属
        # "primary_color": "&H00FFFFFF",
        # "outline_color": "&H00C889A8",

        # "primary_color": "&H00C889A8",
        # "outline_color": "&H00FFFFFF"
    },

    # ==========================================
    # 封面样式配置区（竖屏可调小 title_size）
    # ==========================================
    "cover": {
        "count": 5,  # 每个视频生成的封面数量
        "active_style": "style1",

        # 图片配置（只写文件名即可）
        "images": [
            # {"path": r"image.png", "x": 0.5, "y": 0.5, "anchor": "center", "size": (2000, 1200), "opacity": 1}
        ],

        "style1": {
            "name": "上白下黄震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 255),
            "title_bottom_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style2": {
            "name": "上黄下白震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 0),
            "title_bottom_color": (255, 225, 255),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style3": {
            "name": "居中大字醒目风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.7,
            "title_size": 180,
            "title_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 10,
            "show_summary": False
        },
        "style4": {
            "name": "艺术简洁风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.5,
            "title_size": 180,
            "title_color": (255, 255, 255),
            "title_stroke_color": (50, 50, 50),
            "title_stroke_width": 8,
            "gradient_start_y": 0.0,
            "gradient_opacity": 150,
            "show_summary": False,
            "blur_background": True,
            "blur_radius": 3
        }
    }
}
""").substitute(
        pre_sentences=pre_sentences,
        post_sentences=post_sentences,
        orientation=orientation_literal,
        font_family=font_family_literal,
        font_size=font_size,
        outline_width=outline_width,
        shadow_depth=shadow_depth,
        margin_v=margin_v,
        primary_color=primary_color_literal,
        outline_color=outline_color_literal,
    )
    script_content = f"""# -*- coding: utf-8 -*-
# ===============================
# 配置区（仅影响当前片段）
# ===============================
CLIP_DATA = {clip_json}

# 0 = 不重新生成字幕 .ass，1 = 重新生成字幕 .ass
REBUILD_ASS = 0

USE_DEFAULT = "__USE_DEFAULT__"

{config_overrides_text}
import os
import sys

def _find_project_root(start_dir):
    current = os.path.abspath(start_dir)
    while True:
        if os.path.isfile(os.path.join(current, "Auto_clip.py")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent

project_root = _find_project_root(os.path.dirname(__file__))
if not project_root:
    print("找不到 Auto_clip.py，请检查目录结构")
    sys.exit(1)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import Auto_clip as auto_clip

def _apply_defaults():
    if CONFIG_OVERRIDES.get("font_path") == USE_DEFAULT:
        CONFIG_OVERRIDES["font_path"] = auto_clip.CONFIG["font_path"]

def main():
    _apply_defaults()
    output_dir = os.path.abspath(os.path.dirname(__file__))
    auto_clip.run_single_clip(
        CLIP_DATA,
        output_dir=output_dir,
        config_overrides=CONFIG_OVERRIDES,
        force_regen_ass=bool(REBUILD_ASS)
    )

if __name__ == "__main__":
    main()
"""
    with open(script_path, 'w', encoding='utf-8-sig') as f:
        f.write(script_content)
