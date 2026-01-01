import json
import os
import re
from pathlib import Path

from core.auto_correct import auto_correct_subtitles
from core.auto_detect import auto_detect_files
from core.metadata import load_source_meta, write_source_meta
from core.video_processor import VideoProcessor

# ==========================================
# 1. 用户配置区域
# ==========================================

CONFIG = {
    # 路径配置集中管理，路径不要更改
    # 数据源文件路径 (里面含有JSON数组，用于确定剪辑片段)
    "data_source": "Data_source.txt",

    # 输入文件夹路径, 程序会自动在此文件夹下查找：
    # 1. 唯一的视频文件 (.mp4, .flv, .mkv, .mov, .ts)
    # 2. 唯一的字幕文件 (.srt)
    # 3. 输出时会自动提取这个文件夹的名字，在 output_dir 下创建同名文件夹
    "input_dir": r"",

    "output_dir": "workspace/clip_output",

    # --- 字幕缓冲配置 (按句子数量) ---
    "padding": {
        "pre_sentences": 5,   # 片段向前延伸的句子数量
        "post_sentences": 2   # 片段向后延伸的句子数量
    },

    # --- [封面字体配置] ---
    "font_path": os.path.join(os.path.dirname(__file__), "assets", "font", "WenYue-XinQingNianTi-W8-J-2.otf"),

    # --- [视频字幕样式 (ASS)] ---
    "subtitle": {
        # 视频方向设置，填写：
        # "horizontal" = 横屏 (1920x1080)（B站经典风格）
        # "vertical"   = 竖屏 (1080x1920)（类似于抖音）
        # 如果是横屏直播就填"horizontal"，竖屏直播就填"vertical"。如果这里设置错了，那么字幕会变得异常大或者异常小
        "orientation": "horizontal",

        # 视频字幕字体（使用前要在自己系统里安装字体，否则系统会使用默认字体）
        "font_family": "WenYue XinQingNianTi (Authorization Required) W8-J", # 新青年体（推荐）
        # "font_family": "084-SSZhuangYuanTi",  # 上首状元体
        # "font_family": "Jiyucho",  # 自由体

        "font_size": 120,          # 字体大小  (推荐为120)
        "outline_width": 7,        # 描边宽度 （推荐为7）
        "shadow_depth": 2,         # 阴影深度  (推荐为2)
        "margin_v": 50,            # 字幕和画面底部的距离（推荐为50）

        # 字幕样式：
        # 黄字黑色描边（通用）
        "primary_color": "&H0000E1FF",
        "outline_color": "&H00000000",

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
        # "outline_color": "&H00FFFFFF",
    },
}

# ==========================================
# helpers
# ==========================================

def apply_config_overrides(overrides):
    if not overrides:
        return
    def deep_update(target, updates):
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                deep_update(target[key], value)
            else:
                target[key] = value
    deep_update(CONFIG, overrides)

# ==========================================
# 2. 主程序入口
# ==========================================

def run_single_clip(
    clip_data,
    output_dir=None,
    source_video=None,
    srt_file=None,
    input_dir=None,
    config_overrides=None,
    force_regen_ass=False
):
    if config_overrides:
        apply_config_overrides(config_overrides)

    if source_video:
        CONFIG['source_video'] = source_video
    if srt_file is not None:
        CONFIG['srt_file'] = srt_file
    if input_dir:
        CONFIG['input_dir'] = input_dir

    if not CONFIG.get('source_video'):
        if output_dir:
            meta = load_source_meta(output_dir)
            if meta:
                CONFIG['source_video'] = meta.get('source_video')
                CONFIG['srt_file'] = meta.get('srt_file')

    if not CONFIG.get('source_video'):
        if input_dir:
            video_file, detected_srt = auto_detect_files(input_dir)
            CONFIG['source_video'] = video_file
            CONFIG['srt_file'] = detected_srt
        else:
            print("❌ 未找到视频路径，请先运行 Auto_clip.py")
            return

    if output_dir:
        CONFIG['output_dir'] = output_dir

    processor = VideoProcessor(CONFIG, input_dir=CONFIG.get('input_dir'))
    processor.process_clip(
        1,
        clip_data,
        output_dir_override=output_dir,
        generate_cover=True,
        force_regen_ass=force_regen_ass
    )


def main():
    # 1. 自动纠正字幕并检测文件
    input_dir = CONFIG['input_dir']
    auto_correct_subtitles(input_dir)
    video_file, srt_file = auto_detect_files(input_dir)

    CONFIG['source_video'] = video_file
    CONFIG['srt_file'] = srt_file

    # ================= 自动更新输出路径 =================
    folder_name = os.path.basename(os.path.normpath(input_dir))
    CONFIG['output_dir'] = os.path.join(CONFIG['output_dir'], folder_name)

    # ----------------- 清理逻辑 -----------------
    output_path_obj = Path(CONFIG['output_dir'])
    if output_path_obj.exists():
        print("🧹 检测到输出目录已存在，正在清理视频和封面 (保留 .ass 字幕)...")
        for file_path in output_path_obj.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in ['.mp4', '.mkv', '.flv', '.jpg', '.png', '.jpeg']:
                try:
                    file_path.unlink()
                except Exception as e:
                    print(f"⚠️ 无法删除文件 {file_path.name}: {e}")
    else:
        output_path_obj.mkdir(parents=True, exist_ok=True)
        print("✅ 输出目录已创建")

    write_source_meta(output_path_obj, CONFIG.get('source_video'), CONFIG.get('srt_file'))

    if not os.path.exists(CONFIG['source_video']):
        print(f"❌ 未找到视频文件: {CONFIG['source_video']}")
        return

    data_source_path = CONFIG['data_source']
    if not os.path.exists(data_source_path):
        print(f"❌ 未找到数据源文件: {data_source_path}")
        print(" 请确保 Data_source.txt 位于正确位置或在 CONFIG 中修改路径。")
        return

    try:
        with open(data_source_path, 'r', encoding='utf-8') as f:
            raw_text = f.read()
        json_match = re.search(r'\[.*\]', raw_text, re.S)
        if not json_match:
            print("❌ 数据源文件中未找到 JSON 数组格式 (以 '[' 开头，以 ']' 结尾)")
            return
        clips = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"❌ JSON 格式错误: {e}")
        print("💡 请检查 Data_source.txt 里的逗号、引号是否正确。")
        return
    except Exception as e:
        print(f"❌ 读取数据源时发生未知错误: {e}")
        return

    print("=" * 60)
    print("🎨 视频剪辑与封面生成工具 (自动检测 + 自动归档模式)")
    print("=" * 60)
    print(f"数据来源: {data_source_path}")
    print(f"视频来源: {video_file}")
    print(f"输出目录: {CONFIG['output_dir']}")
    print(f"待处理片段数: {len(clips)}")
    print("=" * 60)

    index_width = max(2, len(str(len(clips))))
    processor = VideoProcessor(CONFIG, input_dir=CONFIG.get('input_dir'))
    for i, clip in enumerate(clips, 1):
        try:
            processor.process_clip(
                i,
                clip,
                generate_cover=False,
                index_width=index_width,
                force_regen_ass=False
            )
        except Exception as e:
            print(f"❌ 处理片段 {i} 时出错: {e}")

    print("\n" + "=" * 60)
    print(f"✅ 所有片段处理完毕! 文件保存在: {CONFIG['output_dir']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
