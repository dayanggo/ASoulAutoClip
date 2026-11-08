from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
if __package__ in (None, ""):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

from PIL import ImageFont
from pathlib import Path

def inspect_font(font_path):
    """检测字体文件的真实家族名称"""
    print(f"正在检测字体: {font_path}\n")
    
    try:
        # 尝试加载字体
        font = ImageFont.truetype(font_path, 50)
        print("✅ 字体文件可以被 PIL 正常加载")
        
        # 获取字体对象的属性
        if hasattr(font, 'font'):
            font_obj = font.font
            print(f"\n字体对象信息:")
            print(f"  - family: {getattr(font_obj, 'family', '未知')}")
            print(f"  - style: {getattr(font_obj, 'style', '未知')}")
        
        # 尝试使用 fontTools 获取详细信息
        try:
            from fontTools.ttLib import TTFont
            tt = TTFont(font_path)
            
            print("\n📋 字体名称表 (name table):")
            name_table = tt['name']
            
            # 重要的 nameID:
            # 1 = Font Family name (字体家族名)
            # 2 = Font Subfamily name (字体子族名)
            # 4 = Full font name (完整名称)
            # 6 = PostScript name (PostScript名称)
            
            important_ids = {1: "字体家族名", 2: "子族名", 4: "完整名称", 6: "PostScript名"}
            
            for record in name_table.names:
                if record.nameID in important_ids:
                    try:
                        name_str = record.toUnicode()
                        print(f"  [{important_ids[record.nameID]}] {name_str}")
                    except:
                        pass
            
            print("\n💡 FFmpeg ASS 字幕推荐配置:")
            # 获取主要的字体家族名
            for record in name_table.names:
                if record.nameID == 1:  # Font Family name
                    try:
                        family_name = record.toUnicode()
                        print(f"   font_family: \"{family_name}\"")
                        break
                    except:
                        pass
                        
        except ImportError:
            print("\n⚠️ 未安装 fontTools，无法获取详细字体信息")
            print("   安装方法: pip install fonttools")
        except Exception as e:
            print(f"\n⚠️ 读取字体详细信息时出错: {e}")
            
    except Exception as e:
        print(f"❌ 字体加载失败: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="查看本机字体文件的字体家族名称")
    parser.add_argument("font_file", type=Path)
    args = parser.parse_args()
    if not args.font_file.is_file():
        parser.error("字体文件不存在")
    inspect_font(str(args.font_file))
