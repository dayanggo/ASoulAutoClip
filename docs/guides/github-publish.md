# 整理项目并发布到 GitHub

本文面向维护者，命令使用项目根目录的 **Windows CMD**。整理、验证、提交和推送分开执行；检查与导出命令不会自动替你提交或上传。

## 目录如何固定

当前目录已按职责划分，不需要为上传再次搬动模型、录播或历史任务：

| 内容 | 固定位置 | 提交 Git |
| --- | --- | --- |
| 主流程与共享逻辑 | `core/` | 是 |
| 启动与维护命令 | `scripts/` | 是 |
| 旧版可选流程 | `legacy/` | 是，不作为主流程默认入口 |
| 提示词、纠错词典、小型静态资源 | `resources/` | 是；字体仅保留安装说明 |
| 空白配置模板 | `config/examples/` | 是，不填真实视频、模型选择或密钥 |
| 本机录播配置 | `config/local/` | 否，仅保留 `.gitkeep` |
| 依赖、测试与文档 | `requirements/`、`tests/`、`docs/` | 是 |
| CI、Issue、PR 模板 | `.github/` | 是 |
| API Key | `.env` | 否，公开 `.env.example` 的空占位 |
| 模型、推理代码与环境 | `models/`、`third_party/`、`.venv*/` | 否，按安装指南获取 |
| 切片、ASR缓存、报告、事件队列 | `outputs/`、旧 `ouput/` | 否 |
| 临时实验、备份、源码包 | `workspace/` | 否 |

根目录的 Python/CMD 文件保留为兼容入口。新增业务代码继续放 `core/`，不为了根目录看起来短就删掉旧用户仍使用的入口。更详细的命名与迁移约定见 [目录规范](../architecture/project-structure.md)。

## 说明文档怎么写

`README.md` 面向第一次接触项目的人，保持下面的阅读顺序：

1. **用途和实现范围**：输入什么、输出什么、哪些能力尚未实现。
2. **环境与首次安装**：Python、FFmpeg、依赖、模型、字体和 API 配置；不能假设对方有你的虚拟环境。
3. **最短可运行步骤**：复制空白模板、填写字段、先检查、再分析、最后生成视频。
4. **两种输入方式**：只有视频；视频配独立弹幕。说明烧录弹幕不等于独立 XML/ASS。
5. **结果在哪里**：报告、成片、封面文案、字幕、投稿清单和零产出的含义。
6. **质量与成本边界**：分数是编辑判断，API 会计费，证据可追溯不等于语义绝对正确。
7. **维护入口**：目录、测试、贡献、发布、来源与授权说明。

长篇专题放指南，README 保留摘要和链接。不要把某场录播的私人路径、人物实验、临时脚本命令当作通用安装步骤，也不要放未验证的“准确率”、未经授权的截图或虚构的 CI 成功徽章。

| 文件 | 负责说明 |
| --- | --- |
| [README](../../README.md) | 项目首页与快速开始 |
| [运行指南](quickstart.md) | 配置、运行阶段、输出和故障排查 |
| [弹幕说明](audience-and-danmaku.md) | 两种输入、时间同步、语境与权重 |
| [录播接入](recorder-integration.md) | Webhook、队列、稳定检测与重试 |
| [流程架构](../architecture/pipeline.md) | 模块关系、缓存、验证和局限 |
| [贡献约定](../../CONTRIBUTING.md) | 代码归属、依赖、测试和提交规范 |
| [变更记录](../../CHANGELOG.md) | 面向用户的行为变化，不粘贴全部提交记录 |

## 发布前先检查本地内容

```bat
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 -m scripts.check_repository
git status --short
```

检查当前跟踪文件和未被忽略的新源码，包含疑似密钥、本机数据、公开模板、大文件及文档链接。检查结果不包含匹配到的凭据原文，但它**不扫描全部 Git 历史，也不检查暂存区旧版本的全部内容**，不能代替提交差异审阅。

`.gitignore` 只影响未跟踪文件；已经跟踪的文件需要停止跟踪后才会被忽略，这是 [GitHub 官方说明](https://docs.github.com/en/get-started/git-basics/ignoring-files) 中明确区分的行为。不要执行清空整个索引再重建的命令；发现具体本机文件时，先确认路径，再针对该文件使用 `git rm --cached -- "文件路径"`，保留本机文件。

当前整理已将 `Data_source.txt` 与曾跟踪的 `.pyc` 停止跟踪，工作区文件保留。旧位置的删除与新目录的新增可能同时显示在状态里，应把对应源码一起提交，不要把新 `core/`、`legacy/`、`resources/` 或 `scripts/` 漏掉。

如密钥已经进入历史或上传过，单纯加入忽略规则不能撤回暴露的密钥；应更换对应凭据，再按 GitHub 的 [敏感数据清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) 处理。不要在 Issue 或聊天中贴出凭据值。

## 先生成一个可审阅的纯源码包

```bat
.\.venv\Scripts\python.exe -X utf8 -m scripts.export_source
```

得到 `workspace/releases/ASoulAutoClip-source.zip`。包中只有当前检查通过的源码，不含 `.git`、个人配置、模型、录播、缓存或输出。可以解压到另一个目录，按 README 从零安装，验证没有依赖维护者电脑的隐藏文件。

源码包不包含历史记录；这不意味着原仓库历史也已经清理。源码包也不附带自动确定的许可证。

## 提交与上传

本项目已经是 Git 仓库，已有 `main` 分支和 `origin`。**无需重新 `git init`，也不要删除 `.git`。** 先确认远端是不是你准备发布的仓库：

```bat
git branch --show-current
git remote -v
git log -1 --oneline
```

如果使用新的 GitHub 仓库，在 GitHub 创建一个你有权限的空仓库，不预先生成 README、`.gitignore` 或许可证。把下面 `YOUR_USERNAME` 替换为目标用户名或组织名，再修改已有远端：

```bat
git remote set-url origin https://github.com/YOUR_USERNAME/ASoulAutoClip.git
```

已有目标仓库包含其他提交时，先检查其历史与本地关系，不使用强制推送覆盖。GitHub 的 [本地项目上传教程](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github) 说明了初始化与导入的区别；当前项目直接使用已有 Git 历史。

检查通过、确认现有修改都属于本次发布后，暂存并审阅：

```bat
git add -A
git diff --cached --stat
git diff --cached --name-status
git diff --cached --check
```

再用编辑器或 `git diff --cached` 在本机查看实际差异。暂存列表中不应出现 `.env`、`config/local/*.json`、模型、虚拟环境或成片。审阅后执行：

```bat
git commit -m "docs: prepare project for GitHub publication"
git push -u origin main
```

命令中的提交说明可按实际范围修改；上传依赖你的 GitHub 账号权限及本机登录状态。不要把访问令牌写进仓库 URL、源码或文档，不使用 `--force` 处理未知的历史冲突。

## 上传后的首页与维护

- GitHub 描述可写：`中文直播录播自动切片：本地ASR、字幕证据选片、可选弹幕辅助、字幕与封面文案、录播姬队列。`
- 可按实际功能添加 `python`、`ffmpeg`、`asr`、`bilibili`、`video-editing` 等 Topics。
- 检查首页 Mermaid、相对文档链接、公开模板，以及新用户能否找到安装步骤。
- 查看 Actions 的 Windows/Python 3.12 检查结果。CI 不配置模型密钥、不下载 ASR 权重；本机测试通过不代表远端已经运行成功。
- 后续按功能独立提交；版本发布前更新 `CHANGELOG.md`，再按实际发布决定标签和 Release，不预先宣称版本已经发布。

## 来源与许可证

保留原作者、来源和已有声明。当前没有独立 `LICENSE`，历史 README 同时出现 MIT 徽章与禁止商业用途表述，尚未统一。整理源码不等于重新授权；确认来源许可及需要保留的声明后，再添加适合的许可证文件并同步 README，不要为了显示徽章直接填 MIT。

GitHub 关于许可证文件与开源声明的说明见 [Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。模型、字体、第三方推理代码及录播素材的许可应分别核对，它们不随本项目源码包分发。
