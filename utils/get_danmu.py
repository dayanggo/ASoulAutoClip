import os
import re
import math
import random
import requests
from io import BytesIO

# ==================== 配置区域 ====================

# 1. 视频输入（支持BV号或完整链接）
VIDEO_INPUT = ""

# 2. SESSDATA（可选，用于获取更多弹幕历史）
SESSDATA = ""

# 3. 输出目录
OUTPUT_DIR = r""

# 4. 多P视频处理选项
DOWNLOAD_ALL_PARTS = True  # 是否下载所有分P（True=下载所有P，False=仅下载第一P）

# ==================================================


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
            if not byte_data:
                return 0
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
            if field_num == 2:
                dm['progress'] = self._read_varint()
            elif field_num == 3:
                dm['mode'] = self._read_varint()
            elif field_num == 4:
                dm['fontsize'] = self._read_varint()
            elif field_num == 5:
                dm['color'] = self._read_varint()
            elif field_num == 7:
                length = self._read_varint()
                dm['content'] = self.stream.read(length).decode('utf-8', errors='ignore')
            else:
                self._skip_field(wire_type)
        return dm

    def _skip_field(self, wire_type):
        if wire_type == 0:
            self._read_varint()
        elif wire_type == 1:
            self.stream.read(8)
        elif wire_type == 2:
            length = self._read_varint()
            self.stream.read(length)
        elif wire_type == 5:
            self.stream.read(4)


# ==================== 弹幕下载器类 ====================
class DanmakuDownloader:
    def __init__(self, video_input, sessdata="", output_dir="弹幕输出"):
        self.bvid = self._extract_bvid(video_input)
        if not self.bvid:
            raise ValueError("无效的输入：无法提取 BV 号")
        
        self.video_url = f"https://www.bilibili.com/video/{self.bvid}"
        self.output_dir = output_dir
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            print(f"[*] 已创建输出目录: {self.output_dir}")
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.video_url,
        }
        
        if sessdata:
            clean_sessdata = sessdata.strip().strip("'").strip('"')
            self.headers['Cookie'] = f'SESSDATA={clean_sessdata}'
        
        self.aid = None
        self.title = "unknown"
        self.pages = []

    def _extract_bvid(self, text):
        pattern = r'(BV\w{10})'
        match = re.search(pattern, text)
        return match.group(1) if match else None

    def get_safe_filename(self, filename):
        return re.sub(r'[\\/:*?"<>|]', '_', filename)

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
        self.pages = video_data['pages']
        
        print(f"[*] 视频标题: {self.title}")
        print(f"[*] 共 {len(self.pages)} 个分P")
        
        for i, page in enumerate(self.pages, 1):
            print(f"    P{i}: {page['part']} (时长: {page['duration']}秒)")

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
            
            danmaku_list = []
            
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
                        danmaku_list.extend(decoder.decode_danmaku_segment())
                    except:
                        pass
            
            print(f"\n[*] P{page_num} 弹幕下载完成，共 {len(danmaku_list)} 条")
            
            if danmaku_list:
                danmaku_list.sort(key=lambda x: x['progress'])
                
                # 文件名添加P数标识
                part_suffix = f"_P{page_num}" if len(self.pages) > 1 else ""
                title_for_file = f"{self.title} - {part_title}" if part_title else self.title
                
                # 保存ASS格式
                ass_filename = self.get_safe_filename(f"{self.title}{part_suffix}_弹幕.ass")
                ass_file = os.path.join(self.output_dir, ass_filename)
                self._write_danmaku_ass(ass_file, title_for_file, danmaku_list)
                print(f"[+] ASS弹幕已保存: {ass_filename}")

    def _write_danmaku_ass(self, filename, title_for_header, danmaku_list):
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
            for dm in danmaku_list:
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

    def run(self, download_all_parts=True):
        try:
            self.get_video_info()
            self.download_danmaku(download_all=download_all_parts)
            
            print(f"\n{'='*50}")
            print("弹幕下载完成！")
            print(f"文件保存位置: {self.output_dir}")
            print(f"{'='*50}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n[-] 发生错误: {e}")


# ==================== 主程序入口 ====================
if __name__ == "__main__":
    print("="*60)
    print("B站弹幕下载器 - 仅下载ASS格式弹幕")
    print("="*60)
    print(f"视频输入: {VIDEO_INPUT}")
    print(f"下载所有分P: {DOWNLOAD_ALL_PARTS}")
    print("="*60)
    
    downloader = DanmakuDownloader(VIDEO_INPUT, SESSDATA, OUTPUT_DIR)
    downloader.run(download_all_parts=DOWNLOAD_ALL_PARTS)