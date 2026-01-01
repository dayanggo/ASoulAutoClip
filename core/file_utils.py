import re

VIDEO_SUFFIXES = {'.mp4', '.mkv', '.flv', '.mov', '.ts'}
IMAGE_SUFFIXES = {'.jpg', '.png', '.jpeg'}

def sanitize_filename(name):
    safe = re.sub(r'[\\/:*?"<>|]', '_', str(name)).strip()
    return safe or "clip"

def clean_output_dir(output_dir, include_images=True):
    if not output_dir.exists():
        return
    for file_path in output_dir.iterdir():
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        if suffix in VIDEO_SUFFIXES or (include_images and suffix in IMAGE_SUFFIXES):
            try:
                file_path.unlink()
            except Exception as e:
                print(f"⚠️ 无法删除文件 {file_path.name}: {e}")
