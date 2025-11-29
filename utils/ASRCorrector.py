import re
import os
from typing import Dict, List

# ================= 配置区域 =================
# 在这里填入你要处理的文件夹路径

TARGET_FOLDER = r""
# ===========================================

class FileBasedCorrector:
    def __init__(self):
        """
        初始化纠错器
        """
        self.error_mapping = self._load_dictionary()
        
        # 按键长度降序排序，确保长词优先匹配
        self.sorted_keys = sorted(
            self.error_mapping.keys(),
            key=len,
            reverse=True
        )
        print(f"✅ 成功加载纠错字典，共 {len(self.sorted_keys)} 条规则。")

    def _load_dictionary(self) -> Dict[str, str]:
        """读取txt文件并转为字典（含冲突检测）"""
        mapping = {}
        # 获取当前脚本的绝对路径，确保能在任何地方运行
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.dict_path = os.path.join(script_dir, "asr_dict.txt")
        
        if not os.path.exists(self.dict_path):
            print(f"❌ 警告：找不到字典文件 {self.dict_path}，将使用空字典。")
            return mapping

        print(f"📂 正在加载字典: {self.dict_path} ...")
        with open(self.dict_path, 'r', encoding='utf-8') as f:
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
        return mapping

    def correct_text(self, text: str) -> str:
        """执行纠错核心逻辑"""
        corrected_text = text
        count = 0
        for error in self.sorted_keys:
            correct = self.error_mapping[error]
            # 使用正则忽略大小写替换
            pattern = re.compile(re.escape(error), re.IGNORECASE)
            
            if pattern.search(corrected_text):
                new_text, n = pattern.subn(correct, corrected_text)
                corrected_text = new_text
                count += n
        return corrected_text

    def process_file(self, input_path: str, output_path: str = None) -> None:
        """处理单个文件"""
        if not os.path.exists(input_path):
            print(f"❌ 错误：找不到文件 {input_path}")
            return

        if output_path is None:
            output_path = input_path

        print(f"🔄 正在处理: {os.path.basename(input_path)} ...")
        
        try:
            # 读取文件
            with open(input_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            
            # 执行纠错
            corrected_text = self.correct_text(raw_text)
            
            # 写入文件
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(corrected_text)
            
            print(f"✨ 完成！已保存至: {os.path.basename(output_path)}")
            
        except Exception as e:
            print(f"❌ 处理文件 {os.path.basename(input_path)} 时出错: {e}")

    def process_folder(self, folder_path: str) -> None:
        """批量处理文件夹内的所有 txt 和 srt 文件"""
        if not os.path.exists(folder_path):
            print(f"❌ 错误：找不到文件夹路径 {folder_path}")
            print(f"   请在代码顶部的 TARGET_FOLDER 处修改路径配置。")
            return

        print(f"\n📁 开始扫描文件夹: {folder_path}")
        
        processed_count = 0
        # 获取文件夹内所有文件
        try:
            files = os.listdir(folder_path)
        except Exception as e:
            print(f"❌ 无法读取文件夹: {e}")
            return

        for filename in files:
            # 检查文件后缀（不区分大小写）
            if filename.lower().endswith(('.txt', '.srt')):
                full_path = os.path.join(folder_path, filename)
                
                # 排除掉字典文件本身（防止误操作，虽然一般字典不放在这里）
                if os.path.abspath(full_path) == os.path.abspath(self.dict_path):
                    continue
                
                # 执行处理（output_path=full_path 表示直接覆盖原文件）
                self.process_file(full_path, output_path=full_path)
                processed_count += 1
        
        print("-" * 30)
        print(f"🎉 批量处理结束，共处理了 {processed_count} 个文件。")

if __name__ == "__main__":
    # 实例化
    corrector = FileBasedCorrector()
    
    # 批量处理
    print("-" * 30)
    # 直接使用顶部的全局配置变量
    corrector.process_folder(TARGET_FOLDER)
    print("-" * 30)