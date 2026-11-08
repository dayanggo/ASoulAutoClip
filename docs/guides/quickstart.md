# 已有录播 → 有证据、经边界复核的切片

主要入口为 `run_clips.cmd` 或 `python -m core.cli`；`analyze_recording.py` 是兼容入口。支持“只有视频 + 本地转写 + 模型 API”，也能导入现有 SRT。

默认选片和标题封面面向不了解主播的普通观众。有独立XML/ASS弹幕时，可在原命令后追加 `--danmaku "弹幕路径"`，提示模型优先回看“绷”附近，并给通过质量门槛的候选加权排序。烧录在MP4画面里的弹幕尚不自动读取。配置、对齐和权重见[大众向与弹幕说明](audience-and-danmaku.md)。

需要录播姬录完后自动触发时，先完成本指南的模型环境，再按 [录播姬接入](recorder-integration.md) 配置 Webhook v2、原视频/XML 配对和串行队列。

## 0. 后续只需填三项，然后启动

源码只提供空白模板，不附带维护者的配置、密钥、模型或运行结果。首次安装先按 [README](../../README.md) 准备环境，再在项目根目录的 CMD 中创建本机文件：

```bat
if not exist config\local mkdir config\local
if not exist config\local\highlights.json copy config\examples\highlights.example.json config\local\highlights.json
if not exist .env copy .env.example .env
```

1. 编辑 `config/local/highlights.json`：`input.video` 填录播路径，`llm.model` 填你在硅基流动控制台选用的完整模型名。
2. 编辑项目根目录 `.env`：在 `SILICONFLOW_API_KEY=` 后填自己的 Key。
3. 双击项目根目录 `run_clips.cmd`，或在终端运行 `python -X utf8 analyze_recording.py`。

`llm.api_key_env` 保持为 `"SILICONFLOW_API_KEY"`：它填写的是环境变量名称，实际密钥只填入 `.env`，不要用密钥替换这个字段。

JSON 中的 Windows 路径建议使用 `/`，例如 `"video": "E:/录播/直播.mp4"`。`input.srt` 留空则自动转写，`input.output_dir` 留空则自动创建稳定的任务目录；相同视频重跑会复用有效缓存。配置中的相对路径以项目根目录为基准。

硅基流动地址已预填为 `https://api.siliconflow.cn/v1`，默认启用 JSON 输出。对照：[官方文本接口](https://docs.siliconflow.cn/docs/userguide/capabilities/text-generation)、[JSON 输出说明](https://docs.siliconflow.cn/docs/userguide/guides/json-mode)。没有默认替你选定付费模型，`enable_thinking: null` 表示不发送此可选参数。

`run_clips.cmd` 会创建/复用 `.venv`，缺少依赖时安装，随后检查配置并启动。需要 Python 3.10+、FFmpeg 和首次下载所需的网络。单独检查可运行 `python -X utf8 analyze_recording.py --check`，不会下载模型或调用 API。

## 1. 先准备一段真实样本

完整运行后，每个 `clips/<片段ID>/` 目录还会导出默认 `cover.jpg`、`cover_01.jpg` 至 `cover_03.jpg` 候选封面，以及标题、简介、标签、`投稿文案.txt` 和 `metadata.json`。封面使用原视频截图与文字排版：横版为 1280×720，竖版为 720×1280；默认竖版构图保留完整画面。简介草稿使用已校验的片段原话摘录和录播出处，不根据字幕补写画面事实。原录播自带的弹幕会保留在截图中。

默认自动使用本机中文字体。可通过配置的 `publication` 段调整 `cover_count`（1–5）、`font_path`（本地中文字体文件）、`tags` 和 `source_url`（真实原录播链接，未提供时留空）。此流程导出投稿素材，尚未自动上传到 B 站。

已有成片只补齐封面和文案，可运行 `run_clips.cmd --package-only`；该模式会核对录播与已有分析是否匹配，不转写、不请求 API、不编码视频。报告页包含封面、简介和文件链接，输出根目录的 `posting_manifest.json` 提供本次素材清单。

首次用一段 10–20 分钟、你熟悉内容的杂谈录播，检查转写和选片后再处理整场。保留一份手工标记：哪些地方值得剪、必要前因在哪里、反应何时结束。

这版优先处理对白笑点、互动、故事和观点。纯动作、舞蹈和完整歌曲还需要后续增加画面或专用分析路径。

时长不合规的候选会收到包含实际秒数的修正反馈。若某窗口的全部候选在限定重试后仍仅因时长不合规而无法使用，程序会记录弃选并缓存该结果，继续导出其他通过证据与边界复核的片段。不会放宽时长限制或硬截断事件；证据不符、格式错误及 API 故障仍按失败处理。

## 2. 安装本地转写依赖

在项目根目录的 PowerShell 中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/asr.txt
ffmpeg -version
ffprobe -version
```

本地 ASR 使用 faster-whisper，默认 `turbo`，设备和计算精度为 `auto`。自动模式先在独立进程中尝试 CUDA / `int8_float16`，失败后尝试 CPU / `int8`，实际使用设备记录在转写回执中。首次运行会下载模型。GPU 需要匹配的 CUDA/cuDNN 运行库；按 [faster-whisper 官方要求](https://github.com/SYSTRAN/faster-whisper#gpu) 配置。

依赖已固定为 faster-whisper 1.2.1 和 PyAV `<19`，避免已验证的 `metadata_errors` 解码不兼容。原因见 [上游问题记录](https://github.com/SYSTRAN/faster-whisper/issues/1589)。启动脚本会检查并修复这个版本组合。

本机验证中 Xet 权重下载停滞，普通 HTTP 下载可继续，因此转写子进程默认设置 `HF_HUB_DISABLE_XET=1`。如果希望使用 Xet，可在系统/会话环境中显式设置为 `0`。该参数见 [Hugging Face 官方说明](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables#hfhubdisablexet)。也可将 `asr.model` 指向已经下载好的 CTranslate2 模型目录。

可以将 ASR 显式改为 `"device": "cpu", "compute_type": "int8"`；显式选择 `cuda` 时则不自动切换设备。CPU 可能明显更慢，速度需实测。GPU 子进程失败日志保留在 `asr/<指纹>/asr-cuda-error.log`。

## 3. 复制配置并单独跑转写

```powershell
if (-not (Test-Path config/local/highlights.json)) { Copy-Item config/examples/highlights.example.json config/local/highlights.json }
.\.venv\Scripts\python.exe -X utf8 analyze_recording.py --video "E:\录播\sample.mp4" --config config/local/highlights.json --output outputs/sample --prepare-only
```

把示例视频路径替换成真实路径。`--prepare-only` 不需要填写模型 API，不调用选片接口。

完成后会得到：

- `asr/<指纹>/transcript.srt`：本地转写字幕。
- `asr/<指纹>/transcript.words.json`：保留词级识别时间，第一版选片使用字幕句级时间。
- `transcript.indexed.json`：稳定字幕 ID、毫秒时间和原文。
- `windows.json`：准备送入模型的重叠字幕窗口。

先试听开头、中间、结尾几处，确认字幕对齐、人名和关键笑点没有大面积识别错误。VAD 只参与转写，成片不会因此自动删除所有停顿。

转写完成后会复用整次缓存。FireRed新运行还会逐段保存严格校验后的识别结果，中途退出后可按同一配置和输出目录复用有效分段；未落盘的旧运行分段不能恢复。Whisper仍按整次转写缓存。详见[FireRed续跑与局部恢复](firered.md#分段续跑与对齐异常)。

若手工修正了字幕，请另存为一份 SRT，再通过 `--srt "修正后的字幕.srt"` 导入，保持原始转写和词时间可追溯。不要直接修改 ASR 缓存文件后期待缓存校验仍然通过。

## 4. 填写已有 API 的地址和模型

编辑 `config/local/highlights.json` 的 `llm` 段：

```json
{
  "base_url": "https://api.siliconflow.cn/v1",
  "model": "你的模型标识",
  "api_key_env": "SILICONFLOW_API_KEY",
  "json_mode": true,
  "enable_thinking": null,
  "max_tokens": 4096,
  "timeout_seconds": 180,
  "attempts": 2
}
```

`base_url` 填到 `/v1` 或厂商对应的版本前缀，程序在后面添加 `/chat/completions`，不要再填完整请求路径。此适配器要求服务支持相应消息接口；原生协议不同的服务需要另加适配器。

默认从项目根目录 `.env` 读取密钥，已有系统/会话环境变量优先。也可以只为当前 PowerShell 会话输入：

```powershell
$clipApiSecret = Read-Host "输入已有 API Key" -AsSecureString
$env:SILICONFLOW_API_KEY = [System.Net.NetworkCredential]::new('', $clipApiSecret).Password
Remove-Variable clipApiSecret
```

`.env` 和本地配置已加入 Git 忽略。无需将密钥粘进聊天或 JSON。若换用其他服务，再根据该服务是否支持 JSON 输出调整 `json_mode`。

## 5. 先分析报告，再生成视频

```powershell
.\.venv\Scripts\python.exe -X utf8 analyze_recording.py --video "E:\录播\sample.mp4" --config config/local/highlights.json --output outputs/sample --analyze-only
```

处理顺序为：

1. 复用已有转写或导入 SRT；给字幕分配 ID，再用 `resources/dictionaries/asr_dict.txt` 纠错，保留原文和修改明细。
2. 分窗找候选，模型返回 ID 和原文证据。
3. 程序检查 ID、引文、证据角色和时长；格式/证据校验失败会带上原因请求修正，默认最多两次，仍不合格才记录失败。
4. 初选看点分低于 `selection.min_score` 时正常弃选，不请求模型抬分。默认门槛为 85 分。
5. 对通过门槛的候选查看前后上下文，复核必要前因和完整结尾，同时独立评价修正后区间的具体看点及拖沓程度。最终分取初选和复核的较低值，都达标才出片。
6. 证据不能被边界修正删掉；通过后去重、排序。`max_clips` 是上限，允许没有合格片段，不按数量补齐。
7. 输出 `analysis.json`、`Data_source.txt` 和 `report.html`。报告展示门槛、评分和弃选原因。

想减少平淡切片，在原启动命令后追加 `--min-score 90`；先加 `--analyze-only` 看筛选结果，再去掉该参数生成成片。`--min-score` 只改变本次运行，不回写配置。旧成片目录不会自动清除，应按最新 `report.html` 和 `posting_manifest.json` 查看本次通过的结果。改质量规则后首次重跑会重新请求选片和复核，已有有效 ASR 可复用；同一版本规则下仅调整门槛，可复用有效请求缓存。详见 [看点门槛说明](quality-gate.md)。

双击输出目录中的 `open_preview.cmd`，用本机预览服务回看原录播对应区间及原文证据。也可直接打开 `report.html`；如果浏览器限制本地媒体或一直显示0:00，按 [报告预览说明](report-preview.md) 启动 HTTP 预览，不需要重跑模型。

报告中的“语义复核”表示模型基于字幕检查过，尚不等于已人工确认、已核对画面、或已保证所有尾音完整。FireRed 现已使用校验过的 CTC 对齐生成词时间；切片边界仍由字幕及语义判断，词典改字后也不会伪装成已对新文字重新对齐。旧估计时间的修复和当前 7 条审查见 [准确性审查记录](../history/current-clip-accuracy-review.md)。

词典修正发生在内容分析之前，因此选片、标题、封面和烧录字幕使用同一版本文本。打开 `corrections.html` 检查误改；修改词典后无需重新转写。已出片任务需要直接重跑完整流程，`--restyle` / `--package-only` 不会重新判断选片。具体规则、缓存和忽略列表见 [字幕词典纠错说明](subtitle-correction.md)。

确认初步结果后，使用同一配置和输出目录，去掉 `--analyze-only`：

```powershell
.\.venv\Scripts\python.exe -X utf8 analyze_recording.py --video "E:\录播\sample.mp4" --config config/local/highlights.json --output outputs/sample
```

程序会复用有效分析缓存，生成 `clips/<片段ID>/clip.mp4`、`captions.generated.srt` 和 `captions.generated.ass`，报告改为播放切片。

视频先写临时目录、检查时长后才替换对应旧视频。每次编码失败会保留旧视频，并记录 `render-error.log`；整批不是原子回滚。自动字幕使用 `.generated` 文件名，手动版本请另存。

如果已有修正后的字幕，两次分析和渲染命令都加上相同 `--srt` 参数。不要在第二次运行中不经意切回原始 ASR 字幕。

## 6. 如何调整第一批结果

默认在生成成片前增加投稿编辑阶段：用现有 API 生成投稿标题候选、封面文案、取帧句和重点字幕；封面区分连续原话 `quote` 与附证据的编辑概括 `caption`。先生成完整 `cover_text` 再按语义分行，默认追加一次独立文案审校，检查通顺性、具体对象和事实。每条切片的投稿文案通常需要两次请求（初稿、审校），两个阶段各最多尝试两次，网络重试另计；已有有效缓存或本地复核会跳过相应请求。审校失败保留有效初稿，重跑只补审校，不重新转写或选片。`publication.copy_review_enabled=false` 可关闭额外模型审校，基础的引文与分行完整性检查继续保留。设置 `publication.editorial_enabled=false` 可关闭整个投稿编辑阶段。选片标签保留在分析里，投稿标题用于报告和投稿清单。详见 [封面文案完整性修复](cover-copy.md)。

已有结果可运行 `.\run_clips.cmd --restyle`，复用字幕和选片，仅更新标题、封面和重点字幕；字幕呈现变化时重新编码，单改标题或封面会复用视频。只补封面和文案仍用 `.\run_clips.cmd --package-only`，此命令不调用 API、不编码。

当前封面默认为 `publication.cover_style=event_yellow`：真实场景、优先两行的大号黄字与粗黑描边，一张图讲一件事；`live_reaction` 和 `quote_poster` 保留可选。字幕默认青年体、84号统一黄字（`emphasis_mode=none`），按可验证词时间分成短句。位置为底部居中、`margin_v=50`。主流程描边4、阴影1、加粗；旧版独立入口保留其描边和阴影默认值。字体文件不随源码提供，见 [字体安装说明](../../resources/fonts/README.md)。

字幕默认 `render.subtitle_style.orientation=auto`，按源视频显示方向选择画布，并读取 90°/270° 旋转信息。横版使用 1920×1080，竖版使用 1080×1920；视频不强制改成横版尺寸。84 号竖版短句有效上限自动缩到约 11 字，继续用真实词时间分段，不用横版行宽挤成两行；没有有效词时间时仍保留原始字幕时间并允许换行。编码后同时检查显示尺寸和时长，防止错误结果替换旧成片。详见 [竖版修复说明](orientation.md)。

`caption_mode=phrases` 使用已有词时间和本地中文分词，默认每条最多 14 字；没有可验证词时间时保留原字幕时间，不平均分配时间。`captions.layout.json` 记录显示计划、来源字幕 ID、词时间来源及回退情况。原始字幕和选片证据不回写。`core/subtitle_style.py` 集中定义默认字体、字号、颜色和短句参数；录播入口、旧版 `Auto_clip.py`、已有 ASS 重排以及生成的单条重剪脚本均使用这套默认值。当前风格研究见 [枝江艾池繁同学风格改进](../research/aichifan-style-study.md)；[自然醒醒风格记录](../research/xingxing-style-study.md) 与 [绘海风格记录](../research/huihai-style-study.md) 保留用于对照。

只调整已有成片的字幕，可运行以下命令。CMD 和 PowerShell 都可使用，不转写、不请求大模型 API，也不改标题封面：

```cmd
run_clips.cmd --output "outputs\sample" --captions-only
```

此模式从任务的 `analysis.json` 获取原录播路径，需保留原录播、字幕索引和 ASR 缓存。词级时间优先取 `correction.raw_srt` 同目录的 `transcript.words.json`，校验对应录播的来源；旧 FireRed 非严格对齐数据不用于分句。显式字幕纠错通过原词范围映射，不伪造纠正后每个字的发声时刻。

字幕保持底部原位置，即使原画面底部有卡片也不会自动移动。旧入口可以从原始 SRT 同目录或任务索引定位词时间，已有 ASS 的百分之一秒精度会匹配回原始词范围；如果只剩 SRT/ASS、没有词时间缓存，则保留原时间并记录回退。短句排版使用本地 jieba，无需额外大模型；依赖由 `requirements/base.txt` 管理，安装ASR依赖时也会安装。

更换视频字幕字体，修改实际使用配置中的 `render.subtitle_style.font_family`，填本机已安装字体的完整名称而非文件路径。默认名称为 `文悦新青年体 (须授权) W8-J`，字体文件不随仓库提供。使用独立配置时继续指定同一 `--config`；修改后用 `run_clips.cmd --config config/local/highlights.json --output "任务目录" --captions-only` 更新已有成片。字幕已烧录进视频，字体变化需要重新编码；有效转写与选片可复用。封面的字体由 `publication.font_path` 单独控制。

“冉总 → 然总”已加入 `resources/dictionaries/asr_dict.txt`，之后完整流程会在选片前统一纠正。已有成片若只需应用用户明确指定的字幕改字，可配置 `render.subtitle_style.text_replacements={"冉总":"然总"}`，随后 `--restyle`。它只修改生成的 ASS/SRT 和烧录文字，不回写原始转写或擅自更新选片证据；每条的 `captions.corrections.json` 保留前后文本、字幕 ID 和原时间。替换按最长匹配单次执行，不会级联改写。大范围或影响事件含义的纠错仍应重跑完整选片流程。

要从同一录播继续找新增片段，可在单独批次配置中设置 `selection.exclude_ranges_ms`，例如 `[[600000, 675000], [1100000, 1190000]]`。单位为原视频毫秒；程序会从选片窗口排除这些语音，不拼接排除区间两侧的字幕，并检查边界复核后是否重新进入旧区间。它保证时间范围不重复，同一话题的不同子事件仍需编辑复核。请同时指定新的 `input.output_dir` 保留旧批次；复用现有原始 SRT 可免去再次转写。

| 现象 | 优先检查 |
| --- | --- |
| 有笑声却没有前因 | 看触发点和结果证据是否来自同一个事件；检查边界复核提示词 |
| 好片段被窗口截开 | 重叠默认 180 秒；提高上下文预算，或调整窗口/重叠；字符上限也会缩短实际窗口 |
| 候选偏少 | 先看 `rejected` 和 `failures`；没有合格事件允许输出零片段，不要仅降低质量要求 |
| 标题、名字编造 | 修正字幕和 `selection.brief`，明确哪些成员出场；加入正负例 |
| 故事太长被拒绝 | 增加 `max_clip_seconds`，同时增大窗口；程序不会直接截断事件 |
| API 失败 | 查看 HTTP 状态和配置，修复后重跑；已完成窗口和转写可复用 |
| 字幕起止不准 | 核对原始 SRT；当前小幅前后留白不能代替词级强制对齐 |

默认最大片长 180 秒、最多 8 条、每窗口最多 4 个候选，均为待校准起点。默认一轮窗口选片加每个候选一轮边界复核；实际 API 次数随窗口和候选数量增加，网络重试和格式修正也会计入服务用量。`selection.validation_attempts` 控制格式/证据修正次数，`llm.attempts` 控制网络及限流重试次数。

默认软件编码便于先验证。确定 NVENC 可用后可将 `render.encoder` 改为 `h264_nvenc`。失败不会自动改变编码参数，请修复后重跑，避免未察觉的结果变化。

## 7. 退出状态与兼容入口

- 退出码 `0`：流程完整执行，允许没有合格片段。
- 退出码 `1`：配置、素材或本地转写等前置条件失败。
- 退出码 `2`：分析窗口/边界调用或渲染存在失败；应重跑，不能当作全部成功。

分析存在失败时暂不编码。报告保留失败原因；缓存包含字幕和模型结果，应按素材文件一样管理。ASR 缓存按视频路径、大小、修改时间及转写配置区分；分析缓存按字幕内容、提示词及模型配置区分，修改提示词后会重新分析对应阶段。同一输出目录有运行锁，重复启动会退出；`job.json` 保存当前阶段和最终状态。

输出的 `Data_source.txt` 为旧格式兼容文件，包含 `boundary_locked=true`，旧渲染入口已支持此标记以避免再次补句。但第一版推荐直接使用新入口，其编码失败保护与报告更完整；不要覆盖项目根目录的原有元数据再批量重跑。

自动化测试覆盖字幕时间、证据校验、缓存复用、API 错误、真实 FFmpeg 出片、封面导出和失败后保留旧文件。另已在本机完成真实录播的 FireRed GPU 转写、硅基流动请求及切片输出验证；这不能证明每句字幕和每条选片都准确。实际质量以任务报告和素材复核为准；风格改进的样本范围与人工参与部分见上面的调研记录。
