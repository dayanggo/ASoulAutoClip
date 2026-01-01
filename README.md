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
