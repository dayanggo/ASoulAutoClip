# FireRedASR2-AED 本地模型

为中文直播录播选用的首测候选。官方实现支持中文、方言、中英混说及歌声转写，并可返回字／词级时间戳与置信度。

- 官方权重：https://huggingface.co/FireRedTeam/FireRedASR2-AED
- 官方下载镜像：https://modelscope.cn/models/FireRedTeam/FireRedASR2-AED
- 官方推理代码：https://github.com/FireRedTeam/FireRedASR2S
- 本地目录：`models/FireRedASR2-AED/`
- 主权重：`model.pth.tar`，4,731,558,506 字节。
- 主权重 SHA-256：`4677cbd30988d63ed3e777f6a42a1e5260a3865317f6e15e488bef40954f7054`。

模型目录已加入 Git 忽略。`download.complete.json` 仅在全部文件下载并通过大小及 SHA-256 校验后生成，记录下载来源、文件版本和哈希。

重新下载、续传或校验已有文件：

```powershell
.\.venv\Scripts\python.exe -X utf8 -u download_firered_aed.py
```

下载器只连接官方 ModelScope 仓库，不修改系统代理配置、不调用大模型付费 API。中断时保留 `.part` 文件，重新运行会尝试续传。

## 使用

1. 首次运行 `setup_firered.cmd` 安装 `.venv-firered` 独立环境和官方推理代码。模型不存在时，先运行上面的下载命令。
2. 修改 `config/local/highlights.json` 的 `asr` 段：

```json
{
  "backend": "firered",
  "model": "models/FireRedASR2-AED",
  "device": "auto",
  "compute_type": "auto",
  "language": "zh",
  "chunk_seconds": 25,
  "beam_size": 3,
  "initial_prompt": ""
}
```

3. 使用原来的 `run_clips.cmd`。仅转写、不调用选片 API：

```powershell
.\run_clips.cmd --prepare-only
```

完整转写、选片和渲染：

```powershell
.\run_clips.cmd
```

程序先提取 16 kHz 单声道音频，使用 Silero VAD 划分最长约 25 秒的语音段，再逐段识别，并将词时间戳加上原视频中的段落偏移。字幕按停顿、长度和时长分行，不删除原视频中的静音间隔。完成后输出 `transcript.srt`、`transcript.words.json`、`speech_regions.json` 和 `firered.raw.json`。

`compute_type=auto` 在 CUDA 上使用 bfloat16，在 CPU 上使用 float32；CUDA设备或运行失败时，auto设备模式仍可回退CPU。严格CTC对齐失败则先做下述局部恢复，不能把文字对齐失败当成设备故障而自动CPU全量重跑。`initial_prompt` 是 Whisper 专用参数，FireRed AED 不使用此提示词，也未接入单独的标点恢复模型。VAD 对低音量、歌声等素材可能漏检；转写和时间边界仍需抽样试听。

## 分段续跑与对齐异常

新运行会把通过校验的每个语音段原子写入 `asr/<任务缓存>/regions-v1/<来源与运行参数指纹>/`。中断后按原配置、原输出目录重跑，仍会提取音频并检查VAD划分，但识别结果可逐段复用。每条缓存核对录播身份、运行参数、原始音频样本哈希、语音段边界和结果指纹；损坏或不匹配的分段会单独重识别。模型与历史输出不搬动。旧运行只保存在内存、尚未写出的分段无法恢复；已完成的严格CTC整次缓存仍可复用。

某段解码产生大量重复字或无法真实对齐时，先对该段尝试FP32识别；仍失败则在中部低能量位置拆成较小的音频段，最多递归三层。每个子段仍必须提供实际CTC对齐，时间加回原位置，禁止均分时间或假造引文。成功恢复的方法记录在 `firered.raw.json` 的 `alignment_recovery`；词时间文件标记 `region_policy=1`，没有改变严格时间策略 `timestamp_policy=2`。

局部恢复后仍失败会保存其他分段并明确退出。`worker-failure.json` 区分对齐与运行错误；对齐错误不触发CPU从头重跑。FP32局部重试可能比常规BF16占用更多显存。这里的校验仅保证时间可追溯及缓存一致，不证明识别文字一定正确，恢复段尤其应试听。

## 本机验证（2026-10-18）

后续审查发现早期 CUDA / bfloat16 运行的 CTC 对齐报错，上游将词时间退回均分估计；早期“成功生成词时间戳”不能证明对齐正确。本轮已将 CTC 输出转为 float32、禁止均分降级并隔离旧缓存；保留原文字完成全部 436 段重对齐，还以真实 6 秒语音验证了修复后的完整转写路径。具体依据和限制见 [准确性审查记录](../history/current-clip-accuracy-review.md)。以下早期验证记录只说明当时识别与文件生成可运行。

- Python 3.12、PyTorch 2.7.1+cu126、torchaudio 2.7.1+cu126；依赖检查通过。
- 官方推理代码版本：`4e7d9aaf4482a47cec1724807026b9b151926eb5`。
- RTX 4060 Laptop 8 GB 上，以 CUDA / bfloat16 完成真实录播 45 秒样本的识别。
- 样本划分为 12 个 VAD 人声段，成功生成 SRT、词时间戳、原始识别结果、字幕索引和选片窗口；峰值 GPU 张量显存约 2.20 GiB，不代表整张显卡的总占用或所有素材的峰值。
- 47 项项目测试通过，包括人声段偏移、时间戳异常拒绝、字幕分段、设备回退和缓存复用。
- 未调用付费选片 API；未进行人工逐字标注对照，以上结果仅证明本机推理及流程可用，不代表准确率已经评估。
