# 自动切片方案调研与 ASoulAutoClip 改进路线

调研日期：2026-10-18。适用条件：Windows、RTX 4060 Laptop 8GB、直播结束后自动处理、主要处理 A-SOUL 直播、最终投稿 B 站。

本轮查阅了开源仓库、部分核心源码、版本记录及厂商 API 文档。下文区分“查到的能力”和“对本项目的设计建议”。尚未用同一批 A-SOUL 录播对这些工具做出片效果对比，也没有调用付费服务；不能据此承诺选片准确率、运行时间或爆款概率。

## 1. 推荐结论

继续保留 ASoulAutoClip，优先升级选片和任务恢复能力。直播信号处理重点参考 HotClip，文本选片参考 zhouxiaoka/autoclip 与 artbyjazi/autoclip；录制、弹幕采集和投稿优先通过现成工具适配；现有字幕、封面模板及单片再生成流程继续作为出片基础。

建议的主链路：

```text
录制视频 + 原始弹幕 + 直播会话信息
  → 下播确认、文件完整性检查、分段时间轴整理
  → 本地 ASR、保留原始和校正后的字幕
  → 全文语义候选 ∪ 弹幕峰值候选 ∪ 音频事件候选
  → 模型选择字幕 ID，给出事件描述和证据
  → 必要时对候选画面进行视觉复核
  → 边界修正、全局排序、去重
  → FFmpeg 字幕与剪辑、模板封面、标题
  → 成片检查 → 投稿 → 保存稿件回执并查询后续状态
```

优先完成“从已有录播和 SRT 自动生成可靠切片”，再连接实时录制和无人值守投稿，便于逐段验收。

## 2. 开源方案比较

| 方案 | 查到的能力 | 对本项目最有价值的部分 | 采用判断 |
| --- | --- | --- | --- |
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) | 本地转写、弹幕与音画证据、候选复核、字幕和发布素材导出 | 弹幕防刷屏、原文定位、人工切点保护、导出文件保护 | 直播选片的重要参考；发布素材包不等同于 B 站自动投稿 |
| [zhouxiaoka/autoclip](https://github.com/zhouxiaoka/autoclip) | 本地出片，支持模型配置、字幕、封面、编辑和多种入口 | 长字幕分窗、字幕 ID 定位、结果校验、任务失败分类 | 分析架构首选参考；仍需适配直播梗 |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | Whisper 转写、本地或云端选片、字幕和重构画幅 | 词 ID、重叠窗口、边界修正、单机队列及阶段产物 | 核心模块清楚；作者明确标注 pre-1.0、选片质量尚未验证 |
| [CercisLinn/Bilibili-Auto-Clipping](https://github.com/CercisLinn/Bilibili-Auto-Clipping) | B 站录播分析、候选推荐、审片、导出 | 主播配置、事件证据、保留/拒绝原因 | 场景接近；仓库很早期，未提供 LICENSE，作为设计参考 |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | 中文转写、字幕、按文本或说话人剪辑、LLM 辅助 | 中文 ASR、热词、文本与视频的对应 | 适合作为转写与精确裁切参考；需自行补录制、投稿 |
| [Aseiel/VideoHighlighter](https://github.com/Aseiel/VideoHighlighter) | 多种音画及文本信号评分、规则与片段报告 | 候选理由可解释、多信号融合 | 参考信号设计；不能把动作多等同于节目效果 |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 基于转写的长视频分段及画幅处理 | 语义主题边界 | 更接近访谈主题分段；不作为新系统主底座 |
| [renmu123/biliLive-tools](https://github.com/renmu123/biliLive-tools) | 直播录制、视频和弹幕处理、Webhook 与上传工作流 | 录制结束事件、弹幕采集、投稿适配 | 基础设施优先候选；由本项目负责高光决策 |
| [biliup/biliup](https://github.com/biliup/biliup) | 直播录制、下载、B 站登录及投稿 | 投稿适配器、回执和错误处理入口 | 可作为录制/投稿的另一选择；不必同时部署两套录制器 |
| [timerring/bilive](https://github.com/timerring/bilive) | 录制、ASR、弹幕处理、密度切片、上传串联 | Provider 思路、上传队列、流程串联 | 延续前轮结论：借鉴接口，避免原样移植主流程 |
| [Xiaoyi-ZHANG23/bili-autoclip](https://github.com/Xiaoyi-ZHANG23/bili-autoclip) | Windows/NVIDIA、录播姬 Webhook、弹幕候选、ASR、审片；最终手动投稿 | 杂谈与歌回分开处理、候选粗切后精切 | 设计贴近场景；v0.1，初期仓库，不能按成熟全自动产品采用 |
| [Cbhhhh211/livestream-highlight-clipper](https://github.com/Cbhhhh211/livestream-highlight-clipper) | 弹幕、语音与能量评分、峰值切片 | 多信号评分与无弹幕回退 | 算法参考；不因信号加权就认定已经理解笑点 |
| [Yht20927/douyin-living](https://github.com/Yht20927/douyin-living) | 抖音录制、弹幕、ASR 与多模态融合 | 弹幕—音频时间延迟补偿、峰值与边界处理 | 可借鉴算法，录制平台需要替换 |
| [bendawg2010/Auto-clipper](https://github.com/bendawg2010/Auto-clipper) | 面向游戏事件及触发条件的自动片段生成 | 特定游戏事件召回 | 可用于未来游戏专场，优先级低 |

维护状态也要纳入判断：本次 GitHub 查询中，zhouxiaoka/autoclip 的 main 分支最近推送已到北京时间 2026-10-18，正式发布查询返回 v1.5.6；主分支更新不能直接当成正式包能力。ClipsAI 最近推送记录为 2024-01-17；CercisLinn 项目仅见 2026-06-08 的初期活动。时间与版本只表示仓库状态，不代表质量排序。来源：[AutoClip 发布记录](https://github.com/zhouxiaoka/autoclip/releases)、[ClipsAI 提交记录](https://github.com/ClipsAI/clipsai/commits/main/)、[Bilibili-Auto-Clipping 提交记录](https://github.com/CercisLinn/Bilibili-Auto-Clipping/commits/main/)。

### 2.1 最值得参考的选片实现

**HotClip：直播信号和定位质量。** `danmaku.ts` 对有用户标识的弹幕限制单人贡献，并考虑热度突增；`match.ts` 用原文引文与句 ID 范围反查时间，区分精确匹配、首尾锚定和句级回退。对本项目最有用的是保留这些可检查的依据。[弹幕源码](https://github.com/xixihhhh/hotclip/blob/main/src/core/danmaku.ts)、[定位源码](https://github.com/xixihhhh/hotclip/blob/main/src/core/highlight/match.ts)

它的 `normalizeScores` 会把推荐候选的排名映射到 76–99，单条推荐候选显示 97。因此 97 不是预测准确率，也不适合直接作为无人值守投稿阈值。[评分源码](https://github.com/xixihhhh/hotclip/blob/main/src/core/highlight/detect.ts)

**zhouxiaoka/autoclip：参考当前实现，避免照搬旧流程。** 仓库仍保留“大纲—时间线—评分”等旧阶段；本次读取的 `clip_finder.py` 已采用带序号字幕的窗口选片，区分本地/云端上下文预算，并校验和去重。对本项目来说，“有界窗口 + 可回查的字幕 ID”比增加很多轮模型调用更有价值。[核心源码](https://github.com/zhouxiaoka/autoclip/blob/main/backend/pipeline/clip_finder.py)

**artbyjazi/autoclip：定位和恢复机制容易借鉴。** `highlights.py` 使用 8 分钟窗口、60 秒重叠，由模型选择词索引，再经过边界修正和再次去重；这些参数属于该仓库的默认值，并非 A-SOUL 最优值。[选片源码](https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/pipeline/highlights.py)、[边界源码](https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/pipeline/boundaries.py)

其架构文档还采用单机队列、阶段文件和相对字幕滤镜路径。本项目可在此基础上增加输入指纹和产物校验，使字幕或提示词改动后正确失效重算。[架构说明](https://github.com/artbyjazi/autoclip/blob/main/docs/ARCHITECTURE.md)

**CercisLinn：参考“为什么值得剪”的数据结构。** 其分析源码要求描述事件、转折、结果以及原文证据，并保存拒绝原因。这种思路适合审片反馈，但仓库里的分数阈值尚不能当作可直接上线的质量标准。[分析源码](https://github.com/CercisLinn/Bilibili-Auto-Clipping/blob/main/src/autoclip/llm_analysis.py)

补充算法参考：douyin-living 的评分源码使用互相关估计弹幕与音频的时差。可以借鉴“先校准反应延迟”的思路，但两种信号不相关时，最强相关点未必是真实时差，本项目仍应保留最大偏移、可信度检查与不调整回退。[时间补偿源码](https://github.com/Yht20927/douyin-living/blob/main/src/scorer.py)

## 3. 商业服务与云 API

这些服务可以成为可替换的分析器或对照组。是否采购，需要用相同录播验证中文人名、多人对话、内部梗、完整上下文和总成本。

| 方案 | 已核实的接口能力 | 对你的适用方式 |
| --- | --- | --- |
| [OpusClip API](https://www.opus.pro/api) | 长视频建任务，支持时间范围、片段长度、主题、字幕/模板等参数 | 可快速对比成片；已有公开 API，不能再按“只能网页手动操作”判断 |
| [Vizard API](https://docs.vizard.ai/docs/quickstart) | 提交视频、异步查询/回调，按配置生成片段 | 商业对照组；文档要求付费方案才能使用 API |
| [Klap API](https://docs.klap.app/) | 长视频到短片，字幕、画幅重构及导出任务 | 通用对照组；需单独验证虚拟主播和横屏效果 |
| [腾讯云 MPS 精彩集锦](https://cloud.tencent.com/document/product/862/107280) | 大模型版可按自定义 Prompt 分析；高级版偏体育/电竞 | 国内云候选；应选可定制的大模型路线 |
| [火山引擎大模型高光剪辑 API](https://docs.volcengine.com/docs/Intelligentprocessing/SubmitlargemodelhighlightclippingtaskAPI?lang=zh) | 支持分析范围和结构化片段；`skip_video_output=true` 可只返回分析结果 | 与现有 FFmpeg 出片层衔接较自然，值得优先试验 |
| [阿里云 AnalyticDB 智能高光剪辑](https://help.aliyun.com/zh/analyticdb/analyticdb-for-mysql/intelligent-highlight-editing) | 异步高光剪辑，支持不同剪辑模式 | 备选；文档中有单视频时长等限制，长直播需要适配 |
| [TwelveLabs](https://docs.twelvelabs.io/docs/get-started/release-notes) | 视频理解与按时间组织的信息输出 | 可作视频事件理解组件，仍需自己渲染和投稿 |
| [Eklipse](https://eklipse.gg/help/how-does-eklipse-ai-choose-highlights/) | 面向游戏高光识别 | 更适合游戏专场，聊天与团播优先级低 |

腾讯文档已区分大模型版、高级版和基础版；基础版从 2025 年 12 月起下线、仅服务存量客户。不能看到“自动集锦”就假设可直接用于所有直播类型。[官方版本说明](https://cloud.tencent.com/document/product/266/131213)

火山上述接口能只输出结构化高光结果，这一点契合本项目：云端给出候选区间，字幕样式、封面、渲染仍沿用本地实现。这里是接入可行性判断，尚无 A-SOUL 效果结论。[接口参数](https://docs.volcengine.com/docs/Intelligentprocessing/SubmitlargemodelhighlightclippingtaskAPI?lang=zh)

云方案总成本应计算“分析用量 + 对象存储 + 上传下载流量 + 重试”，同时记录整场直播上传所需时间。当前不提供未经实际计费验证的每场价格，也不把厂商的传播评分当成播放量保证。

## 4. 本项目最该改进的选片方法

以下为结合现有代码提出的方案，尚待实现和实测。

### 4.1 候选来自多条路径，保留全文覆盖

目前 `danmu_method/get_data_by_danmu.py` 主要先按弹幕密度确定候选，模型再生成片段信息。建议改成三路取并集：

1. **全文语义选片**：让所有字幕都进入有重叠的分析窗口，找完整笑点、反转、互动、故事和观点。
2. **弹幕峰值**：保留现有优势，按本场局部基线计算异常热度，去重计数、保留峰值位置；可识别“哈哈”等群体反应，但不能让刷屏独占候选。
3. **音频事件**：笑声、惊呼、音量变化作为补充线索；音量大只能提高召回优先级，不能直接判定精彩。

弹幕存在反应延迟，录制分段也可能改变文件内时间。需要统一视频、字幕、弹幕的时间轴，记录每个分段的来源偏移和不连续处。低弹幕片段仍可由语义或画面路径进入候选。

若素材存在大量无对白的动作笑点，再加低频画面扫描作为第四条召回路径；仅复核文本候选不能找回完全没被召回的纯视觉事件。

### 4.2 让模型返回字幕 ID，程序映射真实时间

第一版直接使用已有 SRT 句子序号；有词级 ASR 后再支持词序号。建议的模型输出示意：

```json
{
  "event_type": "reaction",
  "start_subtitle_id": 120,
  "end_subtitle_id": 146,
  "evidence_ids": [128, 131, 139],
  "setup": "用原文说明前因",
  "turning_point": "说明发生了什么变化",
  "payoff": "说明观众能看到或听到的结果",
  "reason": "为什么值得独立观看",
  "title": "基于证据生成标题"
}
```

示例字段表达数据结构，不代表已经找到某段素材。程序需校验 ID 存在、先后顺序、证据属于候选、结果没有超出当前窗口，再查询原始毫秒时间。模型分数只用于排序，不能解释成发布成功概率。

分窗初始可试 8–15 分钟、重叠 60–90 秒，并另设 token/字符上限。这是待测试起点；说话密度、模型上下文和目标片长变化后需要调整。跨越多个窗口的长事件可扩展上下文再次判断。

### 4.3 用事件类型决定边界

现有固定前补 5 句、后补 2 句，容易引入无关内容，也可能不足以解释前因。建议让模型先选完整事件，再在有限范围内对齐句首、句尾或自然停顿。

| 类型 | 边界重点 |
| --- | --- |
| 一句话笑点、口误 | 留足触发句和紧接的反应 |
| 多人互动、反转 | 保留前因、转折和结果；不能只剩爆笑声 |
| 故事、观点 | 保留理解所需背景及完整结论 |
| 唱歌、表演 | 单独设置完整性和时长规则 |
| 纯视觉事件 | 使用画面证据，不强求字幕中存在三段式故事 |

边界修正后再次去重，结合时间重叠与事件相同程度处理重复提名。保留喜剧停顿；VAD 可辅助转写和定位，不能直接用于删除成片里所有静音。

### 4.4 给 A-SOUL 维护可验证的领域信息

建立成员昵称、人名、常见误识别、内部梗、直播类型及正负例配置。已有 ASR 纠错词典可继续用，但应同时保存原文、校正文和修改记录。

少量高质量正负例比仅要求“找有节目效果的片段”更容易评估：例如“有前因的反转”是正例，“只有哈哈、没有触发事件”是负例。避免把普通笑声、固定口头禅或感谢礼物全部判为高光，也避免因包含“谢谢”直接删掉整句。

多人说话分离可作为后续能力。声纹分出的 Speaker A/B 不等于已知道具体成员，不能直接据此自动标人名。

### 4.5 视觉分析优先用于候选复核和封面

先围绕候选抽取少量带时间戳的画面，判断动作、表情、画面切换是否支持选片理由；需要识别短暂动作时再提高采样密度。稀疏静态帧无法覆盖所有动态事件。

封面继续使用现有 Pillow 模板，升级为“多时刻选帧 → 排除黑屏、模糊、转场 → 选择与事件对应的画面 → 生成短文案”。虚拟形象及多人团播需要专门验证取景效果，不能直接套用访谈类真人脸追踪。B 站首版优先保留原始横屏构图。

## 5. 4060 8GB 的实际部署建议

| 环节 | 首版选择 | 验证重点 |
| --- | --- | --- |
| ASR | 本地 faster-whisper，比较 `turbo` 与 `large-v3`；小 batch 起步 | 中文专名、多人重叠语音、耗时与显存 |
| 中文 ASR 备选 | FunASR/FunClip 热词路线 | 相同片段上的专名和口语识别效果 |
| 文本选片 | 可切换本地小模型和云文本 API | 证据准确性、漏剪、上下文长度；付费调用保持可选 |
| 视觉复核 | 先少量候选，按模型后端选择本地或云端 | 实际可运行性、采样密度、额外收益 |
| 渲染 | FFmpeg，功能探测通过后用 NVENC；保留软件编码回退 | 字幕路径、字体、音画同步、输出完整性 |
| 调度 | SQLite + 单个 GPU worker，阶段间释放模型 | 中断恢复、显存释放、任务状态 |

faster-whisper 官方实现支持量化、词级时间戳和 VAD；其公开 GPU 基准使用的是 RTX 3070 Ti，不能直接当成这台 4060 Laptop 的速度承诺。CUDA/cuDNN/CTranslate2 组合需要做实际小样本探测。[官方说明](https://github.com/SYSTRAN/faster-whisper)

建议按“ASR → 释放模型 → 文本/视觉分析 → 渲染”串行安排 GPU 重任务。8GB 的可用显存还受桌面占用、模型上下文、分辨率和 batch 影响；暂不承诺某个本地大模型一定满足整场处理速度。

## 6. 接入现有代码的改造位置

| 现有文件/入口 | 建议改动 |
| --- | --- |
| `Auto_clip.py` | 保留批处理入口；增加自动分析入口，将已有 `Data_source.txt` 作为兼容导入/导出格式 |
| `danmu_method/get_data_by_danmu.py` | 拆出纯候选生成，修正重叠计数与截断丢峰值；候选时间由程序管理 |
| `core/subtitle_utils.py` | 增加稳定字幕 ID 和毫秒映射，按新边界重建自动字幕，保留手动版本 |
| `core/video_processor.py` | 临时目录渲染、校验后替换正式产物；明确成功/失败回传 |
| `core/cover_generator.py` | 增加事件相关多帧选择，保留现有模板 |
| `core/regen_script.py` | 将可编辑参数与生成脚本分开，重跑保护用户修改 |
| 新增分析模块 | 统一 ASR、语义选片、弹幕/音频候选、视觉分析的输入输出 |
| 新增任务与投稿模块 | SQLite 状态、阶段缓存、重试、录制器事件接入、投稿回执 |

建议每个阶段保存输入指纹、实现版本、模型与提示词版本、配置及产物路径。缓存键变化或产物校验失败时重算对应阶段；修改封面不应重新做整场 ASR。

Windows 下可借鉴将 ASS 与字体放入任务目录、FFmpeg 的 cwd 指向任务目录、滤镜引用简单相对文件名的方式，并使用参数数组启动进程。本项目已存在的单引号路径问题需要用实际路径回归验证。

## 7. 自动化可靠性与实施顺序

### P0：修复会损失文件或误报完成的问题

先修复已在本项目审查中确认的问题：渲染前清理旧产物、重新生成覆盖人工修改、改变时间范围沿用旧 ASS、模型失败产生缺时间字段的数据、全部失败却报告完成，以及 `utils` 导入冲突。成功标准是“失败能定位、旧结果保留、重跑结果正确”。

### P1：完成已有录播的自动选片

完成 SRT ID 映射、重叠分窗、全文语义与弹幕候选合并、事件证据、边界修正及去重。自动生成现有出片格式，直接复用字幕、封面和渲染代码。

这一阶段已经能检验最关键的问题：选出的东西是否值得看、起止是否完整。

### P2：补转写、视觉和领域反馈

加入本地 ASR，比较人名和口语准确性；增加候选视觉复核与封面选帧；记录保留、拒绝、改边界的原因。用反馈调整提示词和规则，保留独立验证集。

### P3：接通下播触发和自动投稿

录制器的“单个文件写完”不一定表示整场结束。按直播会话汇总分段，在下播确认且文件停止写入、ffprobe 检查通过后启动处理；短暂断流或重连需合并判断。弹幕和视频的原始时间信息一起保留。

投稿由独立适配器执行。示意状态为：

```text
recorded → transcribed → analyzed → rendered → validated
         → submitting → submitted → published
```

这些是建议的本地状态名称。上传返回成功应先保存稿件回执，再查询实际后续状态；提交超时先查询是否已创建稿件，避免盲目重发。投稿幂等键可包含账号、源会话、最终区间、渲染版本和成片指纹。登录失效进入等待处理状态，原视频保留。

自动发布前检查文件可解码、时长、音轨、字幕范围、封面与标题。质量校准期可以抽查样片；稳定后按用户设定自动投稿，无需把人工审片固化成每次必须操作的步骤。

## 8. 用什么试验决定“最适合”

选 3–5 场不同类型的直播，人工标记约 20–30 个目标片段作为首轮样本；另留一场不参与提示词调整。数字是试验规模建议，不是效果保证。

| 对照组 | 目的 |
| --- | --- |
| A：当前弹幕密度方法 | 保留真实基线 |
| B：全文字幕 ID 选片 | 测量语义分析能找回多少低弹幕好片段 |
| C：语义 + 弹幕/音频 + 边界修正 | 测量多路召回和边界策略的增益 |
| D：C + 视觉复核 | 测量增加视觉成本是否值得 |
| E：任选一个云高光 API，可选 | 相同输入对比云服务与本地方案 |

记录人工接受率、目标事件召回、边界缺失/冗余秒数、重复片段、人名错误、每场处理时间、峰值显存、费用和失败恢复情况。候选数量尽量一致，同时记录实际产出数量，避免靠多输出片段制造“召回更好”的假象。

选片质量需要看实际内容；工程可靠性还需单独检查中途退出恢复、失败不删旧文件、人工修改保留、重复触发不重复投稿。没有同素材对比前，不给任何项目标注“准确率最高”。

## 9. 当前不值得优先增加的工作

- 大规模 Agent 编排、多服务集群和复杂消息队列：先用单机阶段任务把主流程跑稳。
- 自训练高光模型：先积累带保留/拒绝理由的真实样本。
- 全场密集视觉分析：先验证候选复核的实际收益，再决定是否扩大覆盖。
- 自动删除所有停顿、自动强制竖屏、每个封面都图生图：先保持 A-SOUL 内容完整和画面可读。
- 仅凭模型自评分直接发布：先用同素材对比校准规则，并保留可回查证据。

学术上可参考 [Moment-DETR / QVHighlights](https://github.com/jayleicn/moment_detr) 的“查询对应视频区间与高光”任务定义，以及 [LINE Lighthouse](https://github.com/line/lighthouse) 的视频片段检索、高光检测和音频片段检索复现框架。但通用数据集表现不能直接证明对中文团播内部梗有效。近期最有价值的工作仍是选片定位、领域样本和可靠任务流程。
