import json
import re
import subprocess
import os
import sys
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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

    # ==========================================
    # 🎨 封面样式配置区（如果是竖屏，需要把使用的封面样式调小，调整title_size）
    # ==========================================
    "cover": {
        "count": 5,  # 每个视频生成的封面数量
        "active_style": "style1", # 当前使用的样式
        
        # 图片配置
        "images": [
            # {
            #     "path": "assets/image/速度线.png",
            #     "x": 0.5, "y": 0.5, "anchor": "center", "size": (2000, 1200), "opacity": 1
            # },
            # {
            #     "path": "assets/image/image3.png",
            #     "x": 0.3, "y": 0.4, "anchor": "center", "size": (300, 300), "opacity": 1
            # },
            # {
            #     "path": "assets/image/image3.png",
            #     "x": 0.7, "y": 0.4, "anchor": "center", "size": (300, 300), "opacity": 1
            # }
        ],
        
        # 封面样式定义
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

# ==========================================
# 工具类区域 (保持不变)
# ==========================================

class SubtitleUtils:
    @staticmethod
    def parse_srt_time(time_str):
        time_str = time_str.replace(',', '.').strip()
        parts = time_str.split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        else:
            print(f"⚠️ 时间格式无法识别: {time_str}")
            return 0

    @staticmethod
    def sec_to_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds % 1) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def sec_to_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def parse_srt(srt_path):
        if not srt_path or not os.path.exists(srt_path):
            return []
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        subs = []
        for block in re.split(r'\n\n+', content.strip()):
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                time_match = re.search(r'(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)', lines[1])
                if time_match:
                    start = SubtitleUtils.parse_srt_time(time_match.group(1))
                    end = SubtitleUtils.parse_srt_time(time_match.group(2))
                    text = '\n'.join(lines[2:])
                    subs.append({'start': start, 'end': end, 'text': text})
        return subs

    @staticmethod
    def get_expanded_time_range(subtitles, target_start, target_end, pre_count, post_count):
        if not subtitles:
            return target_start, target_end

        core_start_idx = -1
        core_end_idx = -1
        
        for i, sub in enumerate(subtitles):
            if sub['end'] > target_start and sub['start'] < target_end:
                if core_start_idx == -1:
                    core_start_idx = i
                core_end_idx = i
        
        if core_start_idx == -1:
            print("   ⚠️ 警告: 该时间段内无匹配字幕,将使用原始时间戳。")
            return target_start, target_end

        new_start_idx = max(0, core_start_idx - pre_count)
        new_end_idx = min(len(subtitles) - 1, core_end_idx + post_count)

        expanded_start = subtitles[new_start_idx]['start']
        expanded_end = subtitles[new_end_idx]['end']

        final_start = min(expanded_start, target_start)
        final_end = max(expanded_end, target_end)

        return final_start, final_end

    @staticmethod
    def auto_wrap_text(text, max_len):
        """
        如果文本超过 max_len，则自动插入换行符。
        """
        # 清除原有的换行符(包括普通换行和ASS换行)
        clean_text = text.replace('\r', '').replace('\n', '').replace('\\N', '')
        
        if len(clean_text) <= max_len:
            return clean_text
            
        result = []
        for i in range(0, len(clean_text), max_len):
            result.append(clean_text[i : i + max_len])
        return '\n'.join(result)

    # ================= 重排现有 ASS 文件的方法 =================
    @staticmethod
    def reformat_ass_file(file_path, max_len):
        """
        读取现有的 ASS 文件，保留内容，仅重新计算换行
        """
        if not os.path.exists(file_path):
            return

        with open(file_path, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            # ASS 字幕行通常以 "Dialogue:" 开头
            if line.startswith('Dialogue:'):
                # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
                # 我们只需要分割前9个逗号，第10部分就是字幕文本(可能包含逗号)
                parts = line.split(',', 9)
                if len(parts) == 10:
                    original_text = parts[9].strip()
                    # 重新应用换行逻辑
                    wrapped_text = SubtitleUtils.auto_wrap_text(original_text, max_len)
                    final_text = wrapped_text.replace('\n', '\\N')
                    
                    # 拼装回去
                    parts[9] = final_text + '\n'
                    new_lines.append(','.join(parts))
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # 覆盖写入
        with open(file_path, 'w', encoding='utf-8-sig') as f:
            f.writelines(new_lines)
    # ===============================================================

    @staticmethod
    def create_ass_file(subtitles, output_path, start_offset, end_offset, max_char_len):
        s = CONFIG['subtitle']

        if s.get('orientation', 'horizontal') == 'vertical':
            play_res_x = 1080
            play_res_y = 1920
        else:
            play_res_x = 1920
            play_res_y = 1080

        style_line = (
            f"Style: Default,{s['font_family']},{s['font_size']},"
            f"{s['primary_color']},{s['primary_color']},{s['outline_color']},-1,"
            f"-1,0,0,0,100,100,0,0,1,{s['outline_width']},{s['shadow_depth']},2,10,10,{s['margin_v']},1"
        )

        header = f"""[Script Info]
Title: Auto Clip
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        clip_duration = end_offset - start_offset
        valid_count = 0

        for sub in subtitles:
            if sub['end'] > start_offset and sub['start'] < end_offset:
                rel_start = max(0, sub['start'] - start_offset)
                rel_end = min(clip_duration, sub['end'] - start_offset)
                start_str = SubtitleUtils.sec_to_ass_time(rel_start)
                end_str = SubtitleUtils.sec_to_ass_time(rel_end)
                
                # 传入动态的 max_char_len
                wrapped_text = SubtitleUtils.auto_wrap_text(sub['text'], max_len=max_char_len)
                text = wrapped_text.replace('\n', '\\N')
                
                events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")
                valid_count += 1
        
        with open(output_path, 'w', encoding='utf-8-sig') as f:
            f.write(header + '\n'.join(events))
        return valid_count

class CoverGenerator:
    @staticmethod
    def split_title_smartly(title, max_chars=10):
        split_chars = ['!', '!', '?', '?', ',', ',', '。', ':']
        for i, char in enumerate(title):
            if char in split_chars and 3 <= i <= len(title) - 3:
                return title[:i+1], title[i+1:]
        mid = len(title) // 2
        return title[:mid], title[mid:]

    @staticmethod
    def add_image_to_cover(base_img, image_config):
        image_path = image_config.get('path', '')
        if not image_path or not os.path.exists(image_path):
            if image_path:
                print(f"  ⚠️ 图片不存在: {image_path}")
            return base_img
        
        try:
            overlay_img = Image.open(image_path).convert("RGBA")
            target_size = image_config.get('size')
            if target_size:
                overlay_img = overlay_img.resize(target_size, Image.Resampling.LANCZOS)
            
            opacity = image_config.get('opacity', 1.0)
            if opacity < 1.0:
                alpha = overlay_img.split()[3]
                alpha = alpha.point(lambda p: int(p * opacity))
                overlay_img.putalpha(alpha)
            
            base_width, base_height = base_img.size
            img_width, img_height = overlay_img.size
            
            x = image_config.get('x', 0)
            y = image_config.get('y', 0)
            
            if isinstance(x, float) and 0.0 <= x <= 1.0: x = int(x * base_width)
            else: x = int(x)
            
            if isinstance(y, float) and 0.0 <= y <= 1.0: y = int(y * base_height)
            else: y = int(y)
            
            anchor = image_config.get('anchor', 'top_left').lower()
            anchor_offsets = {
                'top_left': (0, 0), 'top_center': (-img_width // 2, 0), 'top_right': (-img_width, 0),
                'center_left': (0, -img_height // 2), 'center': (-img_width // 2, -img_height // 2),
                'center_right': (-img_width, -img_height // 2),
                'bottom_left': (0, -img_height), 'bottom_center': (-img_width // 2, -img_height),
                'bottom_right': (-img_width, -img_height)
            }
            offset_x, offset_y = anchor_offsets.get(anchor, (0, 0))
            final_x = x + offset_x
            final_y = y + offset_y
            final_x = max(0, min(final_x, base_width - img_width))
            final_y = max(0, min(final_y, base_height - img_height))
            
            base_img.paste(overlay_img, (final_x, final_y), overlay_img)
        except Exception as e:
            print(f"  ⚠️ 图片叠加失败 ({image_path}): {e}")
        return base_img

    @staticmethod
    def create_aesthetic_cover(video_path, timestamp_sec, cover_text_1, cover_text_2, output_path, style_config):
        temp_img = output_path.with_suffix('.temp.jpg')
        cmd = [
            'ffmpeg', '-ss', str(timestamp_sec), '-i', video_path,
            '-frames:v', '1', '-q:v', '2', '-y', str(temp_img)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not temp_img.exists(): return

        try:
            img = Image.open(temp_img).convert("RGBA")
            width, height = img.size
            
            if style_config.get('blur_background', False):
                img = img.filter(ImageFilter.GaussianBlur(style_config.get('blur_radius', 3)))
            
            overlay = Image.new('RGBA', img.size, (0,0,0,0))
            draw = ImageDraw.Draw(overlay)

            gradient_start_y = int(height * style_config.get('gradient_start_y', 0.6))
            gradient_opacity = style_config.get('gradient_opacity', 200)
            for y in range(gradient_start_y, height):
                progress = (y - gradient_start_y) / (height - gradient_start_y)
                alpha = int(progress * gradient_opacity)
                draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
            
            try:
                font_path = CONFIG['font_path']
                title_font = ImageFont.truetype(font_path, style_config['title_size'])
            except IOError:
                title_font = ImageFont.load_default()

            layout = style_config.get('layout', 'bottom')
            
            def draw_text_with_multilayer_stroke(draw, position, text, font, fill_color, stroke_color, stroke_width):
                x, y = position
                
                # 获取画布尺寸
                img = draw._image
                width, height = img.size
                
                # 创建临时图层用于绘制文字主体
                text_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
                text_draw = ImageDraw.Draw(text_layer)
                text_draw.text((x, y), text, font=font, fill=fill_color, anchor="mm")
                
                # 创建描边图层
                stroke_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
                stroke_draw = ImageDraw.Draw(stroke_layer)
                stroke_draw.text((x, y), text, font=font, fill=stroke_color, anchor="mm")
                
                # 使用 MaxFilter 膨胀描边层(多次应用以达到所需宽度)
                for _ in range(stroke_width):
                    stroke_layer = stroke_layer.filter(ImageFilter.MaxFilter(3))
                
                # 将描边层和文字层合成到原画布
                img.paste(stroke_layer, (0, 0), stroke_layer)
                img.paste(text_layer, (0, 0), text_layer)
            
            if layout == "double" and style_config.get('title_position') == "split":
                y1 = int(height * style_config.get('title_top_y_ratio', 0.2))
                top_color = style_config.get('title_top_color', style_config.get('title_color', (255, 255, 255)))
                draw_text_with_multilayer_stroke(
                    draw, (width / 2, y1), cover_text_1, title_font,
                    top_color, style_config['title_stroke_color'], style_config['title_stroke_width']
                )
                if cover_text_2:
                    y2 = int(height * style_config.get('title_bottom_y_ratio', 0.75))
                    bottom_color = style_config.get('title_bottom_color', style_config.get('title_color', (255, 255, 255)))
                    draw_text_with_multilayer_stroke(
                        draw, (width / 2, y2), cover_text_2, title_font,
                        bottom_color, style_config['title_stroke_color'], style_config['title_stroke_width']
                    )
            else:
                title_y = int(height * style_config.get('title_y_ratio', 0.85))
                text_width = draw.textlength(cover_text_1, font=title_font)
                if text_width > width - 100:
                    scaled_size = int(style_config['title_size'] * (width - 100) / text_width)
                    title_font = ImageFont.truetype(font_path, scaled_size)
                
                draw_text_with_multilayer_stroke(
                    draw, (width / 2, title_y), cover_text_1, title_font,
                    style_config['title_color'], style_config['title_stroke_color'], style_config['title_stroke_width']
                )

            img = Image.alpha_composite(img, overlay)
            
            images_list = CONFIG['cover'].get('images', [])
            if images_list and len(images_list) > 0:
                for image_config in images_list:
                    if isinstance(image_config, dict):
                        img = CoverGenerator.add_image_to_cover(img, image_config)

            final_img = img.convert('RGB')
            final_img = final_img.filter(ImageFilter.SHARPEN)
            final_img.save(output_path, quality=95)
        except Exception as e:
            print(f"⚠️ 封面生成失败: {e}")
        finally:
            if temp_img.exists(): temp_img.unlink()

    @staticmethod
    def create_multiple_covers(video_path, start_sec, end_sec, cover_text_1, cover_text_2, base_output_path, cover_count):
        duration = end_sec - start_sec
        if cover_count <= 0: return []
        
        active_style = CONFIG['cover']['active_style']
        style_config = CONFIG['cover'].get(active_style, CONFIG['cover']['style1'])
        
        print(f"   使用封面样式: {style_config.get('name', active_style)}")
        
        if cover_count == 1: positions = [0.5]
        else: positions = [0.2 + (0.6 / (cover_count - 1)) * i for i in range(cover_count)]
        
        generated_covers = []
        for i, pos in enumerate(positions, 1):
            timestamp = start_sec + duration * pos
            output_path = base_output_path.parent / f"{base_output_path.stem}_cover{i}{base_output_path.suffix}"
            CoverGenerator.create_aesthetic_cover(
                video_path, timestamp, cover_text_1, cover_text_2, output_path, style_config
            )
            if output_path.exists(): generated_covers.append(output_path)
        
        if generated_covers: print(f"   已生成 {len(generated_covers)} 张封面")
        return generated_covers

class VideoProcessor:
    def __init__(self):
        self.base_dir = Path(CONFIG['output_dir'])
        self.base_dir.mkdir(exist_ok=True, parents=True)
        self.all_subs = []
        if os.path.exists(CONFIG['srt_file']):
            self.all_subs = SubtitleUtils.parse_srt(CONFIG['srt_file'])
        else:
            print("❌ 错误: 未找到 SRT 字幕文件!")

    def process_clip(self, index, clip_data):
        time_range = clip_data['timestamp']
        start_str, end_str = time_range.split('-')
        
        original_start_sec = SubtitleUtils.parse_srt_time(start_str)
        original_end_sec = SubtitleUtils.parse_srt_time(end_str)

        pre_sentences = CONFIG['padding']['pre_sentences']
        post_sentences = CONFIG['padding']['post_sentences']
        
        actual_start_sec, actual_end_sec = SubtitleUtils.get_expanded_time_range(
            self.all_subs, original_start_sec, original_end_sec, pre_sentences, post_sentences
        )
        actual_duration = actual_end_sec - actual_start_sec

        safe_title = re.sub(r'[\\/:*?"<>|]', '_', clip_data['title'])
        base_name = f"{safe_title}"
        output_video = self.base_dir / f"{base_name}.mp4"
        output_cover = self.base_dir / f"{base_name}.jpg"
        ass_file = self.base_dir / f"{base_name}.ass"

        print(f"\n🎬 [{index}] {clip_data['title']}")
        print(f"   缓冲策略: 向前{pre_sentences}句 | 向后{post_sentences}句")
        print(f"   剪辑范围: {SubtitleUtils.sec_to_srt_time(actual_start_sec)} --> {SubtitleUtils.sec_to_srt_time(actual_end_sec)}，切片时长: {actual_duration:.2f}秒")

        # ================= 计算字数限制 =================
        if CONFIG['subtitle'].get('orientation', 'horizontal') == 'vertical':
            max_char_len = 14
        else:
            max_char_len = 24
        # ===============================================

        has_subs = False
        
        if ass_file.exists():
            # 场景 A: 字幕文件已存在
            print(f"   ✅ 检测到已有字幕文件: {ass_file.name}")
            
            # [关键修改] 调用重排函数，直接修改文件
            SubtitleUtils.reformat_ass_file(ass_file, max_char_len)
            
            has_subs = True
        else:
            # 场景 B: 第一次运行，从 SRT 生成字幕
            if self.all_subs:
                # 注意：这里多传了一个 max_char_len 参数
                count = SubtitleUtils.create_ass_file(self.all_subs, ass_file, actual_start_sec, actual_end_sec, max_char_len)
                if count > 0: has_subs = True
            else:
                print("   ⚠️ 无字幕源，跳过字幕生成")

        # 准备 FFmpeg 命令
        ass_path = str(ass_file.absolute()).replace('\\', '/').replace(':', r'\:')
        current_dir = os.getcwd().replace('\\', '/').replace(':', r'\:')
        
        cmd = [
            'ffmpeg', 
            '-ss', str(actual_start_sec), 
            '-t', str(actual_duration),
            '-i', CONFIG['source_video'],
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-c:a', 'libmp3lame', '-b:a', '192k'
        ]
        
        if has_subs:
            cmd.extend(['-vf', f"ass='{ass_path}':fontsdir='{current_dir}'"])
            
        cmd.extend(['-y', str(output_video)])

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        cover_count = CONFIG['cover']['count']
        cover_text_1 = clip_data.get('cover_text_1', '')
        cover_text_2 = clip_data.get('cover_text_2', '')
        
        if not cover_text_1:
            cover_text_1 = clip_data.get('title', '未命名片段')
        
        CoverGenerator.create_multiple_covers(
            CONFIG['source_video'], original_start_sec, original_end_sec,
            cover_text_1, cover_text_2, output_cover, cover_count
        )

# ==========================================
# 2. 主程序入口
# ==========================================

def auto_detect_files(input_dir):
    """自动扫描文件夹下的视频和字幕文件"""
    print(f"正在扫描文件夹: {input_dir}")
    if not os.path.exists(input_dir):
        print(f"❌ 文件夹不存在: {input_dir}")
        sys.exit(1)
        
    files = os.listdir(input_dir)
    # 支持的视频格式，包含 FLV
    video_exts = ('.mp4', '.flv', '.mkv', '.mov', '.ts')
    
    videos = [f for f in files if f.lower().endswith(video_exts)]
    srts = [f for f in files if f.lower().endswith('.srt')]
    
    video_path = None
    srt_path = None
    
    # 视频检测
    if len(videos) == 0:
        print("❌ 未找到视频文件 (.mp4/.flv/.mkv 等)")
        sys.exit(1)
    elif len(videos) > 1:
        print(f"❌ 找到多个视频文件，无法确定使用哪个: {videos}")
        sys.exit(1)
    else:
        video_path = os.path.join(input_dir, videos[0])
        print(f"✅ 锁定视频: {videos[0]}")
        
    # 字幕检测
    if len(srts) == 0:
        print("⚠️ 未找到SRT字幕文件，将不写入字幕")
    elif len(srts) > 1:
        print(f"❌ 找到多个SRT文件，无法确定使用哪个: {srts}")
        sys.exit(1)
    else:
        srt_path = os.path.join(input_dir, srts[0])
        print(f"✅ 锁定字幕: {srts[0]}")
        
    return video_path, srt_path

def main():
    # 1. 自动检测文件并更新配置
    input_dir = CONFIG['input_dir']
    video_file, srt_file = auto_detect_files(input_dir)
    
    # 将检测到的路径注入到 CONFIG 中，以便兼容后续代码
    CONFIG['source_video'] = video_file
    CONFIG['srt_file'] = srt_file

    # ================= 自动更新输出路径 =================
    # 获取输入文件夹的名称
    folder_name = os.path.basename(os.path.normpath(input_dir))
    CONFIG['output_dir'] = os.path.join(CONFIG['output_dir'], folder_name)

    # ----------------- [修改] 清理逻辑 -----------------
    output_path_obj = Path(CONFIG['output_dir'])
    
    if output_path_obj.exists():
        print(f"🧹 检测到输出目录已存在，正在清理视频和封面 (保留 .ass 字幕)...")
        # 遍历目录下的所有文件
        for file_path in output_path_obj.iterdir():
            if file_path.is_file():
                # 检查后缀名，如果是视频或图片则删除
                if file_path.suffix.lower() in ['.mp4', '.mkv', '.flv', '.jpg', '.png', '.jpeg']:
                    try:
                        file_path.unlink()
                        # print(f"   已删除: {file_path.name}")
                    except Exception as e:
                        print(f"⚠️ 无法删除文件 {file_path.name}: {e}")
    else:
        # 如果目录不存在，则创建
        output_path_obj.mkdir(parents=True, exist_ok=True)
        print(f"✅ 输出目录已创建")
    # ---------------------------------------------------

    if not os.path.exists(CONFIG['source_video']):
        print(f"❌ 未找到视频文件: {CONFIG['source_video']}")
        return

    data_source_path = CONFIG['data_source']

    # 2. 检查数据源文件是否存在
    if not os.path.exists(data_source_path):
        print(f"❌ 未找到数据源文件: {data_source_path}")
        print(" 请确保 Data_source.txt 位于正确位置或在 CONFIG 中修改路径。")
        return

    # 3. 读取并解析 JSON
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
    print(f"当前封面样式: {CONFIG['cover']['active_style']}")
    print(f"每个视频生成封面数: {CONFIG['cover']['count']}")
    
    images_list = CONFIG['cover'].get('images', [])
    if images_list and len(images_list) > 0:
        print(f"图片叠加: 已启用 ({len(images_list)} 张)")
    else:
        print(f"图片叠加: 未启用")
    
    print(f"待处理片段数: {len(clips)}")
    print("=" * 60)

    processor = VideoProcessor()
    for i, clip in enumerate(clips, 1):
        try:
            processor.process_clip(i, clip)
        except Exception as e:
            print(f"❌ 处理片段 {i} 时出错: {e}")

    print("\n" + "=" * 60)
    print(f"✅ 所有片段处理完毕! 文件保存在: {CONFIG['output_dir']}")
    print("=" * 60)

if __name__ == "__main__":
    main()