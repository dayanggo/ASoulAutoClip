"""Display-aware video geometry and subtitle canvas selection."""
import json
import math
import subprocess
from pathlib import Path

from core.highlight_selection import SelectionError
from core.subtitle_style import subtitle_style


def stream_geometry(stream):
    coded_width,coded_height=stream["width"],stream["height"]
    if type(coded_width) is not int or type(coded_height) is not int or min(coded_width,coded_height)<=0:
        raise ValueError("视频分辨率无效")
    sar=stream.get("sample_aspect_ratio","1:1")
    try:
        numerator,denominator=map(int,sar.split(":"))
        pixel_ratio=numerator/denominator
        if pixel_ratio<=0:
            pixel_ratio=1
    except (ValueError,AttributeError,ZeroDivisionError):
        pixel_ratio=1
    angle=stream.get("tags",{}).get("rotate",0)
    for data in stream.get("side_data_list",[]):
        if "rotation" in data:
            angle=data["rotation"]
            break
    angle=float(angle)%360
    width,height=round(coded_width*pixel_ratio),coded_height
    if abs(angle-90)<.01 or abs(angle-270)<.01:
        width,height=height,width
    return {"width":width,"height":height,"coded_width":coded_width,"coded_height":coded_height,
            "rotation":angle,"sample_aspect_ratio":sar}


def probe_video(path):
    try:
        result=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(path)],
                              check=True,capture_output=True,text=True,encoding="utf-8",timeout=60)
        data=json.loads(result.stdout)
        duration_ms=round(float(data["format"]["duration"])*1000)
        stream=next(s for s in data["streams"] if s["codec_type"]=="video")
        if duration_ms<=0:
            raise ValueError("视频时长无效")
        return dict(stream_geometry(stream),duration_ms=duration_ms,
                    has_audio=any(s["codec_type"]=="audio" for s in data["streams"]))
    except FileNotFoundError:
        raise SelectionError("未找到 ffprobe，请将 FFmpeg 的 bin 目录加入 Path") from None
    except (subprocess.SubprocessError,ValueError,KeyError,StopIteration,TypeError):
        raise SelectionError(f"无法读取视频信息：{Path(path).name}") from None


def caption_line_capacity(style):
    size=style["font_size"]
    if type(size) not in (int,float) or not math.isfinite(size) or size<=0:
        raise SelectionError("subtitle_style.font_size 必须为正数")
    width,limit=(1080,14) if style["orientation"]=="vertical" else (1920,24)
    scale=1.12 if style["emphasis_mode"]=="cue" else 1
    available=max(4,min(limit,int((width-100)/(size*1.05*scale))))
    return min(available,style.get("max_phrase_chars",14)) if style.get("caption_mode")=="phrases" else available


def resolve_subtitle_style(overrides=None,media=None):
    style=subtitle_style(overrides)
    if style["orientation"] not in {"auto","horizontal","vertical"}:
        raise SelectionError("subtitle_style.orientation 支持 auto、horizontal 或 vertical")
    if style["orientation"]=="auto":
        # Standalone ASS utilities have no video; renderers always supply media.
        style["orientation"]="vertical" if media and media["height"]>media["width"] else "horizontal"
    if style.get("caption_mode")=="phrases":
        capacity=caption_line_capacity(style)
        if capacity<6:
            raise SelectionError("当前字号过大，无法在画面宽度内显示短句，请减小字号")
        style["max_phrase_chars"]=capacity
    return style
