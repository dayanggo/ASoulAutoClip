from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
if __package__ in (None, ""):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

import os
import re
import time
import math
import random
import requests
import yt_dlp
from io import BytesIO
from datetime import datetime

# ==================== 配置区域 ====================

# 1. 视频输入（支持BV号或完整链接）
VIDEO_INPUT = ""

# 2. SESSDATA（用于下载会员视频和更多弹幕历史）
SESSDATA = ""

# 3. 输出目录
OUTPUT_DIR = r""

# 4. 下载选项（True=下载，False=不下载）
DOWNLOAD_VIDEO = False      # 是否下载视频
DOWNLOAD_SUBTITLE = False   # 是否下载字幕
DOWNLOAD_DANMAKU = True    # 是否下载弹幕

# 5. 多P视频处理选项
DOWNLOAD_ALL_PARTS = True  # 是否下载所有分P（True=下载所有P，False=仅下载第一P）

# 6. 字幕纠错选项
AUTO_CORRECT_SUBTITLE = True  # 是否自动纠错字幕（需要asr_dict.txt字典文件）

# ==================================================

# ==================== 字幕纠错器 ====================
class FileBasedCorrector:
    def __init__(self, dict_path="asr_dict.txt"):
        """初始化纠错器"""
        self.dict_path = dict_path
        self.error_mapping = self._load_dictionary()
        
        # 按键长度降序排序，确保长词优先匹配
        self.sorted_keys = sorted(
            self.error_mapping.keys(),
            key=len,
            reverse=True
        )
        if self.error_mapping:
            print(f"✅ 成功加载纠错字典，共 {len(self.sorted_keys)} 条规则。")

    def _load_dictionary(self):
        """读取txt文件并转为字典"""
        mapping = {}
        
        # 尝试多个可能的字典文件路径
        possible_paths = [
            self.dict_path,  # 当前目录
            os.path.join(os.path.dirname(os.path.abspath(__file__)), self.dict_path),  # 脚本目录（使用abspath更稳健）
            os.path.join(os.getcwd(), self.dict_path)  # 工作目录
        ]
        if self.dict_path == "asr_dict.txt":
            possible_paths.append(str(_BootstrapPath(__file__).resolve().parents[2] / "resources/dictionaries/asr_dict.txt"))
        
        dict_file = None
        for path in possible_paths:
            if os.path.exists(path):
                dict_file = path
                break
        
        if not dict_file:
            print(f"⚠️ 未找到字典文件 {self.dict_path}，字幕纠错功能将被跳过。")
            return mapping

        print(f"📂 正在加载字典: {dict_file} ...")
        try:
            with open(dict_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2:
                        wrong, right = parts[0], parts[1]
                        # 处理英文空格占位符
                        wrong = wrong.replace('_', ' ')
                        right = right.replace('_', ' ')

                        # 冲突检测
                        if wrong in mapping:
                            existing = mapping[wrong]
                            if existing != right:
                                print(f"⚠️ [冲突警告] 第{line_num}行: '{wrong}' 此前定义为 -> '{existing}'，将被覆盖为 -> '{right}'")
                        
                        mapping[wrong] = right
        except Exception as e:
            print(f"❌ 加载字典文件失败: {e}")
        
        return mapping

    def correct_text(self, text):
        """执行纠错核心逻辑"""
        if not self.error_mapping:
            return text
            
        corrected_text = text
        for error in self.sorted_keys:
            correct = self.error_mapping[error]
            # 使用正则忽略大小写替换
            pattern = re.compile(re.escape(error), re.IGNORECASE)
            corrected_text = pattern.sub(correct, corrected_text)
        return corrected_text

    def correct_file(self, file_path):
        """直接纠错文件（覆盖原文件）"""
        if not self.error_mapping:
            return False
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            
            corrected_text = self.correct_text(raw_text)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(corrected_text)
            
            return True
        except Exception as e:
            print(f"❌ 纠错文件时出错 {file_path}: {e}")
            return False

# ==================== Protobuf 解码器 ====================
class SimpleProtobufDecoder:
    def __init__(self, data):
        self.stream = BytesIO(data)
        self.end = len(data)

    def _read_varint(self):
        result = 0
        shift = 0
        while True:
            if self.stream.tell() >= self.end:
                return 0
            byte_data = self.stream.read(1)
            if not byte_data: return 0
            byte = ord(byte_data)
            result |= (byte & 0x7f) << shift
            if not (byte & 0x80):
                break
            shift += 7
        return result

    def decode_danmaku_segment(self):
        danmakus = []
        while self.stream.tell() < self.end:
            tag = self._read_varint()
            field_num = tag >> 3
            wire_type = tag & 0x07
            if field_num == 1 and wire_type == 2:
                length = self._read_varint()
                start_pos = self.stream.tell()
                danmaku = self._decode_danmaku_elem(start_pos + length)
                if danmaku:
                    danmakus.append(danmaku)
            else:
                self._skip_field(wire_type)
        return danmakus

    def _decode_danmaku_elem(self, end_pos):
        dm = {'progress': 0, 'mode': 1, 'fontsize': 25, 'color': 16777215, 'content': ''}
        while self.stream.tell() < end_pos:
            tag = self._read_varint()
            field_num = tag >> 3
            wire_type = tag & 0x07
            if field_num == 2: dm['progress'] = self._read_varint()
            elif field_num == 3: dm['mode'] = self._read_varint()
            elif field_num == 4: dm['fontsize'] = self._read_varint()
            elif field_num == 5: dm['color'] = self._read_varint()
            elif field_num == 7:
                length = self._read_varint()
                dm['content'] = self.stream.read(length).decode('utf-8', errors='ignore')
            else:
                self._skip_field(wire_type)
        return dm

    def _skip_field(self, wire_type):
        if wire_type == 0: self._read_varint()
        elif wire_type == 1: self.stream.read(8)
        elif wire_type == 2:
            length = self._read_varint()
            self.stream.read(length)
        elif wire_type == 5: self.stream.read(4)

# ==================== 主下载器类 ====================
class BilibiliDownloader:
    def __init__(self, video_input, sessdata="", output_dir="downloads", auto_correct=False):
        self.bvid = self._extract_bvid(video_input)
        if not self.bvid:
            raise ValueError("无效的输入：无法提取 BV 号")
        
        self.video_url = f"https://www.bilibili.com/video/{self.bvid}"
        self.output_dir = output_dir
        self.save_dir = os.path.join(self.output_dir)
        
        # 保存纠错配置
        self.auto_correct = auto_correct
        self.corrector = None
        
        # 如果开启纠错，实例化纠错器
        if self.auto_correct:
            self.corrector = FileBasedCorrector()
        
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
            print(f"[*] 已创建输出目录: {self.save_dir}")
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.video_url,
        }
        
        if sessdata:
            clean_sessdata = sessdata.strip().strip("'").strip('"')
            self.headers['Cookie'] = f'SESSDATA={clean_sessdata}'
        
        self.aid = None
        self.title = "unknown"
        self.author = "unknown"
        self.pages = []  # 存储所有分P信息
        self.danmaku_list = []

    def _extract_bvid(self, text):
        pattern = r'(BV\w{10})'
        match = re.search(pattern, text)
        return match.group(1) if match else None

    def get_safe_filename(self, filename):
        return re.sub(r'[\\/:*?"<>|]', '_', filename)

    def get_filepath(self, filename):
        safe_filename = self.get_safe_filename(filename)
        return os.path.join(self.save_dir, safe_filename)

    def get_video_info(self):
        print(f"\n[*] 正在获取视频信息: {self.bvid}")
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={self.bvid}"
        resp = requests.get(url, headers=self.headers)
        data = resp.json()
        
        if data['code'] != 0:
            raise Exception(f"API错误: {data.get('message', '未知错误')}")
        
        video_data = data['data']
        self.aid = video_data['aid']
        self.title = video_data['title']
        self.author = video_data['owner']['name']
        
        # 获取所有分P信息
        self.pages = video_data['pages']
        
        print(f"[*] 视频标题: {self.title}")
        print(f"[*] UP主: {self.author}")
        print(f"[*] 共 {len(self.pages)} 个分P")
        
        # 显示所有分P信息
        for i, page in enumerate(self.pages, 1):
            print(f"    P{i}: {page['part']} (时长: {page['duration']}秒)")
        
        # 保持兼容性：默认使用第一个分P的cid
        self.cid = self.pages[0]['cid']

    # ==================== 视频下载 ====================
    def download_video(self):
        print(f"\n{'='*50}")
        print("开始下载视频")
        print(f"{'='*50}")
        
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': os.path.join(self.save_dir, '%(title)s.%(ext)s'),
            'writethumbnail': True,
            'cookiefile': 'Workspace/cookies.txt'
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.video_url])
            print("[+] 视频下载完成！")
        except Exception as e:
            print(f"[-] 视频下载失败: {e}")

    # ==================== 字幕下载 ====================
    def download_subtitle(self, download_all=True):
        print(f"\n{'='*50}")
        print("开始下载字幕")
        print(f"{'='*50}")
        
        pages_to_download = self.pages if download_all else [self.pages[0]]
        
        for idx, page in enumerate(pages_to_download, 1):
            page_num = idx
            cid = page['cid']
            part_title = page['part']
            
            if len(self.pages) > 1:
                print(f"\n[*] P{page_num}: {part_title}")
            
            subtitle_url = self._get_subtitle_url(cid)
            if not subtitle_url:
                print(f"[-] P{page_num} 没有字幕")
                continue
            
            try:
                resp = requests.get(subtitle_url, headers=self.headers)
                subtitle_list = resp.json()['body']
                print(f"[*] P{page_num} 获取到 {len(subtitle_list)} 条字幕")
                
                # 文件名添加P数标识
                part_suffix = f"_P{page_num}" if len(self.pages) > 1 else ""
                
                # 保存SRT格式
                srt_file = self.get_filepath(f"{self.title}{part_suffix}_字幕.srt")
                self._save_subtitle_srt(subtitle_list, srt_file)
                print(f"[+] SRT字幕已保存: {os.path.basename(srt_file)}")
                
                # 保存TXT格式
                txt_file = self.get_filepath(f"{self.title}{part_suffix}_字幕.txt")
                self._save_subtitle_txt(subtitle_list, txt_file, part_title)
                print(f"[+] TXT字幕已保存: {os.path.basename(txt_file)}")
                
                # 自动纠错字幕
                if self.auto_correct and self.corrector and self.corrector.error_mapping:
                    print(f"[*] P{page_num} 开始自动纠错字幕...")
                    
                    if self.corrector.correct_file(srt_file):
                        print(f"[+] SRT字幕纠错完成")
                    
                    if self.corrector.correct_file(txt_file):
                        print(f"[+] TXT字幕纠错完成")
                
            except Exception as e:
                print(f"[-] P{page_num} 字幕下载失败: {e}")

    def _get_subtitle_url(self, cid):
        url = f"https://api.bilibili.com/x/player/wbi/v2?aid={self.aid}&cid={cid}"
        resp = requests.get(url, headers=self.headers)
        data = resp.json()
        
        if 'data' not in data or 'subtitle' not in data['data']:
            return None
        
        subtitles = data['data']['subtitle']['subtitles']
        if not subtitles:
            return None
        
        return "https:" + subtitles[0]['subtitle_url']

    def _save_subtitle_srt(self, subtitle_list, filename):
        def format_time(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        
        with open(filename, 'w', encoding='utf-8') as f:
            for idx, item in enumerate(subtitle_list, 1):
                f.write(f"{idx}\n")
                f.write(f"{format_time(item['from'])} --> {format_time(item['to'])}\n")
                f.write(f"{item['content']}\n\n")

    def _save_subtitle_txt(self, subtitle_list, filename, part_title=""):
        def format_time(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        
        with open(filename, 'w', encoding='utf-8') as f:
            title_to_write = f"{self.title} - {part_title}" if part_title else self.title
            f.write(f"{title_to_write}\n")
            f.write(f"{self.video_url}\n")
            f.write(f"{self.author} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            for item in subtitle_list:
                f.write(f"{format_time(item['from'])} {item['content']}\n")

    # ==================== 弹幕下载 ====================
    def download_danmaku(self, download_all=True):
        print(f"\n{'='*50}")
        print("开始下载弹幕")
        print(f"{'='*50}")
        
        pages_to_download = self.pages if download_all else [self.pages[0]]
        
        for idx, page in enumerate(pages_to_download, 1):
            page_num = idx
            cid = page['cid']
            part_title = page['part']
            duration = page['duration']
            
            if len(self.pages) > 1:
                print(f"\n[*] P{page_num}: {part_title}")
            
            # 清空之前的弹幕列表
            self.danmaku_list = []
            
            total_segments = math.ceil(duration / 360)
            print(f"[*] P{page_num} 共 {total_segments} 个弹幕分片")
            
            for i in range(1, total_segments + 1):
                print(f"\r[*] P{page_num} 下载弹幕分片 {i}/{total_segments} ...", end='')
                url = f"https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&pid={self.aid}&segment_index={i}"
                resp = requests.get(url, headers=self.headers)
                
                if resp.status_code == 200:
                    if resp.content.startswith(b'{') and b'"code":' in resp.content:
                        continue
                    try:
                        decoder = SimpleProtobufDecoder(resp.content)
                        self.danmaku_list.extend(decoder.decode_danmaku_segment())
                    except:
                        pass
            
            print(f"\n[*] P{page_num} 弹幕下载完成，共 {len(self.danmaku_list)} 条")
            
            if self.danmaku_list:
                self.danmaku_list.sort(key=lambda x: x['progress'])
                
                # 文件名添加P数标识
                part_suffix = f"_P{page_num}" if len(self.pages) > 1 else ""
                
                self._save_danmaku_files(part_suffix, part_title)

    def _save_danmaku_files(self, part_suffix="", part_title=""):
        # 保存ASS格式
        title_for_file = f"{self.title} - {part_title}" if part_title else self.title
        ass_file = self.get_filepath(f"{self.title}{part_suffix}_弹幕.ass")
        self._write_danmaku_ass(ass_file, title_for_file)
        print(f"[+] ASS弹幕已保存: {os.path.basename(ass_file)}")
        
        # 保存SRT格式
        srt_file = self.get_filepath(f"{self.title}{part_suffix}_弹幕.srt")
        self._write_danmaku_srt(srt_file)
        print(f"[+] SRT弹幕已保存: {os.path.basename(srt_file)}")

    def _write_danmaku_ass(self, filename, title_for_header):
        def sec_to_ass(s):
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            return f"{int(h)}:{int(m):02d}:{s:05.2f}"
        
        header = f"""[Script Info]
Title: {title_for_header}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Danmaku,Microsoft YaHei,25,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        
        track_count = 15
        tracks = [0] * track_count
        
        with open(filename, 'w', encoding='utf-8-sig') as f:
            f.write(header)
            for dm in self.danmaku_list:
                start = dm['progress'] / 1000.0
                duration = 8.0
                end = start + duration
                
                b = (dm['color'] & 0xFF)
                g = (dm['color'] >> 8) & 0xFF
                r = (dm['color'] >> 16) & 0xFF
                color_ass = f"&H{b:02X}{g:02X}{r:02X}"
                
                track_idx = random.randint(0, track_count - 1)
                for i in range(track_count):
                    if start >= tracks[i]:
                        track_idx = i
                        break
                tracks[track_idx] = start + (duration / 2)
                y_pos = 50 + (track_idx * 40)
                
                text = dm['content'].replace('\n', ' ').replace('\r', '')
                mode = dm['mode']
                
                if mode in [4, 5]:
                    move = f"\\pos(960,{y_pos if mode==5 else 1000})"
                    end_str = sec_to_ass(start + 4.0)
                else:
                    est_len = len(text) * 25
                    move = f"\\move(2020, {y_pos}, {-100 - est_len}, {y_pos})"
                    end_str = sec_to_ass(end)
                
                color_tag = f"\\c{color_ass}" if color_ass != "&HFFFFFF" else ""
                f.write(f"Dialogue: 0,{sec_to_ass(start)},{end_str},Danmaku,,0,0,0,,{{{move}{color_tag}}}{text}\n")

    def _write_danmaku_srt(self, filename):
        def sec_to_srt(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = int(s % 60)
            ms = int((s - int(s)) * 1000)
            return f"{h:02}:{m:02}:{sec:02},{ms:03}"
        
        with open(filename, 'w', encoding='utf-8') as f:
            for i, dm in enumerate(self.danmaku_list):
                start = dm['progress'] / 1000.0
                f.write(f"{i+1}\n")
                f.write(f"{sec_to_srt(start)} --> {sec_to_srt(start + 4.0)}\n")
                f.write(f"{dm['content']}\n\n")

    # ==================== 主运行函数 ====================
    def run(self, download_video=True, download_subtitle=True, download_danmaku=True, download_all_parts=True):
        try:
            # 获取视频基本信息
            self.get_video_info()
            time.sleep(1)
            
            # 根据配置下载内容
            if download_video:
                self.download_video()
            
            if download_subtitle:
                time.sleep(1)
                if self.auto_correct:
                    print(f"\n[*] 已启用字幕自动纠错功能")
                self.download_subtitle(download_all=download_all_parts)
            
            if download_danmaku:
                time.sleep(1)
                self.download_danmaku(download_all=download_all_parts)
            
            print(f"\n{'='*50}")
            print("所有任务完成！")
            print(f"文件保存位置: {self.save_dir}")
            print(f"{'='*50}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n[-] 发生错误: {e}")


# ==================== 主程序入口 ====================
if __name__ == "__main__":
    print("="*60)
    print("B站视频下载器 - 视频+字幕+弹幕")
    print("="*60)
    print(f"视频输入: {VIDEO_INPUT}")
    print(f"下载视频: {DOWNLOAD_VIDEO}")
    print(f"下载字幕: {DOWNLOAD_SUBTITLE}")
    print(f"下载弹幕: {DOWNLOAD_DANMAKU}")
    print(f"下载所有分P: {DOWNLOAD_ALL_PARTS}")
    print(f"自动纠错: {AUTO_CORRECT_SUBTITLE}")
    print("="*60)
    
    downloader = BilibiliDownloader(VIDEO_INPUT, SESSDATA, OUTPUT_DIR, auto_correct=AUTO_CORRECT_SUBTITLE)
    
    downloader.run(
        download_video=DOWNLOAD_VIDEO,
        download_subtitle=DOWNLOAD_SUBTITLE,
        download_danmaku=DOWNLOAD_DANMAKU,
        download_all_parts=DOWNLOAD_ALL_PARTS
    )
