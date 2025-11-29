import re
import os
import json
import sys
import requests
from collections import defaultdict

# ==============================================================================
# [配置区域]
# ==============================================================================

class FileConfig:

    # 输入文件夹路径 (修改为你的文件夹路径)
    # 程序会自动在此文件夹下寻找唯一的 .ass 和 .srt 文件
    # .srt代表字幕文件，其中包含字幕信息
    # .ass代表弹幕文件，其中包含弹幕信息
    # 一定要确定该输入文件夹下有且仅有一个 .ass 和 .srt 文件，如果有多个字幕文件或者弹幕文件，程序会报错
    INPUT_DIR = r""
    
    # 输出文件
    OUTPUT_FILE = 'Data_source.txt'

class ApiConfig:
    # LLM API 设置
    API_KEY = ""  # 填写你自己的API Key
    BASE_URL = "https://api.siliconflow.cn/v1/chat/completions" # 硅基流动请求地址
    MODEL_NAME = "deepseek-ai/DeepSeek-V3.1-Terminus"  # 模型名称
    TIMEOUT = 60    # 请求超时时间

# 成员出场状态
MEMBER_STATUS = {
    "嘉然": 1,
    "贝拉": 1,
    "乃琳": 1,
    "心宜": 0,
    "思诺": 0
}

class AnalyzeConfig:
    # 算法参数
    TOP_N = 25              # 初始筛选出多少个高光片段（数量越大，最终筛选出的高光片段也越多，推荐填写20-30）
    WINDOW_SIZE = 10        # 密度计算的时间窗口(秒)，表示计算每多少秒内的热度（推荐填写10）
    MIN_DENSITY = 5         # 最小热度阈值（推荐填写5）
    MERGE_THRESHOLD = 10    # 合并间隔
    MIN_DURATION = 10       # 最小保留时长
    MAX_DURATION = 90       # 最大保留时长

# 弹幕权重
DANMAKU_WEIGHTS = [
    (r'警告|绷|笑死|名场面|锐评|蚌埠住了', 2.0),
    (r'[?\uff1f]', 1.8),
    (r'(哈{2,}|h{3,}|[啊]{2,})', 1.5),
    (r'可爱捏|急了|牛', 1.2)
]
LONG_TEXT_BONUS = (15, 1.2) 

# Prompt 模板
PROMPT_TEMPLATE = """
你是一位非常熟悉虚拟女团 A-SOUL（成员是嘉然、贝拉、乃琳）的资深剪辑UP主。现在需要基于一段{broadcast_type}的高能片段，生成直播精彩片段的元数据。

### 视频信息
- 直播类型: {broadcast_type}
- 出场成员: {active_members}
- 原始文件: {filename}

### 字幕内容 (成员发言或对话)
{subtitle_text}

### 弹幕反应 (观众情绪或反应)
{danmaku_text}

### 任务要求
请分析这个片段，**只输出一个纯净的 JSON 对象**，不要包含 markdown 标记或任何解释性文字。

JSON 字段生成策略：
1. **title**: 标题可以用夸张吸引眼球的词汇，如'震惊!'、'绷不住了!'、'笑死!'、'名场面!'、'破防了!'等开头吸引点击。
2. **cover_text_1**: 封面视觉核心，提炼最强冲突点，限制 **3-10个字** (如：嘉然身高被嘲)。
3. **cover_text_2**: 封面辅助吐槽，对主字的补充或反转，限制 **3-10个字** (如：乃琳当场笑疯)。

### JSON 输出格式
{{
  "title": "B站风格吸睛标题，可以使用**情绪/玩梗前缀+具体事件**的二段式标题。",
  "summary": "用粉丝视角的口吻，生动概括这个片段发生了什么（1-2句）",
  "cover_text_1": "封面核心大字(3-10字)",
  "cover_text_2": "封面补充小字(3-10字)",
  "highlight_reason": "说明为什么这是高光片段，观众为何有这样的弹幕反应。如果涉及有趣的梗，请标注出来"
}}
"""

# ==============================================================================
# [核心逻辑]
# ==============================================================================

class DanmakuAnalyzer:
    def __init__(self):
        # 初始化时执行自动文件检测
        self.input_dir = FileConfig.INPUT_DIR
        self.ass_file = None
        self.srt_file = None
        
        # 数据容器
        self.danmaku_data = []
        self.subtitle_data = []
        
        # 执行检测
        self._auto_detect_files()

    def _auto_detect_files(self):
        """自动检测文件夹下的 ASS 和 SRT 文件"""
        print(f"正在扫描文件夹: {self.input_dir}")
        
        if not os.path.exists(self.input_dir):
            print(f"❌ 错误: 文件夹不存在 -> {self.input_dir}")
            sys.exit(1)

        files = os.listdir(self.input_dir)
        # 忽略大小写进行后缀匹配
        ass_files = [f for f in files if f.lower().endswith('.ass')]
        srt_files = [f for f in files if f.lower().endswith('.srt')]

        # 检测 ASS 文件数量
        if len(ass_files) == 0:
            print(f"❌ 错误: 在文件夹中未找到 .ass 弹幕文件")
            sys.exit(1)
        elif len(ass_files) > 1:
            print(f"❌ 错误: 在文件夹中找到 {len(ass_files)} 个 .ass 文件，请保持弹幕文件唯一。")
            print(f"   找到的文件: {ass_files}")
            sys.exit(1)
        
        # 检测 SRT 文件数量
        if len(srt_files) == 0:
            print(f"❌ 错误: 在文件夹中未找到 .srt 字幕文件")
            sys.exit(1)
        elif len(srt_files) > 1:
            print(f"❌ 错误: 在文件夹中找到 {len(srt_files)} 个 .srt 文件，请保持字幕文件唯一。")
            print(f"   找到的文件: {srt_files}")
            sys.exit(1)

        # 锁定文件
        self.ass_file = os.path.join(self.input_dir, ass_files[0])
        self.srt_file = os.path.join(self.input_dir, srt_files[0])
        
        print(f"✅ 已锁定弹幕文件: {ass_files[0]}")
        print(f"✅ 已锁定字幕文件: {srt_files[0]}")
        print("-" * 50)

    def parse_ass_time(self, time_str):
        try:
            parts = time_str.split(':')
            if len(parts) != 3:
                raise ValueError("格式不是 H:M:S")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            return 0
    
    def parse_srt_time(self, time_str):
        try:
            time_str = time_str.replace(',', '.')
            parts = time_str.split(':')
            if len(parts) != 3:
                raise ValueError("格式不是 H:M:S")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            return 0
    
    def load_danmaku(self):
        if not os.path.exists(self.ass_file):
            print(f"错误: 找不到文件 {self.ass_file}")
            return []
        try:
            with open(self.ass_file, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return []
        
        pattern = r'Dialogue:\s*(\d+),(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),(.*)'
        matches = re.findall(pattern, content)
        count = 0
        
        for match in matches:
            try:
                start_time = self.parse_ass_time(match[1])
                raw_text = match[9]
                clean_text = re.sub(r'\{[^}]*\}', '', raw_text).strip()
                clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_text)
                if clean_text and start_time > 0:
                    self.danmaku_data.append({'time': start_time, 'text': clean_text})
                    count += 1
            except Exception:
                continue
        self.danmaku_data.sort(key=lambda x: x['time'])
        print(f"✓ 成功加载 {count} 条弹幕")
        return self.danmaku_data
    
    def load_subtitles(self):
        if not os.path.exists(self.srt_file):
            print(f"错误: 找不到文件 {self.srt_file}")
            return []
        try:
            with open(self.srt_file, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return []
        
        blocks = content.strip().split('\n\n')
        count = 0
        for block in blocks:
            try:
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    time_match = re.match(r'(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)', lines[1])
                    if time_match:
                        self.subtitle_data.append({
                            'start': self.parse_srt_time(time_match.group(1)),
                            'end': self.parse_srt_time(time_match.group(2)),
                            'text': ' '.join(lines[2:]).strip()
                        })
                        count += 1
            except Exception:
                continue
        print(f"✓ 成功加载 {count} 条字幕")
        return self.subtitle_data
    
    def get_danmaku_weight(self, text):
        weight = 1.0
        for pattern, score in DANMAKU_WEIGHTS:
            if re.search(pattern, text, re.IGNORECASE):
                weight = max(weight, score)
        limit, bonus = LONG_TEXT_BONUS
        if len(text) >= limit:
            weight = max(weight, bonus)
        return weight

    def calculate_density(self):
        if not self.danmaku_data: return {}, {}
        raw_score = defaultdict(float)
        raw_count = defaultdict(int)
        for danmaku in self.danmaku_data:
            t = int(danmaku['time'])
            weight = self.get_danmaku_weight(danmaku['text'])
            raw_score[t] += weight
            raw_count[t] += 1
            
        if not raw_score: return {}, {}
        max_time = max(raw_score.keys())
        min_time = min(raw_score.keys())
        window_size = AnalyzeConfig.WINDOW_SIZE
        score_map = {}
        count_map = {}
        for t in range(min_time, max_time + 1):
            curr_score = 0
            curr_count = 0
            for i in range(window_size):
                if (t + i) in raw_score:
                    curr_score += raw_score[t + i]
                    curr_count += raw_count[t + i]
            if curr_score > 0:
                score_map[t] = curr_score
                count_map[t] = curr_count
        
        print(f"✓ 计算密度: 窗口大小={window_size}秒, 有效时间点={len(score_map)}")
        return score_map, count_map

    def find_highlights(self):
        score_map, count_map = self.calculate_density()
        if not score_map: return []
            
        all_scores = sorted(score_map.values(), reverse=True)
        if not all_scores: return []

        p90 = all_scores[int(len(all_scores) * 0.1)]
        target_min = AnalyzeConfig.MIN_DENSITY
        adaptive_min = p90 if target_min > p90 else target_min
        
        # === 详细输出：高分窗口 ===
        high_score_windows = [(t, s) for t, s in score_map.items() if s >= adaptive_min]
        high_score_windows.sort(key=lambda x: x[1], reverse=True)
        
        print(f"\n找到 {len(high_score_windows)} 个高分时间窗口 (阈值: {adaptive_min:.1f})")
        print("前20个高分窗口:")
        for i, (t, s) in enumerate(high_score_windows[:20], 1):
            print(f"  {i}. {self.format_time(t)} - 热度:{s:.1f} - 弹幕数:{count_map.get(t,0)}")

        if not high_score_windows: return []

        # 合并逻辑
        raw_highlights = []
        top_limit = AnalyzeConfig.TOP_N * 5
        lookback = 5
        
        for start_time, score in high_score_windows[:top_limit]:
            raw_highlights.append({
                'start': max(0, start_time - lookback),
                'end': start_time + AnalyzeConfig.WINDOW_SIZE + lookback,
                'score': score,
                'count': count_map.get(start_time, 0)
            })
        raw_highlights.sort(key=lambda x: x['start'])
        
        merged = []
        for h in raw_highlights:
            if not merged:
                merged.append(h)
                continue
            last = merged[-1]
            gap = h['start'] - last['end']
            if gap <= AnalyzeConfig.MERGE_THRESHOLD:
                last['end'] = max(last['end'], h['end'])
                last['score'] = max(last['score'], h['score'])
                last['count'] += h['count']
                if last['end'] - last['start'] > AnalyzeConfig.MAX_DURATION:
                    last['end'] = last['start'] + AnalyzeConfig.MAX_DURATION
            else:
                merged.append(h)
        
        final_highlights = []
        for h in merged:
            duration = h['end'] - h['start']
            if AnalyzeConfig.MIN_DURATION <= duration <= AnalyzeConfig.MAX_DURATION:
                final_highlights.append(h)
        
        final_highlights.sort(key=lambda x: x['score'], reverse=True)
        final_highlights = final_highlights[:AnalyzeConfig.TOP_N]
        final_highlights.sort(key=lambda x: x['start'])
        
        # === 详细输出：最终选中片段 ===
        print(f"\n✓ 最终选中 {len(final_highlights)} 个高光片段:")
        for i, h in enumerate(final_highlights, 1):
            duration = h['end'] - h['start']
            print(f"  片段{i}: {self.format_time(h['start'])} - {self.format_time(h['end'])} "
                  f"(高光时长:{duration:.0f}秒)"
                  f"(弹幕:{h['count']}, 热度:{h['score']:.1f})")
        
        return final_highlights

    def generate_summary_with_ai(self, highlight):
        start, end = highlight['start'], highlight['end']
        # 弹幕往前回溯5秒，往后延迟5秒，获取更完整的信息，最多保留50条不重复的弹幕
        danmaku_context = [d['text'] for d in self.danmaku_data if start-5 <= d['time'] <= end+5]    
        danmaku_text = '\n'.join([f"- {t}" for t in list(set(danmaku_context))[:50]]) or "(无弹幕)"
        # 字幕往前回溯25秒，往后延迟5秒，获取更完整的信息
        sub_context = [s for s in self.subtitle_data if s['start'] <= end+5 and s['end'] >= start-25] 
        sub_text = '\n'.join([f"{self.format_time(s['start'])}: {s['text']}" for s in sub_context]) or "(无字幕)"
        
        active_members = [k for k, v in MEMBER_STATUS.items() if v == 1]
        
        if len(active_members) == 1:
            broadcast_desc = f"{active_members[0]}单播"
        elif len(active_members) > 1:
            broadcast_desc = "A-SOUL团播"
        else:
            broadcast_desc = "A-SOUL直播"
        
        prompt = PROMPT_TEMPLATE.format(
            broadcast_type=broadcast_desc,
            active_members=', '.join(active_members),
            filename=os.path.basename(self.srt_file),
            subtitle_text=sub_text,
            danmaku_text=danmaku_text
        )

        try:
            response = requests.post(
                ApiConfig.BASE_URL,
                headers={'Authorization': f'Bearer {ApiConfig.API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': ApiConfig.MODEL_NAME,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.7,
                    'max_tokens': 600
                },
                timeout=ApiConfig.TIMEOUT
            )
            
            if response.status_code == 200:
                ai_content = response.json()['choices'][0]['message']['content']
                clean_json = re.sub(r'```json\s*|\s*```', '', ai_content).strip()
                result_json = json.loads(clean_json)
                time_str = f"{self.format_time(start)}-{self.format_time(end)}"
                result_json = {
                    "timestamp": time_str,
                    **result_json,
                }
                return result_json
            else:
                return {"title": f"AI错误 {response.status_code}"}
        except Exception:
            return {"title": "AI生成失败"}

    def format_time(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def run(self):
        print("=" * 70)
        print("A-SOUL 弹幕高光片段分析器 v3.1 (自动文件扫描版)")
        print("=" * 70)
        print("成员出场配置:")
        for k, v in MEMBER_STATUS.items():
            status = "✓ 出场" if v == 1 else "✗ 未出场"
            print(f"  {k}: {status}")
        print()
        
        self.load_danmaku()
        self.load_subtitles()
        
        if not self.danmaku_data: return

        highlights = self.find_highlights()
        if not highlights:
            print("[提示] 未找到高光片段")
            return

        print("\n" + "=" * 70)
        print("生成AI总结...")
        print("=" * 70)
        
        results = []
        for i, h in enumerate(highlights, 1):
            duration = h['end'] - h['start']

            print(f"\n[{i}/{len(highlights)}] 分析片段中...")
            ai_info = self.generate_summary_with_ai(h)
            
            # 默认值填充，防止KeyError
            title = ai_info.get('title', 'AI生成失败')
            summary = ai_info.get('summary', '无')
            reason = ai_info.get('highlight_reason', '无')
            
            print(f"  高光时段: {self.format_time(h['start'])} - {self.format_time(h['end'])} ({duration:.0f}秒)")
            print(f"  标题: {title}")
            print(f"  概括: {summary}")
            print(f"  原因: {reason}")
            
            result_item = {
                'index': i,
                'raw_start': h['start'],
                'raw_end': h['end'],
                'duration': duration,
                'score': h['score'],
                'danmaku_count': h['count'],
                **ai_info
            }
            results.append(result_item)
            
        # === 最终汇总 ===
        print("\n" + "=" * 70)
        print("最终汇总 (按热度排序)")
        print("=" * 70)
        
        sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
        for res in sorted_results:
            print(f"\n【片段 {res['index']}】(热度排名: 第{sorted_results.index(res) + 1})")
            print(f"高光时段: {self.format_time(res['raw_start'])} - {self.format_time(res['raw_end'])}")
            print(f"时长: {res['duration']:.0f}秒")
            print(f"弹幕: {res['danmaku_count']}条 | 热度: {res['score']:.1f}")
            print(f"标题: {res.get('title', '')}")
            print(f"概括: {res.get('summary', '')}")
            print(f"原因: {res.get('highlight_reason', '')}")
        
        self.export(results) 

    def export(self, results):
        try:
            output_keys = ['timestamp', 'title', 'summary', 'cover_text_1', 'cover_text_2', 'highlight_reason']
            # 过滤出需要的字段
            simple_data = [{k: v for k, v in r.items() if k in output_keys} for r in results]
            
            # 写入到 Data_source.txt (保持 JSON 格式以便读取)
            with open(FileConfig.OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(simple_data, f, ensure_ascii=False, indent=2)
                
            print(f"\n✓ 标准格式结果已保存到: {FileConfig.OUTPUT_FILE}")
            
        except Exception as e:
            print(f"导出失败: {e}")

if __name__ == "__main__":
    try:
        app = DanmakuAnalyzer()
        app.run()
    except SystemExit:
        pass # 允许正常退出
    except Exception as e:
        print(f"程序运行出错: {e}")