import os
import re
import subprocess
from pathlib import Path

from core.cover_generator import CoverGenerator
from core.file_utils import clean_output_dir, sanitize_filename
from core.regen_script import write_regen_script
from core.subtitle_utils import SubtitleUtils
from core.caption_layout import find_word_timing_path
from core.highlight_selection import write_json
from core.media_geometry import caption_line_capacity, probe_video, resolve_subtitle_style

class VideoProcessor:
    def __init__(self, config, input_dir=None):
        media=probe_video(config['source_video'])
        self.config = dict(config,subtitle=resolve_subtitle_style(config.get('subtitle'),media))
        config=self.config
        self.base_dir = Path(config['output_dir'])
        self.base_dir.mkdir(exist_ok=True, parents=True)
        self.subtitle_utils = SubtitleUtils(config)
        self.cover_generator = CoverGenerator(config)
        self.all_subs = []
        self.input_dir = input_dir or config.get('input_dir', '')
        source = Path(config['source_video']).resolve()
        stat = source.stat()
        self.source_identity = {'path': str(source), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

        srt_file = config.get('srt_file')
        self.word_timing_path = find_word_timing_path(srt_file)
        if srt_file and os.path.exists(srt_file):
            self.all_subs = SubtitleUtils.parse_srt(srt_file)
        else:
            print("❌ 错误: 未找到 SRT 字幕文件!")

    def process_clip(
        self,
        index,
        clip_data,
        output_dir_override=None,
        generate_cover=True,
        index_width=2,
        force_regen_ass=False
    ):
        time_range = clip_data['timestamp']
        start_str, end_str = time_range.split('-')
        
        original_start_sec = SubtitleUtils.parse_srt_time(start_str)
        original_end_sec = SubtitleUtils.parse_srt_time(end_str)

        pre_sentences = self.config['padding']['pre_sentences']
        post_sentences = self.config['padding']['post_sentences']
        if clip_data.get('boundary_locked') is True:
            pre_sentences = post_sentences = 0
            actual_start_sec, actual_end_sec = original_start_sec, original_end_sec
        else:
            actual_start_sec, actual_end_sec = SubtitleUtils.get_expanded_time_range(
                self.all_subs, original_start_sec, original_end_sec, pre_sentences, post_sentences
            )
        actual_duration = actual_end_sec - actual_start_sec

        safe_title = sanitize_filename(clip_data.get('title', 'clip'))
        base_name = f"{safe_title}"
        if output_dir_override:
            clip_dir = Path(output_dir_override)
        else:
            prefix = f"{index:0{index_width}d}_"
            clip_dir = self.base_dir / f"{prefix}{safe_title}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clean_output_dir(clip_dir, include_images=generate_cover)

        output_video = clip_dir / f"{base_name}.mp4"
        output_cover = clip_dir / f"{base_name}.jpg"
        ass_file = clip_dir / f"{base_name}.ass"
        regen_script = clip_dir / "regen_clip.py"

        print(f"\n🎬 [{index}] {clip_data['title']}")
        print(f"   缓冲策略: 向前{pre_sentences}句 | 向后{post_sentences}句")
        print(
            f"   剪辑范围: {SubtitleUtils.sec_to_srt_time(actual_start_sec)} --> "
            f"{SubtitleUtils.sec_to_srt_time(actual_end_sec)}，切片时长: {actual_duration:.2f}秒"
        )

        max_char_len = caption_line_capacity(self.config['subtitle'])

        has_subs = False
        
        if force_regen_ass and ass_file.exists():
            try:
                ass_file.unlink()
            except Exception as e:
                print(f"⚠️ 无法删除字幕文件 {ass_file.name}: {e}")

        if ass_file.exists():
            print(f"   ✅ 检测到已有字幕文件: {ass_file.name}")
            self.subtitle_utils.reformat_ass_file(ass_file, max_char_len, actual_start_sec,
                                                  self.source_identity, self.word_timing_path)
            has_subs = True
        else:
            if self.all_subs:
                count = self.subtitle_utils.create_ass_file(
                    self.all_subs, ass_file, actual_start_sec, actual_end_sec, max_char_len,
                    source_identity=self.source_identity, word_timing_path=self.word_timing_path
                )
                if count > 0:
                    has_subs = True
            else:
                print("   ⚠️ 无字幕源，跳过字幕生成")

        if self.subtitle_utils.last_layout is not None:
            write_json(clip_dir / f"{base_name}.captions.layout.json", self.subtitle_utils.last_layout)

        ass_path = str(ass_file.absolute()).replace('\\', '/').replace(':', r'\:')
        current_dir = os.getcwd().replace('\\', '/').replace(':', r'\:')
        
        cmd = [
            'ffmpeg', 
            '-ss', str(actual_start_sec), 
            '-t', str(actual_duration),
            '-i', self.config['source_video'],
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-c:a', 'libmp3lame', '-b:a', '192k'
        ]
        
        if has_subs:
            cmd.extend(['-vf', f"ass='{ass_path}':fontsdir='{current_dir}'"])
            
        cmd.extend(['-y', str(output_video)])

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        if generate_cover:
            cover_config = self.config.get('cover')
            if not cover_config:
                print("⚠️ 未配置封面参数，跳过封面生成")
            else:
                cover_count = cover_config.get('count', 0)
                cover_text_1 = clip_data.get('cover_text_1', '')
                cover_text_2 = clip_data.get('cover_text_2', '')
                
                if not cover_text_1:
                    cover_text_1 = clip_data.get('title', '未命名片段')
                
                self.cover_generator.create_multiple_covers(
                    self.config['source_video'], original_start_sec, original_end_sec,
                    cover_text_1, cover_text_2, output_cover, cover_count, cover_config
                )

        if self.input_dir:
            write_regen_script(regen_script, clip_data, self.config)
