# 项目目录和维护规范

## 可发布源码

| 目录 | 只放什么 | 不放什么 |
| --- | --- | --- |
| `core/` | 主流程、模型接口、转写适配、字幕、选片、渲染及共享代码 | 单场录播实验、模型文件、运行结果 |
| `scripts/` | 启动器实现、模型下载/重对齐、审查、仓库检查和导出 | 大段重复业务实现 |
| `legacy/` | 原手动剪辑入口、旧弹幕算法、可选工具和人工提示词 | 新主流程默认实现 |
| `resources/prompts/` | 当前程序实际读取的提示词 | 不使用的历史提示词 |
| `resources/dictionaries/` | 版本控制中的纠错规则 | 单场录播的自动替换记录 |
| `resources/images/` | 小型静态封面资源 | 视频截帧、个人参考下载全集 |
| `resources/fonts/` | 字体放置说明 | 未明确授权的字体文件 |
| `config/examples/` | 空白、可分享的配置模板 | 实际视频路径、密钥和个人参数 |
| `requirements/` | 分层依赖声明 | 下载好的 wheel 和虚拟环境 |
| `tests/` | 离线测试代码 | 真实直播、付费调用和大型测试视频 |
| `docs/guides/` | 当前使用方法 | 未标明过期的旧步骤 |
| `docs/architecture/` | 架构、规范和改进路线 | 单次运行日志 |
| `docs/research/` | 方案与风格研究 | 下载的参考视频 |
| `docs/history/` | 有历史意义的技术记录 | 当成当前操作说明的旧命令 |
| `.github/` | CI、PR 等协作配置 | 密钥、账号和 Cookie |

`core/` 现阶段保留平铺模块，避免为目录美观破坏已有 `core.*` 导入和单条重剪脚本。模块归属可见 [流程架构](pipeline.md)；新增功能优先扩展对应模块，出现独立子系统后再拆包。

录播姬接入的事件、配对和队列放 `core/recorder_ingest.py`、`core/recorder_webhook.py`，命令放 `scripts/recorder_service.py`。公开接入模板为 `config/examples/recorder.example.json`；本机队列、配置快照和任务记录写入忽略的 `outputs/`，不写回录制目录。

## 本机数据

```text
config/local/      实际配置，禁止提交
.env               API 密钥，禁止提交
outputs/           新任务的默认输出，禁止提交
ouput/             旧任务历史目录，原样保留并忽略
workspace/         实验、备份、参考图片、源码导出包，忽略
models/            模型权重，忽略
third_party/       下载的外部推理代码，忽略
.venv/             常规环境，忽略
.venv-firered/     FireRed 独立环境，忽略
```

已有任务中保存了绝对路径和来源身份，不在目录整理时搬动历史输出、模型或虚拟环境。新任务逐步使用 `outputs/`；显式配置的旧输出路径继续生效。禁止靠复制整个运行目录来发布项目。

建议新任务使用 `outputs/YYYY-MM-DD_节目名_pN/`，单场配置使用 `config/local/YYYY-MM-DD_节目名_pN.json`。任务区分录播与分P，避免不同源复用一个目录。

## 旧路径迁移

| 原位置 | 新位置 / 行为 |
| --- | --- |
| `analyze_recording.py` 的实现 | `core/cli.py`，原文件保留转发 |
| `audit_clips.py` | `scripts/audit_clips.py`，根目录保留转发 |
| `download_firered_aed.py`、`realign_firered.py` | `scripts/` 下同名实现，原入口保留转发 |
| `Auto_clip.py` | `legacy/auto_clip.py`，原入口保留，支持已有 `regen_clip.py` |
| `run_clips.cmd`、`setup_firered.cmd` | 根目录为入口，实际逻辑在 `scripts/` |
| `config/highlights.example.json` | `config/examples/highlights.example.json`，加载器兼容旧路径 |
| `config/highlights.local.json` | `config/local/highlights.json`，加载器兼容旧路径 |
| `config/highlights.radio.local.json` | `config/local/radio.json`，加载器兼容旧路径 |
| `config/highlights.<场景>.local.json` | `config/local/<场景>.json`，仅兼容本项目 `config/` 下旧路径 |
| `utils/asr_dict.txt` | `resources/dictionaries/asr_dict.txt`，词典加载器兼容旧配置值 |
| `prompts/` | `resources/prompts/` |
| `assets/image/` | `resources/images/` |
| `prompt_method/` | `legacy/prompts/` |
| `danmu_method/get_data_by_danmu.py` | `legacy/danmu/analyze.py` |
| `utils/` 中的独立工具 | `legacy/tools/`，见该目录说明 |
| 根目录 `requirements*.txt` | 兼容引用；实际依赖在 `requirements/` |

只编辑新路径下的实现和资源。旧入口不再复制配置或业务函数；兼容层导入与新模块保持同一个模块对象，已有测试和调用方的补丁仍可工作。

## 文件规范

- 英文代码文件使用 `snake_case.py`；文档使用小写短横线命名。
- 默认 UTF-8；Python 四空格，JSON / YAML 两空格；CMD 为 CRLF，其余文本为 LF。
- 单个已发布资源默认不超过 10 MiB；模型、音视频、缓存与本机字体不纳入版本控制。
- 资源路径以项目根目录为基准，不依赖执行命令时所在目录。
- 生成文件必须写到任务目录或 `workspace/`，不要写回源码、提示词或原始转写。
- 不直接覆盖旧视频：保留现有临时生成、校验后替换的机制。

## 提交前

运行离线测试，再运行 `python -m scripts.check_repository`。检查涵盖当前 Git 跟踪文件和未忽略的新文件，不检查所有历史提交，也不代表内容审美或授权已经确认。

`__pycache__`、`.pyc` 和根目录的个人 `Data_source.txt` 已停止跟踪，实际文件保留在本机。图片、旧工具和源代码只是移动到新位置，未丢弃原有修改。
