import json
from pathlib import Path

SOURCE_META_FILENAME = "_source_meta.json"

def write_source_meta(output_dir, source_video, srt_file):
    try:
        meta_path = Path(output_dir) / SOURCE_META_FILENAME
        meta = {
            "source_video": source_video,
            "srt_file": srt_file
        }
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 无法写入源信息: {e}")

def load_source_meta(start_dir):
    current = Path(start_dir).resolve()
    while True:
        meta_path = current / SOURCE_META_FILENAME
        if meta_path.exists():
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return None
        if current.parent == current:
            break
        current = current.parent
    return None
