# 录播姬录完后自动切片

接入使用录播姬 **Webhook v2**。原视频始终必需，弹幕为可选辅助：有同名 XML 就参考，默认等不到 XML 时仅凭视频转写切片。本项目保存事件、等文件写完、逐个调用现有流程，不扫描历史录播，不自动投稿。

```text
原始视频 + 可选同名 XML → 录制事件 → 持久化队列
  → 等录制结束、事件安静期、文件稳定及格式校验
  → 本地转写 → 证据选片 → 质量复核 → 字幕/封面/报告
```

## 一次性准备

先按 [运行指南](quickstart.md) 配好 `.venv`、FFmpeg、本地转写和模型 API。在项目根目录 **CMD** 执行，已有文件不会覆盖：

```bat
if not exist config\local mkdir config\local
if not exist config\local\recorder.json copy config\examples\recorder.example.json config\local\recorder.json
if not exist config\local\recorder-highlights.json copy config\examples\highlights.example.json config\local\recorder-highlights.json
```

编辑 `config/local/recorder-highlights.json`：填写 `llm.model` 和 `asr`，可从已跑通的配置复制模型、词典、字体和渲染设置。`input` 三项保持空白，密钥仍只放环境变量或 `.env`。`selection.brief` 使用通用要求，不要沿用上场的游戏、成员和剧情。直播间标题会单独附加，但不作为逐句说话人识别证据。

门槛建议保持 85 或更高；`max_clips` 是**每个分段**的上限，不是必须产出，没有整场总配额或跨分段去重。字幕沿用青年体、84 号、统一黄色、原底部位置和验证过的词时间。

编辑 `config/local/recorder.json`：

| 配置 | 填写内容 |
| --- | --- |
| `recordings_dir` | **录播姬的工作目录**，Webhook 的 `RelativePath` 从这里找原文件。不要用压制后“弹幕版”目录替代 |
| `base_config` | `config/local/recorder-highlights.json` |
| `allowed_rooms` | 允许处理的房间号数组，如 `[22637261]` |
| `state_dir` | 默认 `outputs/recorder-service`，保存本机事件和 SQLite 队列 |
| `outputs_dir` | 默认 `outputs/recordings`，每场、每个分段独立输出 |
| `mode` | `full` 生成视频和投稿素材；`analyze` 只转写选片 |
| `quiet_seconds` | 默认 120，收到本场最后一个新事件后等待的秒数 |
| `stable_seconds` | 默认 30，视频/XML 大小和修改时间保持稳定的秒数 |
| `danmaku_offset_seconds` | 默认 0，适用于原始视频与 XML 时间一致；核对后再手动修改 |
| `danmaku_policy` | 默认 `optional`，XML 缺失时只用视频；`required` 会一直等待 XML |
| `danmaku_wait_seconds` | 默认 60，事件安静期结束后为缺失 XML 额外保留的等待时间；视频稳定校验同时进行 |

配置内的相对路径以项目根目录为基准；命令行显式 `--config` 路径按调用位置解析。JSON 的 Windows 路径可写正斜杠，如 `E:/recordings`。

```bat
.\.venv\Scripts\python.exe -X utf8 -m scripts.recorder_service --check
```

检查不发请求、不下载权重，只检查配置、FFmpeg 和 API 配置/密钥是否存在。实际 ASR 环境、GPU 和同步情况需用一次真实录制验证。

## 设置录播姬

1. 希望参考弹幕时开启“录制弹幕”，保留 **原始视频和同名 XML**。统计“绷”无需额外保存弹幕原始 JSON。不录弹幕也能用默认 `optional` 模式切片。
2. 在**全局设置的 WebhookV2** 添加一行，已有地址保留，不要填入 WebhookV1：

   ```text
   http://127.0.0.1:18765/bililive
   ```

3. 自定义 `port` 时同步改地址。如还使用 biliLive-tools 压制/上传，确认其后处理不会在切片结束前移动、改名、删除原视频和 XML。本程序不会修改外部配置。

全局字段是字符串 `WebHookUrlsV2`，多地址换行分隔。参考 [官方设置](https://rec.danmuji.org/reference/settings/) 和 [弹幕录制说明](https://rec.danmuji.org/user/danmaku/)。

## 日常启动

在**开始录制前**启动接入服务并保持窗口打开：

```bat
cd /d E:\pyProject\ASoulAutoClip
.\.venv\Scripts\python.exe -X utf8 -m scripts.recorder_service
```

然后正常使用录播姬。接入不启动录播姬、不登录、不修改房间设置。录完后依次等待：

1. 本场有 `SessionStarted` / `SessionEnded` 和所有已知分段的打开、关闭事件。
2. 本场经过事件安静期。
3. 原视频存在且稳定，大小与关闭事件相同，实际时长大致一致且有音轨。存在同名 XML 时也检查其稳定性。
4. 存在 XML 时校验格式和弹幕时间，正常传入观众语境与“绷”反应；0 条弹幕也可以处理。缺 XML 时，`optional` 等到期限后按无弹幕模式入队，`required` 则继续等待。

已经存在但格式错误、空文件、不可读或所有弹幕在视频外的 XML 不会被悄悄忽略；修正问题后再处理。无弹幕模式同样执行证据、完整性和分数门槛，不要求必须有观众反应。

默认有稳定 XML 时通常在最后一个新事件后至少约 150 秒开始转写；缺 XML 时通常等待约 180 秒，文件仍变化会继续延后。在另一 CMD 窗口查队列：

```bat
.\.venv\Scripts\python.exe -X utf8 -m scripts.recorder_service --status
```

`waiting` 显示等待原因，`jobs` 显示 ID、状态和输出目录：

```text
outputs/recordings/<房间号>/<场次标识>/<分段标识>/
  recorder-source.json   视频/XML 身份、关闭事件、弹幕统计
  recorder-config.json   配置快照，不含 API 密钥值
  pipeline.log           转写、API 和编码日志
  analysis.json          选片证据、弃选和失败原因
  report.html            回看报告
  clips/                full 模式下合格的成片和投稿素材
```

PowerShell 可用 `Get-Content -LiteralPath "任务目录\pipeline.log" -Tail 20 -Wait` 跟踪日志。浏览器打不开视频时见 [本机预览](report-preview.md)。

状态包括 `queued` 等待、`running` 处理中、`analyzed` 已分析、`complete` 已完成、`complete_empty` 正常完成但无合格片段、`failed` 失败、`interrupted` 中断。零产出不算 API 故障，不会降低门槛补片。

## 重试和中断

失败不会无限重试。根据日志修复问题后显式重试指定 ID：

```bat
.\.venv\Scripts\python.exe -X utf8 -m scripts.recorder_service --retry "任务ID"
```

重试沿用配置快照、同一输出和有效缓存。修改基础配置或 `mode` 只影响**之后入队**的任务；需要改变已有任务参数时，复制任务配置到 `config/local/`，通过普通切片入口显式运行。

入队时会记录是否使用 XML。缺失 XML 的原因写入 `recorder-source.json`；无弹幕任务入队后才到达的 XML 不会改变快照或触发重复调用。需要使用这份迟到弹幕重新分析时，通过普通入口显式指定 `--danmaku` 和一个新输出目录。更新接入配置/代码后重启服务，新规则用于之后入队的任务。

`Ctrl+C` 会停止服务启动的切片进程组，数据保留。断电或强杀后旧工作进程有可能仍在；重启将运行中任务标为 `interrupted` 并暂停队列。**确认旧切片/ASR 进程停止后**再用 `--retry` 恢复，避免重复调用。

## 当前边界

- 处理单位是**录制 Session**。手动停止录制也会结束 Session，不等于预测主播以后不会再开播。
- 多分段有 XML 时各自配对，无 XML 时独立转写；不拼整场、不推算断流缺失时间。跨段的梗可能漏掉；证据不完整应弃选。尽量避免过短的固定分段。
- 官方说明事件可能乱序、失败最多重试三次。幂等持久化与安静期减少竞争，但尚无录播姬文件清单对账，不能证明从未丢事件。服务应先于录制启动；漏接开始事件的场次不自动补跑。历史或漏接素材按 [普通录播方式](quickstart.md) 显式指定视频、XML 和独立输出处理。
- 同名、大小及媒体时长校验降低误配风险，不能证明弹幕完全同步。首次应抽查几条弹幕；剪过、拼过、变速的视频不能直接配原 XML。
- 一个队列只运行一个切片进程，同队列第二个服务被锁阻止。不要同时手动运行其他 GPU 任务；暂不提供跨所有命令的 GPU 调度。
- 有 XML 时提供带时间的观众评论抽样作为语境，并保留“绷”提示与合格候选排序权重；弹幕不是语音证据。还没有独立弹幕峰值提名、视觉高光、自动跳剪或自动投稿。

事件字段、顺序与重试机制依据 [录播姬官方 Webhook v2 文档](https://rec.danmuji.org/reference/webhook/)。
