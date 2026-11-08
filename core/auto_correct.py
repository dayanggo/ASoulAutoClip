import os

def auto_correct_subtitles(input_dir):
    if not input_dir or not os.path.exists(input_dir):
        return
    try:
        from legacy.tools.asr_corrector import FileBasedCorrector
    except Exception as e:
        print(f"⚠️ 自动字幕纠错不可用: {e}")
        return
    print("🔧 开始自动纠错字幕...")
    corrector = FileBasedCorrector()
    corrector.process_folder(input_dir)
