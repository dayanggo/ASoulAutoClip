# 原版与可选工具

这里保留原项目的独立工作流。新用户先使用根目录 `run_clips.cmd`；以下工具不会自动纳入新主流程。

| 位置 | 用途 |
| --- | --- |
| `auto_clip.py` | 读取已准备好的 `Data_source.txt`、视频和 SRT，执行原版剪辑 |
| `danmu/analyze.py` | 按弹幕密度分析，独立配置输入和 API |
| `prompts/` | 原版手工提交给模型的成员提示词 |
| `tools/asr_corrector.py` | 原版文件夹字幕纠错 |
| `tools/shift_timestamps.py` | 原版时间轴偏移工具，会覆盖它所配置的数据文件 |
| `tools/download_recording.py` | 原版下载、字幕和弹幕工具，可选依赖 `yt_dlp` |
| `tools/download_danmaku.py` | 独立弹幕下载 |
| `tools/font_family.py` | 读取字体内部名称 |

从项目根目录运行模块，例如：

```bat
python -m legacy.tools.font_family "resources/fonts/your-font.otf"
python -m legacy.danmu.analyze
```

仅当需要原版下载工具时，安装可选依赖：`python -m pip install -r requirements/legacy.txt`。这不属于默认切片流程，也不会在测试中下载录播。手动剪辑数据格式可参考 [示例](examples/data_source.example.json)，复制为本机 `Data_source.txt` 后填写实际时间和内容。

原根目录 `Auto_clip.py` 保留转发，已有单条 `regen_clip.py` 仍能导入它。旧工具中的 `Data_source.txt` 是本机工作文件，不提交仓库；不要在代码中保存 API Key 或 Cookie 后上传。
