import json
import re
from datetime import datetime, timedelta

# ================= 配置区域 =================
# 逻辑：时间差 = TIME_1（官方） - TIME_2（非官方），用于对齐官方录播和非官方录播的时间
# 修正后的时间 = 原时间 - 时间差
TIME_1 = "00:09:04"  # 官方录播op结束时间 (基准)
TIME_2 = "00:00:55"  # 非官方录播op结束时间 (当前)

# 输入文件名 (同时也是输出文件名，会覆盖原文件)
FILE_PATH = "Data_source.txt"       
# ===========================================

def time_str_to_seconds(time_str):
    """将 HH:MM:SS 格式转换为总秒数"""
    try:
        t = datetime.strptime(time_str, "%H:%M:%S")
        seconds = t.hour * 3600 + t.minute * 60 + t.second
        return seconds
    except ValueError:
        print(f"警告: 时间格式错误 {time_str}，跳过计算")
        return 0

def seconds_to_time_str(seconds):
    """将总秒数转换回 HH:MM:SS 格式"""
    seconds = max(0, int(seconds)) # 防止负数
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def extract_json_array(text):
    """
    从杂乱文本中提取 JSON 数组。
    逻辑：寻找第一个 '[' 和最后一个 ']' 之间的内容。
    """
    start_index = text.find('[')
    end_index = text.rfind(']')

    if start_index != -1 and end_index != -1 and end_index > start_index:
        # 提取两个符号之间的内容（包括符号本身）
        return text[start_index : end_index + 1]
    return None

def main():
    # 1. 计算时间差
    seconds_1 = time_str_to_seconds(TIME_1)
    seconds_2 = time_str_to_seconds(TIME_2)
    diff_seconds = seconds_1 - seconds_2
    
    print(f"时间修正配置: {TIME_1} - {TIME_2}")
    print(f"需减去的时间差: {diff_seconds} 秒")
    print("-" * 30)

    # 2. 读取文件内容（读取为纯文本）
    try:
        with open(FILE_PATH, 'r', encoding='utf-8') as f:
            raw_content = f.read()
    except FileNotFoundError:
        print(f"错误: 找不到文件 {FILE_PATH}")
        return

    # 3. 提取 JSON 部分
    json_str = extract_json_array(raw_content)
    
    if not json_str:
        print("错误: 在文件中未找到有效的 JSON 数组结构（未找到配对的 '[' 和 ']'）")
        return

    # 4. 解析 JSON
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"错误: 提取的内容不是合法的 JSON 格式。\n错误信息: {e}")
        return

    # 5. 处理数据
    processed_count = 0
    for item in data:
        if "timestamp" in item:
            original_ts = item["timestamp"]
            try:
                # 分割时间段
                start_str, end_str = original_ts.split('-')
                
                # 转换、计算、还原
                start_sec = time_str_to_seconds(start_str)
                end_sec = time_str_to_seconds(end_str)
                
                new_start_sec = start_sec - diff_seconds
                new_end_sec = end_sec - diff_seconds
                
                new_start_str = seconds_to_time_str(new_start_sec)
                new_end_str = seconds_to_time_str(new_end_sec)
                
                item["timestamp"] = f"{new_start_str}-{new_end_str}"
                processed_count += 1
                
            except ValueError:
                continue # 忽略格式不对的 timestamp

    # 6. 覆盖保存回原文件
    # 注意：这会丢弃原文件中 JSON 数组以外的杂乱文字，只保存纯净的 JSON
    try:
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"成功处理 {processed_count} 条数据！")
        print(f"文件已覆盖更新: {FILE_PATH}")
    except Exception as e:
        print(f"保存文件时发生错误: {e}")

if __name__ == "__main__":
    main()