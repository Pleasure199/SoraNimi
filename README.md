# SoraNimi

基于 LangGraph / LangChain 的本地编程 Agent，提供 Web 工作台与 CLI 两种运行模式。模型可以探索代码、精确编辑文件、执行测试，经过权限审批后继续执行；会话和文件快照保存在本地。

当前版本：`0.3.0`。核心开发闭环与高级扩展的基础实现已经接通；详细完成度和未实现项见 [实现状态](docs/implementation-status.md)，原方案见 [项目规划](coding-agent-plan.md)。

## 安装与离线演示

需要 Python 3.10+。本项目使用已有的 conda **Lang** 环境，路径为 `F:\miniconda3\envs\Lang`。

```powershell
conda activate Lang
python -m pip install -e ".[ui,dev,mcp]"

# 推荐：启动图形工作台
python -m soranimi --ui

# 不需要 API Key 的 UI 演示
python -m soranimi --ui --demo

# 原有 CLI 模式保持可用
python -m soranimi --demo --non-interactive
```

离线演示通过真实 LangGraph 图调用 `list_dir`，不需要 API Key，不编辑业务文件。它会创建本地 `.soranimi/` 会话记录。
浏览器打开 `http://127.0.0.1:8765`。也可以执行 `./start-ui.ps1`（演示模式：`./start-ui.ps1 -Demo`），脚本使用上面的 Lang 环境。
独立入口为 `soranimi-ui` / `python -m soranimi.web.launcher`；`soranimi` / `python -m soranimi` 默认保留 CLI。
UI 使用随 Python 包分发的 HTML/CSS/JavaScript，不需要 Node、npm、前端构建或外部 CDN。完整操作说明见 [Web 工作台指南](docs/ui-guide.md)。

`requirements-dev.lock` 记录早期 Python 3.10 CLI 环境的依赖版本。当前 Lang 环境可按 `pyproject.toml` 安装，不要用旧 lock 覆盖整个 conda 环境。PostgreSQL 是额外选项，未纳入当前测试。

## 连接模型

复制 `.env.example` 为 `.env`，填写所用提供商的 Key。也可以直接设置环境变量；环境变量优先于 `.env`。

```powershell
$env:DEEPSEEK_API_KEY = "你的 Key"
python -m soranimi --model deepseek:deepseek-chat
```

CLI 默认使用 `deepseek:deepseek-chat`；`SORANIMI_MODEL` 可覆盖默认值。

| 选择方式 | Key 环境变量 | 地址 |
| --- | --- | --- |
| `--model deepseek:模型名` | `DEEPSEEK_API_KEY` | 默认 DeepSeek API |
| `--model openai:模型名` | `OPENAI_API_KEY` | 提供商默认地址 |
| `--model anthropic:模型名` | `ANTHROPIC_API_KEY` | 提供商默认地址 |
| OpenAI 兼容服务 | `SORANIMI_API_KEY` | `--base-url` 或 `SORANIMI_BASE_URL` |

请填写账号实际有权限使用、支持原生工具调用的模型名。Anthropic 仍使用专用 Key；`SORANIMI_API_KEY` 用于 OpenAI 协议适配器。

```powershell
# 单次任务
python -m soranimi "修复 src 中的错误，并运行相关测试" --workspace F:\my_project

# 从本地端点读取模型（即使本地服务不验证 Key，也需要提供非空占位值）
$env:SORANIMI_API_KEY = "local"
python -m soranimi --model openai:你的模型名 --base-url http://localhost:11434/v1

# 恢复会话、列出会话
python -m soranimi --sessions
python -m soranimi --resume 会话ID
```

指定其他工作区时，读取的是那个目录下的 `.env` 和 `AGENTS.md`。模型配置不存储 Key；恢复会话时需再次提供环境变量和所需 CLI 配置。

## 权限与执行环境

| 模式 | 文件编辑 | 命令 |
| --- | --- | --- |
| `accept-edits`（默认） | 自动 | 只读/构建测试自动，其他审批 |
| `manual` | 审批 | 所有 shell 命令审批 |
| `plan` | 禁止 | 只读命令允许，构建测试与写操作禁止 |
| `auto` | 自动 | 普通命令自动，危险命令仍审批 |

专用读取工具在所有模式下可用。命令包含管道、重定向、shell 展开或解释器内联执行时会提高风险等级。
`sudo`、`eval` 和显式 shell 包装命令等直接拒绝。未知外部/MCP 工具均审批，`plan` 模式禁止外部调用。

`--non-interactive` 不自动批准：出现审批时保存检查点并以 **2** 退出；在交互终端用 `--resume` 可以继续批准或拒绝。成功退出码为 0，错误为 1，中断为 130。

默认的 `subprocess` 是受信任本地执行：Windows 使用 PowerShell，Unix 使用 `/bin/sh`；每次从工作区根目录启动，变量和 `cd` 不跨调用保留。
文件工具限制工作区路径并保护 Git/Agent 内部目录，但 shell、Git 和 MCP 不是这个文件工具边界内的操作。执行不可信代码应选择 Docker：

```powershell
docker build -t soranimi-sandbox:latest docker
python -m soranimi --sandbox docker
```

Docker 执行器使用断网、只读容器根目录、可写工作区、临时 `/tmp`、资源限制及 capability 限制；它隐藏顶层 `.git` 和工作区内的会话数据目录。
需要运行中的 Docker Linux daemon 和已构建的镜像。镜像构建可以联网，工具执行容器断网。Docker 仅用于 `bash`，MCP 服务器与模型客户端仍在宿主机运行。
容器仍能看到挂载工作区中的源文件和 `.env`，请勿把无关敏感文件放入不可信任务的工作区。

## 交互方式

Enter 发送，Alt+Enter 换行，Ctrl+C 中断并保留会话。支持斜杠命令及 `@文件路径` 补全；文件提及由模型通过工具读取。

| 命令 | 功能 |
| --- | --- |
| `/help`、`/exit` | 帮助、退出 |
| `/plan` | 当前任务步骤 |
| `/compact [重点]` | 摘要压缩，保留原始本地历史 |
| `/diff`、`/undo` | 当前会话文件差异、撤销最近一次 edit/write |
| `/sessions` | 会话列表 |
| `/checkpoints` | 图检查点，标记可回溯的完整轮次 |
| `/rewind ID` | 回到完整轮次检查点，不修改磁盘文件 |
| `/memory` | 查看已保存的项目偏好 |

编辑需要先通过 `read_file` 完整读取文件；分页或截断读取不授予编辑资格。编辑使用唯一字符串匹配，保留 UTF-8/BOM、UTF-16 大小端和原行尾，写入采用同目录临时文件原子替换。
外部修改会使编辑失效；撤销也检查当前内容，防止覆盖后续用户工作。`/undo` 不回滚命令、Git 提交或外部服务操作。

启动时按全局 `~/.soranimi/AGENTS.md` → 工作区 `AGENTS.md` 加载指令。`load_instructions` 的库接口支持工作区到指定子目录的分层加载，总内容限制为 32 KiB；CLI 当前工作目录固定为工作区根目录。

## 可选扩展

### 子 Agent

`--subagents` 开启 `spawn_subagent` 和 `parallel_subagents`。子 Agent 具有独立消息上下文，仅执行代码研究并返回摘要；最多并行 6 个，不能继续派生子 Agent。主 Agent 通过 `update_plan` 维护步骤，负责修改代码和运行测试。

### 多模型配置与子 Agent 模型选择

可在同一个 JSON 文件中定义多个命名模型，各自使用独立提供商、API 地址和密钥环境变量。复制并修改 [models.example.json](models.example.json)，将示例模型名换成账号实际可用的模型；不要填写明文密钥。`api_key_env` 指定的变量可放在工作区 `.env` 中。

```json
{
  "main": {"model": "deepseek:deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"},
  "research": {"model": "openai:your-model", "base_url": "http://localhost:11434/v1", "api_key_env": "RESEARCH_API_KEY"}
}
```

```sh
python -m soranimi --ui --models-config models.json --model main --subagents --subagent-model research
# CLI 使用相同参数，去掉 --ui 即可；独立 soranimi-ui 入口同样支持。
```

`--models-config` 的相对路径以工作区为基准，也可设置 `SORANIMI_MODELS_CONFIG`；默认子 Agent 模型可通过 `SORANIMI_SUBAGENT_MODEL` 设置。原有 `--model provider:model` 仍可使用；不指定子 Agent 模型时沿用主模型。`--summary-model` 也支持配置名。

Web 设置页支持选择主模型、开启只读子 Agent 并选择其默认模型，应用于新会话并保留到会话恢复。浏览器只接收配置名和模型名，不接收密钥或 API 地址。配置文件仍在服务端维护，修改后重启服务。

主 Agent 可在 `spawn_subagent(task, model="research")`、`parallel_subagents(tasks, model="research")` 中为本次委派选择配置；`plan_and_execute` 使用 `worker_model="research"`。工具参数只接受已配置的名称，省略时使用默认子 Agent 模型；规划器和重规划器仍使用主模型。

命名配置不会继承全局 `SORANIMI_BASE_URL` / `SORANIMI_API_KEY`；省略 `base_url` 使用提供商默认地址，省略 `api_key_env` 使用对应提供商的标准 Key 变量。指定的密钥变量缺失时直接报错，不会借用其他模型账号。直接指定子 Agent 的 `provider:model` 时也不继承主模型自定义地址；需要独立地址或密钥时请使用命名配置。

### 上传与解析文档

Web 输入区点击「上传文档」，可上传 TXT、MD、CSV、JSON、LOG、TSV、PDF、Word `.docx` 和 PowerPoint `.pptx`。解析完成后可预览文本、勾选附件并发送任务；没有输入问题时默认总结所选附件。后续提问可再次勾选会话中已上传的附件。

- 文本支持 UTF-8（含 BOM）、带 BOM 的 UTF-16 和 GB18030。PDF 提取可选取的文字并保留页码标记；DOCX 提取正文段落和表格；PPTX 提取文本框、分组、表格及演讲者备注，保留幻灯片编号。
- 旧版 `.doc`/`.ppt` 需先另存为 `.docx`/`.pptx`；不支持加密文档、OCR、嵌入图片内容或完整视觉版式还原。复杂 PDF 的阅读顺序和表格结构可能不完整，请核对原件。
- 单文件最多 20 MiB；PDF/PPTX 最多 500 页/张；每次最多引用 8 个附件；每会话最多 20 个、累计 100 MiB。解析文本最多保留 50 万字符，截断时提示。Office 解压体积和条目数量也有限制。
- 二进制文档在独立 Python 进程中解析，超时 30 秒，支持的平台附加 CPU/内存限制，不启动 Office、不执行宏、不获取外链。进程限制并非完整操作系统沙箱。
- 原件和解析文本存入数据目录的 `attachments.db`，不覆盖源码；附件 ID 只在所属会话可读取。仅上传不会调用模型；发送任务时会将所选附件的有界文本片段提供给当前模型，模型可通过 `read_attachment` 按行分页继续读取。文档内容按不可信参考资料处理，不作为操作授权。取消勾选只是取消本次引用，不删除本地原件。

CLI 可通过重复 `--attach` 导入用户指定的文件（允许工作区外路径），并带问题启动任务：

```sh
conda activate Lang
python -m soranimi "总结需求并提取待办" --attach ./requirements.pdf --attach ./slides.pptx
```

工作区内的 PDF/DOCX/PPTX 也可通过现有 `read_file` 工具解析，不授予对二进制原件的编辑资格。解析接口参考：[pypdf](https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html)、[python-docx](https://python-docx.readthedocs.io/en/stable/api/document.html)、[python-pptx](https://python-pptx.readthedocs.io/en/latest/user/notes.html)。

### MCP

安装 `pip install -e ".[mcp]"` 后，显式通过 `--mcp 配置.json` 加载服务器：

```json
{
  "mcpServers": {
    "local": {
      "transport": "stdio",
      "command": "python",
      "args": ["path/to/server.py"]
    },
    "remote": {
      "transport": "http",
      "url": "https://your-server.example/mcp"
    }
  }
}
```

这只是配置格式示例，请替换为自己的服务器。stdio 命令会在加载配置时启动，配置文件须来自可信来源。
认证可在 `headers` 或 `env` 中显式配置；不要将真实凭据提交到仓库。外部工具使用 `mcp_` 前缀，调用始终经过审批。

### Git、图片、记忆与仓库地图

内置 `git_status`、`git_diff`、`git_commit`，提交必须指定文件并审批，避免包含无关已暂存内容。不会自动初始化仓库或自动提交。
`read_file` 可以返回 PNG/JPEG/WebP/GIF 内容块（需要模型支持图片）；`repo_map` 使用 Python AST 提取定义/导入并以 PageRank 排序。
`remember_memory` 和 `recall_memory` 保存/查询跨会话项目偏好，采用本地 SQLite 关键词检索。

### 独立规划与摘要模型

启用 `--subagents` 后，主 Agent 还可以调用 `plan_and_execute`：独立 planner 生成研究问题，executor 并行派发只读子 Agent，replanner 根据累计结果调整下一轮问题或返回总结。默认最多 3 轮，可在工具参数中指定 1–5 轮；每轮不超过 6 个问题，受 `max_parallel` 限制。返回值包含研究结果、轮数、完成标记和剩余问题；预算耗尽或子任务失败时保留部分结果。此流程用于研究和任务分解，主 Agent 继续负责编辑和测试。

自动压缩、`compact` 工具及 CLI `/compact` 支持单独使用摘要模型，省略时沿用主模型：

```sh
python -m soranimi --subagents --summary-model deepseek:deepseek-chat
# 摘要模型使用独立的 OpenAI 兼容端点
python -m soranimi --summary-model openai:summary-model --summary-base-url http://localhost:11434/v1
```

也可在工作区 `.env` 中设置 `SORANIMI_SUMMARY_MODEL`、`SORANIMI_SUMMARY_BASE_URL`。摘要模型使用现有的提供商 Key 配置，API 地址独立于主模型；未指定摘要地址时使用对应提供商默认地址。客户端在首次压缩时才创建，空摘要或调用失败会保留原检查点历史。上述参数同时适用于 `--ui` 和独立 `soranimi-ui` 入口；演示模式继续使用离线模型。

## 数据与开发验证

默认数据在工作区 `.soranimi/`，可用 `--data-dir` 指定。包含：

- `checkpoints.db`：图状态、消息和审批检查点。
- `sessions.db`：会话元信息；`files.db`：原文件快照和读取哈希。
- `memory.db`：跨会话偏好；`transcripts/*.jsonl`：压缩前的对话归档。
- `tool-events.jsonl`：工具名、调用 ID、耗时和状态；不重复记录工具参数和内容。

会话消息、读取的代码和快照以本地明文存储；`.env` 和 `.soranimi/` 已加入忽略规则。模型服务会接收提供给它的提示和工具结果。

```powershell
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m pip check
```

测试不需要付费模型 Key，覆盖完整工具循环、权限审批恢复、编码与行尾、外部改动冲突、跨进程恢复、上下文压缩、并发子图、真实 stdio MCP、Git 定向提交及 CLI。
本仓库按原有四层结构组织：`cli/` → `agent/` → `tools/` → `sandbox/`；状态和资源由 `runtime.AgentSession` 统一管理。
