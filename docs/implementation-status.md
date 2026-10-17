# 实现状态与后续路线

依据 `coding-agent-plan.md`，2026-10-16 将原有空模块推进为可安装、可运行、可测试的版本。
原技术方案保留为设计参考；本文件区分已经接通的功能和仍需推进的项目。

## Web 工作台（2026-10-16）

已增加本地 Web 工作台，保留 CLI。通过 `python -m soranimi --ui` 或 `start-ui.ps1` 启动；开发、运行和验证使用 `F:\miniconda3\envs\Lang`。

界面包含深色/浅色主题、会话创建与搜索、聊天和 SSE 流式回复、工具活动、任务计划、逐项审批、可恢复暂停、文件差异、撤销、文件预览与引用。
UI 复用现有 AgentSession 与 SQLite，审批和暂停隔离在不同图节点。旧 CLI 会话在 UI 中按手动审批导入。
静态资源随 Python 包分发，无 npm 构建或 CDN 依赖，服务仅监听本机。操作说明见 [UI 指南](ui-guide.md)。

新增 Web 集成测试覆盖真实 HTTP/SSE、审批前无副作用、拒绝与恢复、差异/撤销、文件路径边界、并发互斥、暂停跨服务恢复和暂停后仍须审批。
浏览器实际验证了任务发送与工具结果、文件预览/引用、搜索、设置、主题和刷新恢复。核心回归与新增测试合计 **79 passed, 1 skipped**；符号链接测试仍受当前 Windows 权限限制。

当前 Lang 环境使用 LangGraph 1.1.2，已将支持范围扩展为 `>=1.0,<1.3`。下方原始 CLI 里程碑与 67 项结果为上一轮记录。

## 原始实施路线进度

| 阶段 | 当前能力 | 尚待完成或验证 |
| --- | --- | --- |
| P1 | Python 打包、Typer CLI、LangGraph 工具循环、七个核心工具、流式 Markdown、离线演示 | 真实模型上的任务成功率评估 |
| P2 | 完整读取后编辑、内容哈希防过期、原子写入、磁盘快照、撤销和 diff、四种权限、审批中断恢复、三层上下文压缩、SQLite、分层 AGENTS.md、补全 | 终端 Esc 双击回滚；当前使用 Ctrl+C 中断、`/undo` 撤销 |
| P3 | OpenAI/Anthropic/DeepSeek 适配、OpenAI 兼容地址、Docker 执行器与镜像、结构化工具日志、跨会话偏好、检查点列表与回溯 | Docker daemon 未运行，隔离仅检查了命令构造；真实 API 与 PostgreSQL 未验证；摘要暂使用主模型；长期记忆是关键词查询，未接向量搜索 |
| P4 | 独立上下文只读子 Agent、LangGraph Send 并发与结果聚合、`update_plan`、MCP stdio/HTTP 适配、图像读取、Git 状态/diff/定向提交、Python AST + PageRank 地图 | 子 Agent 隔离写入与冲突合并；独立 planner/replanner 图；自动 commit/revert 和分支管理；懒加载 MCP/BM25；Playwright；WebSearch/WebFetch；多语言 tree-sitter 地图与性能基准 |

## 当前实现的边界

- 子 Agent 默认为关闭，`--subagents` 开启。只允许研究和读取，深度为 1，并行上限 6；主 Agent 负责修改与测试。
- `update_plan` 的状态保存在图中，由主模型执行和更新。`subagent/plan_execute.py` 实现 Send fan-out，不把它等同于独立的 planner → executor → replanner 系统。
- `/rewind` 仅接受完整轮次的检查点，避免回放已经产生外部影响的工具；它不修改磁盘文件。`/undo` 负责文件恢复。
- Git 提交必须通过工具审批并指定文件，不会主动提交整个工作区。
- MCP 适配器选择已安装并实际验证的 `langchain-mcp-adapters` API；启用配置代表启动用户明确配置的服务器，工具调用仍经过审批。
- 工具调用失败会回传给模型修正，不会自动重跑存在副作用的命令。网络模型客户端仅做提供商支持的有限重试。
- 文件快照覆盖 edit/write 工具，不追踪 bash、Git、MCP 对文件或外部服务造成的全部影响。
- subprocess 没有操作系统隔离；权限分类是保守的执行审批，不是完整 shell 语法分析器。
- 本次没有初始化 Git 仓库、连接用户外部账号或调用付费模型。

## 验证方法

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m soranimi --demo --non-interactive
```

集成测试使用可控制的 LangChain 模型，实际运行 LangGraph、SQLite、文件工具、子进程、Git 和 stdio MCP。
这能验证框架和副作用处理；不代表已经验证真实模型的工具调用质量或 Docker daemon 中的实际隔离。

本次最终验证：**67 passed, 1 skipped**。跳过项为 Windows 当前权限下无法创建符号链接；路径越界、保护目录和大小写变体另有通过的测试。
Ruff 检查、格式检查、编译检查、`pip check` 和离线 CLI 演示通过。

## API 参考

- [LangGraph 流式模式](https://docs.langchain.com/oss/python/langgraph/streaming)：使用兼容的 `stream(messages/custom/updates)`；event streaming 可独立评估。
- [LangGraph 中断](https://docs.langchain.com/oss/python/langgraph/interrupts)：审批放在每次执行之前，一个图步骤只执行一个工具，避免恢复时重放同一批中的前序工具。
- [MCP adapters 源码](https://github.com/langchain-ai/langchain-mcp-adapters)：外部工具在独立事件循环运行，并以同步工具接入当前 CLI。
