# 开发与提交规范

## 目录归属

业务功能放 `core/`，维护命令放 `scripts/`，旧版独立工作流放 `legacy/`。不要在根目录兼容入口里增加业务逻辑，也不要把针对某一场录播的实验脚本放进 `core/`；临时实验放忽略的 `workspace/`。

配置模板放 `config/examples/`，本机配置放 `config/local/`。模型、录播、字幕缓存和成片不进源码仓库。具体归属与迁移兼容规则见 [目录规范](docs/architecture/project-structure.md)。

## 代码和数据

- Python 使用 UTF-8、四空格缩进，函数、变量和文件使用 `snake_case`，类使用 `PascalCase`。
- JSON / YAML 使用两空格缩进；Windows CMD 使用 CRLF，其余文本使用 LF。由 `.editorconfig` 和 `.gitattributes` 约定。
- 项目内路径通过 `core/paths.py` 管理；不写开发者绝对路径。配置中的相对素材路径以项目根目录为基准，命令行显式路径按调用位置解析。
- 共享默认配置只保留一份；示例不填写私有路径、密钥或默认付费模型。密钥从环境变量或本机 `.env` 读取。
- 提示词是运行时资源，放 `resources/prompts/`；词典放 `resources/dictionaries/asr_dict.txt`。不要把业务规则藏在 README 或个人配置里。
- 保留原始识别、字幕时间、证据和来源身份。显示纠错、编辑概括与真实引文分开记录，不伪造已确认的说话人或画面事实。
- 修改会影响缓存结果的输入、模型参数、提示词或处理规则时，更新对应缓存身份；不能为了复用而让旧结果冒充新分析。
- 不为填满片数降低证据标准；缺少看点可以正常弃选。模型评分不写成准确率。

## 文档与用户反馈

README面向第一次安装的用户，应写清输入、输出、最短命令、两种弹幕情况和已知限制，不依赖维护者的本机配置。专题步骤放 `docs/guides/`，架构与规范放 `docs/architecture/`，旧实验与验证记录放 `docs/history/`，修改后同步 `docs/README.md` 索引。

对用户可见的命令、配置、输出或缓存行为变化，更新对应指南并在 `CHANGELOG.md` 的“未发布”中记录。Issue模板收集复现步骤和脱敏日志；不要求用户公开密钥、Cookie、完整个人配置或真实录播。整理和上传步骤见 [GitHub发布指南](docs/guides/github-publish.md)。

## 依赖

公共运行依赖维护在 `requirements/base.txt`；本地转写依赖在 `requirements/asr.txt`；FireRed 独立环境在 `requirements/firered.txt`。根目录三个依赖文件只是旧命令转发。不要在运行时偷偷修改系统代理或安装全局包。

## 验证

```bat
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 -m scripts.check_repository
```

测试不能使用维护者密钥、真实录播或大型模型。优先模拟接口响应，必要时用 FFmpeg 生成几秒的临时素材验证实际音画处理。修改公共入口、路径或缓存时，应验证干净源码副本，不依赖本机配置才能通过。

## 提交与 PR

建议使用 `feat:`、`fix:`、`refactor:`、`docs:`、`test:`、`chore:` 前缀，后面描述具体结果。一项提交集中解决一个问题，不混入运行产物。

PR 说明问题、修改后的行为和验证结果；涉及配置、命令、输出目录的变更同步更新指南。测试分数或局部成功案例不能被描述成总体切片质量提升的证明。

提交前运行仓库检查，再用 `git diff --cached --stat` 确认实际提交范围。不要强制添加被忽略的本机文件。检查不会替你提交、推送或上传任何内容。
