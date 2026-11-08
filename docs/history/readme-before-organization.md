> 历史归档：目录整理前的 README，包含旧命令与尚未验证的效果宣传；当前操作请阅读根目录 README。

# 已有录播自动选片入口

新增 `analyze_recording.py`：本地转写 → 字幕 ID 与原文证据选片 → 上下文边界复核 → 字幕切片和可回看报告。

支持已有录播视频，不提供 SRT 时使用本地 faster-whisper，自动尝试 GPU 并可回退 CPU；选片已适配硅基流动 API。录播路径、模型名和 Key 留空，后续填写即可启动。

1. 在 `config/highlights.local.json` 填 `input.video` 和 `llm.model`；接口地址已预填。
2. 在项目根目录 `.env` 填 `SILICONFLOW_API_KEY`。
3. 双击 [run_clips.cmd](../../run_clips.cmd)，自动检查依赖并运行。

完整流程还会为每个切片生成 `cover.jpg`、3 张候选封面、`title.txt`、`description.txt`、`tags.txt`、`投稿文案.txt` 和 `metadata.json`。在报告页可预览封面及简介，`posting_manifest.json` 汇总本次片段的投稿信息。仅为已有成片补齐封面及文案可运行 `run_clips.cmd --package-only`，不会转写、请求大模型或重新编码视频。

封面先写完整文案再分行，默认追加一次独立的文案 API 审校，检查通顺性、指代和事实；例如“像什么／确实也像”即使逐字来自字幕，也会因没有具体对象被拦截。审校与初稿分别缓存，失败可继续；有效本地复核不调用 API。额外审校可通过 `publication.copy_review_enabled=false` 关闭。实现与当前 ASMR 批次修复见 [封面文案完整性说明](../guides/cover-copy.md)。

多人电台使用完整对话与保守归属规则；录播背景会同时传入选片、边界复核和投稿文案，成员名单不会冒充逐句说话人标签。本场三人电台专用配置及后续说话人分离方案见 [三人电台准确性说明](../guides/multi-speaker.md)。当前仍未接入音频说话人分离。

选片默认采用 **85 分看点门槛**（`selection.min_score`）。初选低分直接弃选，边界复核同时独立评价具体看点和拖沓程度，最终分取两轮较低值；普通闲聊、日常操作和完整但平淡的问答不会仅凭事件完整就通过。`max_clips` 只是数量上限，允许产出 0 条。可在原启动命令追加 `--min-score 90` 提高要求，配合 `--analyze-only` 先看报告。评分标准尚未经过人工素材校准，不代表观众满意率。详见 [看点门槛与弃选规则](../guides/quality-gate.md)。

视频字幕默认自动读取源视频方向：横版使用横屏画布，竖版使用竖屏画布，并按画面宽度缩短字幕卡片；字体、84 号黄字和底部位置继续沿用。竖版封面为 720×1280，报告按原比例显示。处理本场已有 8 条与当前未完成选片的区别见 [竖版修复说明](../guides/orientation.md)。

投稿编辑会单独生成标题候选、封面文案和重点台词；文案区分真实原话与带证据的编辑概括。默认封面 `event_yellow` 使用真实场景、两行大号黄字和粗黑描边，标题优先讲清一件具体事情和真实回应。支持经看图标注的人物放大与左右构图；旧的 `live_reaction` 和 `quote_poster` 仍可配置使用。所有切片入口默认使用青年体、84 号统一黄字，并根据真实词时间把长句分段显示；字幕保持原来的底部位置、原有描边和加粗，不自动上移或换色。公共默认值在 `core/subtitle_style.py`。只更新已有成片字幕使用 `run_clips.cmd --output "任务目录" --captions-only`，不转写、不调用 API；没有词时间缓存时保留原字幕时间。已有结果运行 `run_clips.cmd --restyle` 可复用字幕与选片更新标题、封面等；该模式可能调用 API，字幕呈现改变时重新编码。当前参考样本、改进内容与限制见 [枝江艾池繁同学风格研究](../research/aichifan-style-study.md)，此前的 [自然醒醒风格记录](../research/xingxing-style-study.md) 与 [绘海风格记录](../research/huihai-style-study.md) 保留用于对照。

详细说明见 [首次运行步骤](../guides/quickstart.md)，可提交的配置模板为 [highlights.example.json](../../config/examples/highlights.example.json)。Windows 路径在 JSON 中建议写成 `E:/录播/sample.mp4`。

```powershell
python -X utf8 analyze_recording.py
```

首次建议先加 `--prepare-only` 检查转写，再用 `--analyze-only` 检查证据和边界。真实选片质量需要用你的录播验证。

支持可选的 **FireRedASR2-AED** 本地转写后端：运行 `setup_firered.cmd` 安装独立环境，再将 `asr.backend` 设为 `firered`、`asr.model` 设为 `models/FireRedASR2-AED`。后续仍使用 `run_clips.cmd` 启动。详见 [FireRed 使用说明](../guides/firered.md)。

选片前默认使用 `utils/asr_dict.txt` 修正已知错词，保留原始 ASR 和字幕时间，生成 `corrections.html` 前后对照。修改词典后直接重跑即可，无需删除 ASR 缓存；依赖语境的规则可用 `correction.ignore_sources` 关闭。见 [字幕词典纠错说明](../guides/subtitle-correction.md)。

检查已有结果可运行 `.\.venv\Scripts\python.exe -X utf8 audit_clips.py`，生成 `audit/report.html`，核对证据、字幕版本、成片区间和片前片后上下文，不调用 API 或重新编码。当前 7 条的具体问题与 CTC 对齐修复见 [准确性审查记录](current-clip-accuracy-review.md)。

# 2026.1.1更新（V2.0）

- `Auto_clip.py` 启动时会自动纠正字幕（调用 `utils/ASRCorrector.py`）并自动识别输入目录中的视频与 SRT
- **输出目录改为：`workspace/clip_output/<输入文件夹名>/<序号_片段标题>/`，每个片段独立子文件夹**
- **输出每个片段文件夹里面都会生成 `regen_clip.py`，可独立重新生成该片段，从而实现单独编辑某个视频**
- **`Auto_clip.py` 默认只生成视频与字幕，封面在运行 `regen_clip.py` 时生成**
- `regen_clip.py` 顶部配置可单独调整字幕/封面，并用 `REBUILD_ASS` 控制是否重建 `.ass`
- `regen_clip.py` 的默认配置来自生成当时的 `Auto_clip.py` 快照，修改配置后需重新运行 `Auto_clip.py` 生成新的脚本

---

# 🎞️ A-SOUL 自动化切片工具 (Asoul-Auto-Clip)

![FFmpeg](https://img.shields.io/badge/Dependency-FFmpeg-green)
![License](https://img.shields.io/badge/license-MIT-orange)
![Status](https://img.shields.io/badge/Status-Stable-brightgreen)

> **极速切片，解放双手！专为 A-SOUL 切片设计的自动化生产线。**
> 
> 仅需 **5-10分钟** 配置，即可从两小时的直播中自动提取 **10-20个** 精彩片段。自动对齐时间轴、生成多风格封面、内嵌字幕及标题，直接生成可投稿视频！

## ✨ 核心功能

*   **⚡ 极速处理**：相比手动切片（耗时约2小时），本工具可节省 **90%** 以上的时间。
*   **🧠 双重策略**：
    *   **LLM 语义分析**：利用 GPT-5/Claude/Gemini/豆包 分析字幕，精准提取对话梗。
    *   **弹幕热度分析**：基于观众真实反应（"绷"、"?"、"哈哈哈"）定位高光时刻。
*   **🎨 全自动包装**：自动剪辑视频、嵌入字幕、生成标题、生成多种风格的封面图。
*   **🛠️ 智能辅助**：内置 ASR 文本纠错字典、官方/非官方录播时间轴对齐工具。

---

## 📦 安装与环境配置

在运行之前，请确保你的环境满足以下要求。

### 1. 系统依赖 (必须安装)
本项目依赖 **FFmpeg** 进行视频处理，请务必安装并添加到环境变量。

*   **Windows**: [下载 FFmpeg](https://ffmpeg.org/download.html) -> 解压 -> 将 `bin` 目录添加到系统环境变量 `Path` 中，详细教程参考：[FFmpeg 超级详细安装与配置教程（Windows 系统）](https://blog.csdn.net/Natsuago/article/details/143231558)。
*   **Mac**: `brew install ffmpeg`
*   **Linux**: `sudo apt install ffmpeg`

验证安装：在终端输入 `ffmpeg -version` 不报错即为成功。

### 2. Python 依赖
```bash
git clone https://github.com/dayanggo/ASoulAutoClip.git
cd ASoulAutoClip
pip install -r requirements.txt
```

### 3. 创建目录

- 在使用该项目之前，需要在项目根目录下创建 `workspace` 目录，然后在`workspace`目录下面创建`video_input`目录和`clip_output`目录。
- 在项目的`assets`目录下，创建`font`目录，存放制作视频使用的字体。

### 4. 字体配置
项目默认使用**自定义字体**生成封面和字幕。
*   将你喜欢的字体文件（`.ttf`, `.otf`）放入 `assets/font/` 目录，并在自己电脑上安装该字体。
*   修改 `Auto_clip.py` (主程序) 中的 `font_path` 为你的字体名称，`font_family`为字体家族名称（可以通过 `utils/get_font_family_name.py` 获取）。

字体推荐安装：
- [文悦新青年体](https://www.fonts.net.cn/font-32331338415.html)
- [上首状元体](https://www.fonts.net.cn/font-40031585739.html)
- [自由体](https://www.fonts.net.cn/font-31793233330.html)

---

## 📂 项目结构

```text
ASoulAutoClip/
├── assets/                       # 静态资源 (字体、图片素材)
├── danmu_method/                 # [核心] 弹幕分析算法模块
├── prompt_method/                # [核心] LLM 提示词模板
├── utils/                        # 工具箱
│   ├── ASRCorrector.py           # 字幕纠错工具
│   ├── get_danmu.py              # 弹幕下载器
│   ├── edit_data_source.py       # 时间轴对齐工具
│   ├── get_font_family_name.py   # 获取字体家族名称，用于填入主程序
|   └── asr_dict.txt              # 字幕纠错字典
├── workspace/                    # 工作区
│   ├── clip_output/              # 剪辑片段输出
|   └── video_input/              # 放入视频、字幕、弹幕文件等素材
├── Auto_clip.py                  # 🚀 主程序入口
├── Data_source.txt               # [数据] 剪辑片段元数据
├── requirements.txt              # Python 依赖
└── README.md                     # 说明文档
```

---

## 🚀 使用指南 (Workflow)

### 第一步：素材准备

运行工具前，请确保以下文件已就位：
1.  **录播视频文件**（完整视频）
2.  **字幕文件**（`.srt` 或 `.txt`）
3.  **元数据文件**（`Data_source.txt`）

#### 1. 视频下载
推荐使用以下工具获取 Bilibili 录播视频：
- [SnapAny](https://snapany.com/zh/bilibili) / [效率坊](https://www.xiaolvfang.com/) / [ShowBL](https://www.showbl.com/lab/bilibili/) / [kedou](https://www.kedou.life/extract/bilibili)
- 或者使用 [B站录播姬](https://github.com/Bililive/BililiveRecorder) 自行录制。

#### 2. 字幕下载与处理
推荐使用 **vCaptions** 插件下载字幕：
- [Edge 插件下载](https://microsoftedge.microsoft.com/addons/detail/vcaptions-%E7%BB%99%E4%BB%BB%E6%84%8F%E7%BD%91%E7%AB%99%E8%A7%86%E9%A2%91%E6%B7%BB%E5%8A%A0%E5%AD%97%E5%B9%95%E5%88%97%E8%A1%A8/lignnlhlpiefmcjkdkmfjdckhlaiajan?hl=zh-CN)
- [Chrome 插件下载](https://chromewebstore.google.com/detail/vcaptions-%E7%BB%99%E4%BB%BB%E6%84%8F%E7%BD%91%E7%AB%99%E8%A7%86%E9%A2%91%E6%B7%BB%E5%8A%A0%E5%AD%97%E5%B9%95%E5%88%97%E8%A1%A8/bciglihaegkdhoogebcdblfhppoilclp?hl=zh-CN&utm_source=ext_sidebar)

或者使用在线提取：[飞鱼多字幕工具](https://www.feiyudo.com/caption/subtitle/bilibili)

> **⚠️ 注意**：获取字幕后，请务必运行 `utils/ASRCorrector.py` 对字幕进行自动纠错，以提高识别准确率。

---

### 第二步：生成高光片段元数据 (核心)

你需要将高光时刻的元数据填入 `Data_source.txt`。本仓库提供两种自动化生成方法：

#### 📊 方法对比

| 特性 | 方法一：LLM 提示词法 | 方法二：弹幕热度法 |
| :--- | :--- | :--- |
| **原理** | 依靠直播字幕 + 大模型语义理解 | 依靠弹幕密度波峰 + 字幕辅助 |
| **优点** | **无需接口**，可利用 Gemini/GPT 高级推理能力；<br>适用范围广（任意视频）。 | **客观真实**，反映观众实时反应；<br>全自动化，无需手动提问。 |
| **缺点** | 依赖字幕质量；无法识别纯画面/动作梗。 | 必须有大量弹幕（仅限官方录播）；<br>需要调用 LLM API (消耗 Token)。 |
| **适用场景**| 字幕清晰、对话为主的直播 | 官方大型直播、弹幕互动多的场次 |

#### 📥 `Data_source.txt` 数据格式示例
无论使用哪种方法，最终填入的数据格式必须如下：

```json
[
  {
    "timestamp": "00:49:52-00:50:16",
    "title": "破防了！嘉然自曝演唱会蹦迪因身高太矮被淹没",
    "summary": "嘉然分享独自看演唱会的尴尬经历...",
    "cover_text_1": "站起也被淹没",
    "cover_text_2": "警告一次",
    "highlight_reason": "嘉然自嘲身高梗，节目效果强烈。"
  }
]
```

#### 👉 操作流程

**方法一：LLM 提示词法 (prompt_method)**
1.  找到 `prompt_method` 文件夹。
2.  将**纠错后的字幕文件**作为附件，配合对应的 Prompt 发送给高级大模型（推荐 GPT-5, Claude 4.5, Gemini 3.0 Pro, 豆包等）。
3.  将模型生成的 JSON 数据复制到 `Data_source.txt`。

**方法二：弹幕热度法 (danmu_method)**
1.  获取直播弹幕：
    *   运行 `utils/get_danmu.py` 下载。
    *   或者使用其他方法，用第三方工具转换弹幕格式（如 XML 格式需转换为 ASS 格式，[转换工具](https://tiansh.github.io/us-danmaku/bilibili/)）。
2.  将 `直播弹幕.ass` 和 `字幕.srt` 放入视频同级目录。
3.  配置并运行 `danmu_method/get_data_by_danmu.py`，程序将自动填充 `Data_source.txt`。

 
> **💡 进阶技巧：官方弹幕 + 非官方视频**
> 如果你想利用官方录播的弹幕热度数据，来剪辑非官方录播的视频（通常存在时间偏差），请在生成数据后额外执行一步：
>
> 运行脚本：`utils/edit_data_source_timestep.py`
>
> **作用**：该脚本会自动修正 `Data_source.txt` 中的时间戳，将其从官方录播时间对齐到你下载的非官方视频时间轴。

---

### 第三步：运行主程序

1.  打开 `Auto_clip.py`，根据注释填写相关文件路径配置。
2.  运行程序。
3.  等待输出文件夹中生成切片视频、切片封面 (10个切片大约耗时2分钟)。

> **提示**：如果对结果不满意，可手动微调 `Data_source.txt` 中的标题或封面内容，再次运行脚本即可。
另外，如果视频中有字幕错误，可以手动在输出文件夹中的 `[相应切片标题].ass` 文件中修改字幕，再次运行脚本即可。

---

## 🔗 传送门 (Resources)

为了方便获取素材，这里整理了常用链接：

### 官方录播源
*   [嘉然 (Diana)](https://space.bilibili.com/672328094/lists/222940?type=series)
*   [贝拉 (Bella)](https://space.bilibili.com/672353429/lists/222938?type=series)
*   [乃琳 (Eileen)](https://space.bilibili.com/672342685/lists/222754?type=series)
*   [心宜 (fiona)](https://space.bilibili.com/3537115310721181/lists/3698069?type=series)
*   [思诺 (gladys)](https://space.bilibili.com/3537115310721781/lists/3692011?type=series)

### 非官方录播
*   [A-SOUL二创计画](https://space.bilibili.com/547510303/upload/video)
*   [奶淇琳周报](https://space.bilibili.com/1729236416/upload/video) | [贝极星周报](https://space.bilibili.com/2114847153/upload/video) | [嘉心糖周报](https://space.bilibili.com/247210788/upload/video)
*   [地海菠萝](https://space.bilibili.com/165364/upload/video) | [天神白娅](https://space.bilibili.com/12416994/upload/video)

---


## ⚠️ 免责声明 (Disclaimer)

1.  本项目为粉丝自制开源工具，**非 A-SOUL 官方项目**。
2.  生成的视频素材版权归 **A-SOUL** 所有。
3.  请在使用本工具进行二创投稿时，遵守 Bilibili 社区规范及 A-SOUL 二创公约。
4.  本项目禁止用于任何商业盈利目的。

---

## 🤝 贡献与支持

欢迎提交 Issue 或 Pull Request！
如果这个项目帮到了你，请给一个 Star ⭐️ 鼓励一下！

---
## 👤 作者
**救一人为侠**  
Bilibili: [点击主页](https://space.bilibili.com/667041249)
