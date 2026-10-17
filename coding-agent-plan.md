# 用 LangChain 与 LangGraph 构建编程 Agent 的详细规划

> 技术方案 / Technical Proposal
>
> 从零设计一个类似 Claude Code、OpenAI Codex 的命令行编程智能体，覆盖架构设计、核心工具实现、上下文管理、安全沙箱、流式交互到分阶段实施路线的完整工程方案。

---

## 目录

1. [项目概述与定位](#1-项目概述与定位)
2. [设计目标与原则](#2-设计目标与原则)
3. [技术栈选型](#3-技术栈选型)
4. [整体架构设计](#4-整体架构设计)
5. [Agent Loop 核心设计](#5-agent-loop-核心设计)
6. [工具集设计](#6-工具集设计)
7. [文件编辑工具实现](#7-文件编辑工具实现)
8. [上下文管理策略](#8-上下文管理策略)
9. [权限控制与安全沙箱](#9-权限控制与安全沙箱)
10. [流式输出与 CLI 交互](#10-流式输出与-cli-交互)
11. [持久化与记忆](#11-持久化与记忆)
12. [项目指令文件](#12-项目指令文件)
13. [实施路线图](#13-实施路线图)
14. [关键技术难点](#14-关键技术难点)

---

## 1. 项目概述与定位

Claude Code 与 OpenAI Codex 代表了当前编程 Agent 的最高水平，它们的共同核心是一个 **agentic loop**（智能体循环）：接收用户指令后，模型在"收集上下文 → 执行操作 → 验证结果"三阶段间反复循环，直到任务完成 [1][2]。模型而非硬编码规则驱动每一次工具调用的决策——何时读文件、何时执行命令、何时结束，都由模型推理决定。

本方案的目标是用 **LangChain** 和 **LangGraph** 复刻这一架构，构建一个 Python 原生的命令行编程 Agent。LangGraph 提供了构建这类循环式 Agent 所需的全部基础设施：StateGraph 状态机、工具调用循环、人机交互中断（interrupt）、检查点持久化（checkpointer）以及流式输出 [3]。LangChain 则提供工具定义抽象和模型集成层。2025-2026 年间，LangChain 生态已明确分层——LangChain 负责 Agent 抽象与集成，LangGraph 作为底层编排运行时专注持久化执行与流式处理 [4]。

> **定位**：本 Agent 定位为**开源、可扩展、本地优先**的命令行编程助手。它不是一个云端服务，而是在用户终端中运行、直接操作本地文件系统的工具型 Agent，类似于 Aider 和 Claude Code 的产品形态。核心差异化在于：基于 LangGraph 的持久化执行让会话可中断、可恢复、可回溯，这是 Aider 等基于简单循环的工具不具备的能力。

### 对标产品的核心能力

| 能力维度 | Claude Code | OpenAI Codex CLI | 本方案目标 |
|----------|-------------|-------------------|------------|
| Agent Loop | gather → act → verify 循环 | inference ↔ tool_call 迭代 | LangGraph StateGraph 条件边循环 |
| 文件编辑 | str_replace 精确替换 | apply_patch（Lark 文法解析） | str_replace + 唯一性校验 |
| 沙箱 | macOS Seatbelt + checkpoints | bubblewrap/Seatbelt + 命令禁用清单 | Docker / subprocess 分级 |
| 上下文管理 | 三层自动压缩 + subagent 隔离 | 自动 compaction + SQLite thread | 三层压缩 + trim_messages |
| 持久化 | JSONL 会话日志 | SQLite thread（可 resume/fork） | LangGraph Checkpointer |
| 子任务分解 | Agent 工具派生子 agent | spawn_agent / wait_agent | LangGraph Send + 子图 |

---

## 2. 设计目标与原则

构建一个能真正干活的编程 Agent，工具接口的设计质量比工具数量更重要。SWE-agent 的研究表明，仅用约 100 行代码的精简工具接口就能在 SWE-bench 上达到 74% 以上的表现 [5]。以下原则贯穿整个设计。

**模型驱动决策**：何时调用工具、调用哪个工具、何时结束，全部由模型推理决定，不使用硬编码的启发式规则。Agent 的智能来自模型，框架只负责执行和编排。

**工具循环自治**：模型反复调用工具直到任务完成，而非单轮问答。循环由条件边驱动，设有 recursion_limit 防止失控，但允许足够多的迭代完成复杂任务。

**精确而非暴力**：文件编辑采用字符串精确替换而非整文件重写，节省 token、降低误改风险。每个编辑操作都做唯一性校验，确保改对地方。

**安全默认值**：文件系统默认可写范围受限，危险命令需用户确认，编辑前快照可回滚。权限分级从严格到宽松，用户按需放开。

**上下文即工程**：上下文窗口是固定预算，决定模型看到什么信息是 Agent 性能的关键。采用多层压缩策略，策略性遗忘而非硬截断。

**可扩展架构**：工具声明式定义、模型可切换、系统提示词可定制。通过 MCP 协议接入外部工具，支持垂直领域扩展。

> **关键约束**：Anthropic 的工程实践表明，**agentic search 优先于 semantic search**——先让 Agent 用 grep/glob 自主决定如何加载文件，文件夹结构本身就是一种 context engineering [6]。本方案不预先向量化代码库，而是依赖 Agent 自主探索。

---

## 3. 技术栈选型

技术选型围绕一个核心目标：用最少的自研代码复现 Claude Code 级别的 Agent 能力，把持久化、流式、循环控制等基础设施交给成熟框架。

### 核心框架

| 层级 | 选型 | 选型理由 |
|------|------|----------|
| Agent 编排 | **LangGraph** | StateGraph 状态机天然适合循环式 Agent；内置 checkpointer 持久化、interrupt 人机交互、stream_events v3 流式输出 [3] |
| 工具与模型 | **LangChain** | `@tool` 装饰器 + `bind_tools` 统一工具定义与绑定；支持 30+ 模型提供商 [7] |
| 模型适配 | LangChain ChatModel | 统一接口切换 Claude / GPT / DeepSeek / 本地模型，无需改工具代码 |
| CLI 渲染 | **Rich** | Markdown 渲染、代码高亮（Syntax）、Live 流式更新，终端输出体验最佳 [8] |
| CLI 输入 | **prompt_toolkit** | 多行编辑、历史记录、自动补全、斜杠命令，GNU readline 的现代替代 [9] |
| 命令入口 | Typer | 基于 Click，命令定义极简，自动生成帮助文档 |
| 内容搜索 | ripgrep (rg) | Grep 工具底层引擎，性能远超 grep，支持正则与文件类型过滤 |
| 沙箱（可选） | Docker | Bash 工具的可选隔离环境，namespace + cgroup 隔离，适合运行不可信代码 |
| 持久化 | SQLite / PostgreSQL | LangGraph 的 SqliteSaver（本地）或 PostgresSaver（生产）做 checkpointer [10] |

> **为什么选 LangGraph 而非自研循环**：自研一个 `while stop_reason == "tool_use"` 的循环看似简单，但要同时处理持久化中断恢复、流式 token 输出、并行工具调用、状态回溯、多 Agent 编排，工程量巨大。LangGraph 把这些能力封装为图的原语——条件边实现循环、checkpointer 实现持久化、interrupt 实现人机交互、Send 实现并行 fan-out [3]。使用 LangGraph 相当于站在 Apache Beam / Google Pregel 同级的分布式计算抽象上构建 Agent。

---

## 4. 整体架构设计

系统采用四层架构，自上而下依次为 CLI 交互层、Agent 编排层、工具层、执行环境层。每层职责清晰、边界明确，上层依赖下层抽象而非实现细节。

```
┌─────────────────────────────────────────────────────┐
│                   CLI 交互层                         │
│   prompt_toolkit 输入 | Rich 渲染输出 | 斜杠命令/审批  │
├─────────────────────────────────────────────────────┤
│              Agent 编排层 — LangGraph                │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │StateGraph│  │agentic loop│  │ interrupt 人机交互│  │
│  │  状态机  │  │llm↔tool   │  │   上下文压缩/trim │  │
│  └──────────┘  └───────────┘  └──────────────────┘  │
├─────────────────────────────────────────────────────┤
│           工具层 — LangChain @tool                   │
│  read_file | edit_file | write_file | glob          │
│  grep      | bash     | list_dir                    │
├─────────────────────────────────────────────────────┤
│                  执行环境层                          │
│  本地文件系统 | Shell/沙箱 | ripgrep | Checkpointer  │
└─────────────────────────────────────────────────────┘
```

### 各层职责

**CLI 交互层**：负责用户输入接收与输出渲染。prompt_toolkit 处理多行输入、历史记录、斜杠命令补全（如 `/help`、`/compact`、`/undo`）；Rich 的 Live 组件实时渲染模型流式输出的 Markdown 和代码块。当 Agent 请求执行危险操作时，这一层弹出审批提示等待用户确认。

**Agent 编排层**：系统的核心，由 LangGraph StateGraph 驱动。它管理对话状态（消息列表）、调度模型调用、路由工具执行、处理人机交互中断。agentic loop 是一个条件边循环：模型输出含 `tool_calls` 时路由到工具节点，工具执行完毕后回到模型节点，直到模型输出不含工具调用时结束。上下文管理模块在每轮循环中监控 token 用量，必要时触发压缩。

**工具层**：7 个核心工具，每个用 LangChain 的 `@tool` 装饰器定义，带有类型注解和 docstring。工具的 docstring 就是给模型看的说明文档——Claude Code 的实践表明，详尽的工具描述本身就是工程文档 [11]。工具层只负责执行单一操作并返回结构化结果，不包含业务逻辑。

**执行环境层**：底层资源访问。文件系统操作直接调用 Python 标准库（`pathlib`、`os`）；内容搜索调用 ripgrep 子进程；命令执行通过 `subprocess` 或 Docker 沙箱；Checkpointer 负责会话状态持久化到 SQLite 或 PostgreSQL。

---

## 5. Agent Loop 核心设计

agentic loop 是整个系统的引擎。Claude Code 和 Codex 的底层协议相同：当 `stop_reason == "tool_use"` 时执行工具，把 `tool_result` 追加为新的 user 消息，继续采样；循环在 `end_turn` 时退出 [12]。LangGraph 用 StateGraph 的条件边优雅地实现了这一模式。

```
START → llm_call → {有 tool_calls?} ──是──→ tool_node → context_check → llm_call
                      │                              │
                      否                             │
                      ↓                              │
                     END ←─── 危险操作 → interrupt(等待确认) → tool_node
```

### StateGraph 实现

状态使用 `MessagesState`（内置 `messages` 字段 + `add_messages` reducer），消息按 ID 去重覆盖，支持人机交互编辑历史消息 [3]。

```python
from typing import Annotated, Literal
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite import SqliteSaver

# 绑定工具到模型
model = ChatAnthropic(model="claude-sonnet-4-5")
model_with_tools = model.bind_tools(tools)

def llm_call(state: MessagesState):
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response]}

def should_continue(state: MessagesState) -> Literal["tools", END]:
    last = state["messages"][-1]
    return "tools" if last.tool_calls else END

# 构建图
builder = StateGraph(MessagesState)
builder.add_node("llm_call", llm_call)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "llm_call")
builder.add_conditional_edges("llm_call", should_continue, ["tools", END])
builder.add_edge("tools", "llm_call")  # 关键：回边形成循环

# 编译时注入 checkpointer，启用持久化
with SqliteSaver.from_conn_string("agent.db") as checkpointer:
    checkpointer.setup()
    agent = builder.compile(checkpointer=checkpointer)
```

这段代码的核心是 `should_continue` 条件边：它检查模型最新输出是否包含 `tool_calls`，有则路由到工具节点，无则结束。`tools → llm_call` 的回边形成了循环。`ToolNode` 是 LangGraph 预置节点，自动处理并行工具调用、错误捕获和 ToolMessage 回填 [13]。

### 防止无限循环

LangGraph 内置 `recursion_limit`（默认 25），限制图的最大迭代步数。对于复杂编程任务，可在编译或调用时调整：

```python
agent = builder.compile(checkpointer=checkpointer, recursion_limit=50)
# 或运行时
result = agent.invoke(
    {"messages": [HumanMessage(content="重构 auth 模块")]},
    config={"configurable": {"thread_id": "session-001"},
            "recursion_limit": 50}
)
```

超出限制时 LangGraph 抛出 `GraphRecursionError`，CLI 层捕获后提示用户"任务过于复杂，请拆分为更小的子任务"。在节点内可通过 runtime metadata 读取当前步数，实现主动式控制——例如第 40 步后注入一条提示消息引导模型收尾。

### 两种实现路径

除了 Graph API，LangGraph 还提供 **Functional API**（`@entrypoint` / `@task`），把控制流交还给 Python 原生语法 [14]。对于需要复杂分支逻辑的场景更直观：

```python
from langgraph.func import entrypoint, task

@entrypoint(checkpointer=checkpointer)
def coding_agent(messages):
    response = call_llm(messages).result()
    while True:
        if not response.tool_calls:
            break
        results = [call_tool(tc).result() for tc in response.tool_calls]
        messages = add_messages(messages, [response, *results])
        # 上下文压缩检查
        messages = maybe_compact(messages)
        response = call_llm(messages).result()
    return response
```

本方案推荐**先用 Graph API 起步**——它的条件边模型与 agentic loop 的语义高度吻合，且 `ToolNode` 开箱即用。需要插入自定义逻辑（如上下文压缩、权限检查）时，再在图中增加中间节点，无需切换到 Functional API。

---

## 6. 工具集设计

编程 Agent 的工具集宜精不宜多。参考 Claude Code 和 SWE-agent 的实践，6-7 个高质量工具足以覆盖绝大多数编程任务 [5][11]。每个工具有单一清晰的职责，输出格式结构化便于模型解析。

| 工具 | 核心参数 | 返回值 | 设计要点 |
|------|----------|--------|----------|
| `read_file` | path, offset?, limit? | 带行号的内容 | `cat -n` 风格行号；支持分页避免撑爆上下文；大文件截断单行 |
| `edit_file` | path, old_string, new_string | 成功/失败信息 | str_replace 精确替换；唯一性校验；先读后写约束（详见第 7 节） |
| `write_file` | path, content | 成功信息 | 新建或整文件覆盖；编码检测（UTF-8/LF）；目录不存在时自动创建 |
| `glob` | pattern, path? | 文件路径列表 | 按修改时间排序；支持 `**/*.py` 递归匹配；忽略 `.gitignore` |
| `grep` | pattern, path?, glob? | 匹配行+文件路径 | 底层调 ripgrep；支持正则、文件类型过滤；三种输出模式 |
| `bash` | command, timeout? | stdout + stderr + exit_code | 工作目录持久、shell 状态不持久；超时默认 120s；输出截断 |
| `list_dir` | path | 文件/目录列表 | 区分文件与目录；支持 ignore 模式；快速了解项目结构 |

### 工具定义示例

LangChain 的 `@tool` 装饰器自动从类型注解生成输入 schema，docstring 成为模型看到的工具描述 [7]。工具名用 `snake_case`，避免空格和特殊字符。

```python
from langchain_core.tools import tool
from pathlib import Path

@tool
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """读取文件内容并返回带行号的文本。

    行号格式为 "行号:内容"，用于后续 edit_file 工具精确定位。
    对于大文件，使用 offset 和 limit 参数分页读取。
    支持 PNG/JPG 图片（以多模态方式返回）。

    Args:
        path: 文件的绝对或相对路径
        offset: 起始行号（从 0 开始），默认 0
        limit: 读取的最大行数，默认 2000
    """
    p = Path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    lines = p.read_text(encoding="utf-8").splitlines()
    selected = lines[offset:offset + limit]
    numbered = "\n".join(f"{i + offset + 1}: {line}" for i, line in enumerate(selected))
    return numbered
```

### 工具描述的工程化

工具描述不是写给人看的注释，而是写给模型看的操作手册。Claude Code 的每个工具描述本身就是数百字的工程文档，包含使用场景、参数说明、边界情况、错误处理指引 [11]。好的工具描述能显著减少模型的误用率。例如 `edit_file` 的描述应明确告知模型：必须先调用 `read_file` 读取目标文件，`old_string` 必须在文件中唯一匹配，编辑失败时应该重新读取文件而非盲目重试。

> **工具输出设计**：工具返回值的格式直接影响模型的下一步决策。`bash` 工具应返回结构化的 `{stdout, stderr, exit_code}` 而非拼接的纯文本，让模型能区分正常输出和错误。`grep` 工具返回匹配行时附上文件路径和行号，方便模型随后用 `read_file` 读取上下文。所有工具的超长输出应截断并附加 `[truncated: N more lines]` 提示。

---

## 7. 文件编辑工具实现

`edit_file` 是整个系统中工程复杂度最高的工具。Claude Code 的 Edit 工具看似只是 `str.replace()`，实则背后是一条多阶段处理管线 [15]。本方案参考其设计，实现一个健壮的精确编辑工具。

### 核心接口

```python
@tool
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """对文件做精确字符串替换。

    要求：
    1. 必须先调用 read_file 读取该文件的完整内容
    2. old_string 必须在文件中唯一匹配（否则报错）
    3. old_string 和 new_string 不能相同
    4. 空的 new_string 表示删除 old_string

    失败时会返回详细错误信息，请根据提示修正后重试。
    """
```

不传行号、不打 AST patch——让模型输出"原文"和"替换后文本"，由工具层完成实际写入 [15]。模型无需精确计算行号，降低出错率；工具层做唯一性校验和原子写入。

### 三阶段处理管线

```
阶段一：预处理                    阶段二：校验                      阶段三：写入
┌─────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ 反脱敏           │         │ 弯引号归一化      │         │ 竞态检查 2        │
│ 还原 API 缩写   │──────→  │ 直引号 ↔ 弯引号  │──────→  │ 原子再验证       │
│ 去行号前缀       │         │ 竞态检查 1        │         │ 恢复原始编码      │
│ 剥离 read_file  │         │ 文件是否已变更    │         │ 与行尾格式       │
│ 行号            │         │ 唯一性校验        │         │ 写入磁盘         │
│                 │         │ old_string 匹配数 │         │                  │
└─────────────────┘         └──────────────────┘         └──────────────────┘
```

**阶段一：预处理**：模型从 `read_file` 的输出中复制文本时，常把行号前缀（如 `42:\tfunction foo()`）一起带入 `old_string`。用正则匹配并剥离两种行号格式。部分模型 API 会将 XML 标签缩写（如 `<function_results>` → `<fnr>`），需对 `old_string` 和 `new_string` 应用反向映射恢复原文 [15]。

**阶段二：校验**：模型偶尔产出弯引号（`" "` `' '`）而非直引号。处理流程：先精确匹配 → 失败则归一化引号后重试 → 成功后对 `new_string` 反向应用文件原有引号风格，保持代码风格一致 [15]。唯一性校验检查 `old_string` 在文件中的匹配数，若大于 1 则报错要求模型提供更长、更唯一的上下文。竞态检查 1 异步比较文件 mtime 与上次读取时间戳，若已变更则不展示注定失败的编辑，避免浪费用户时间。

**阶段三：写入**：用户批准后，同步、原子地再次比较 mtime 和内容——代码注释明确警告"此处与写盘之间禁止 await，以保持原子性" [15]。写入时恢复原始编码（UTF-8/UTF-16LE）和行尾格式（LF/CRLF）。

> **先读后写硬约束**：`edit_file` 强制要求目标文件必须先被 `read_file` 完整读取过。若读取记录为空则直接拒绝；若读取时使用了 offset/limit（部分读取）也拒绝——因为模型可能未看到完整上下文，`old_string` 可能不唯一 [15]。这一约束显著降低了误改风险。

### 编辑失败的多级回退

参考 Aider 的策略 [16]，编辑失败时按以下顺序尝试：

| 回退级别 | 策略 | 说明 |
|----------|------|------|
| 1 | 精确匹配 | 严格字节级 `str.replace`，要求唯一匹配 |
| 2 | 空白弹性匹配 | 忽略缩进和空格差异，按非空白字符序列匹配 |
| 3 | 引号归一化 | 弯引号归一化为直引号后重试 |
| 4 | 报错引导 | 返回"未找到匹配，文件可能已变更，请重新 read_file"的错误 |

失败反馈必须具体——不只是"编辑失败"，而是告知匹配数（0 次还是多次），引导模型自我修正。Claude Code 的实践表明，清晰的错误反馈能让模型在下一轮调用中自动调整 `old_string`，多数情况下无需人工干预。

---

## 8. 上下文管理策略

LLM 上下文窗口是固定预算，系统提示、对话历史、工具输出、模型响应共享同一预算。读一个 1000 行文件约占 4000 token，读 30 个文件、跑 20 条命令即可突破 100k 上限 [17]。自 2018 年来窗口增长 20000 倍，但长上下文会引发注意力稀释——"更大不等于更好"。上下文管理的核心思想是**策略性遗忘而非硬截断**。

### 三层压缩策略

参考 Claude Code 的工业级方案 [18]，完整历史永久保存在磁盘（checkpointer），内存中按三层策略压缩：

- **micro_compact（每轮静默执行）**：只保留最近 3 个工具调用的完整结果。更早的工具结果若超 100 字符，替换为 `[Previous: used {tool_name}]` 占位符。保留工具名让模型知道之前做过什么。一轮即可减少数千到数万 token，覆盖 90% 场景。

- **auto_compact（阈值触发）**：token 超 50000 时触发：先把完整对话存到磁盘 JSONL（永不丢失），再调用 LLM 生成摘要（强制包含已完成工作、当前状态、关键决策三要素），将整个对话替换为两条消息，压缩比可达 10:1 以上。

- **compact 工具（模型主动调用）**：模型可在需要时手动触发压缩，支持 `focus` 参数指定重点保留的信息。与 auto_compact 共用摘要逻辑，但由模型而非框架决定时机。

- **token 估算**：用简单规则 `len(str(messages)) // 4` 估算 token（4 字符约等于 1 token），无需引入 tiktoken，性能极高且足够触发阈值判断 [18]。

### LangGraph 中的实现

LangGraph 提供 `trim_messages` 和 `summarize_messages` 两个工具管理短期记忆 [10]。在 agentic loop 中插入一个 `context_check` 节点，在工具执行后、模型调用前检查 token 用量：

```python
from langchain_core.messages import trim_messages

def context_check(state: MessagesState):
    messages = state["messages"]
    token_est = sum(len(str(m)) for m in messages) // 4

    if token_est > 50000:
        # 触发 auto_compact：摘要历史
        summary = summarize_history(messages)
        messages = [HumanMessage(content=f"[对话摘要]\n{summary}"),
                    messages[-3:]]  # 保留最近 3 条
    else:
        # micro_compact：替换旧工具输出
        messages = replace_old_tool_outputs(messages, keep_recent=3)

    return {"messages": messages}
```

### 预算分配原则

上下文预算不是平均分配的。一个合理的分配方案：

| 预算区域 | 占比 | 内容 |
|----------|------|------|
| 系统提示 + 工具定义 | 15-20% | 固定开销，工具描述 + AGENTS.md 指令 |
| 当前任务上下文 | 40-50% | 用户指令、相关文件内容、最近工具输出 |
| 对话历史 | 20-25% | 摘要后的历史决策与已完成工作 |
| 模型输出预留 | 15-20% | 至少保留 2000-4000 token 给模型输出，否则函数尾部会被截断 [19] |

---

## 9. 权限控制与安全沙箱

编程 Agent 能读写文件、执行命令，这既是能力也是风险。Claude Code 采用动态交互式权限确认，Codex 采用静态声明式规则文件 [20][21]。本方案融合两者：多档权限模式 + 命令分类审批 + 沙箱隔离 + 编辑前快照。

### 权限模式

| 模式 | 文件编辑 | 命令执行 | 适用场景 |
|------|----------|----------|----------|
| **Manual** | 每次确认 | 每次确认 | 不熟悉的项目、生产环境 |
| **Accept Edits** | 自动执行 | 只读命令自动、写命令确认 | 日常开发（推荐默认） |
| **Plan** | 禁止 | 只读命令允许 | 探索代码库、制定方案 |
| **Auto** | 自动 | 自动（危险命令除外） | 信任的沙箱环境 |

### 命令分类审批

不是所有命令都需要确认。参考 Claude Code 的只读命令集和 Codex 的硬编码禁用清单 [11][21]：

| 类别 | 示例 | 处理方式 |
|------|------|----------|
| 只读命令 | `ls`, `cat`, `git status`, `git log`, `rg` | 所有模式自动执行 |
| 构建/测试 | `npm test`, `pytest`, `cargo build` | Accept Edits 及以上自动执行 |
| 写命令 | `git commit`, `npm install`, `mkdir` | 需确认（可预批准） |
| 危险命令 | `rm -rf`, `git push --force`, `curl \| sh` | 所有模式强制确认 |
| 硬编码禁用 | `bash -c`, `python -e`, `sudo`, `eval` | 直接拒绝（绕过审查的任意执行） |

### 人机交互中断

LangGraph 的 `interrupt()` 函数是实现权限确认的原语 [22]。在 `bash` 和 `edit_file` 工具内部调用 `interrupt()`，暂停图执行并把操作详情抛给调用方，用户确认后通过 `Command(resume=True)` 恢复：

```python
from langgraph.types import interrupt, Command

@tool
def bash(command: str, timeout: int = 120) -> str:
    """执行 shell 命令。"""
    if needs_approval(command):
        approved = interrupt({
            "type": "bash",
            "command": command,
            "risk": classify_risk(command),
        })
        if not approved:
            return "Command cancelled by user."

    result = execute_command(command, timeout)
    return format(result)
```

`interrupt()` 不能被 try/except 包裹，恢复时节点从头重跑，因此 interrupt 前的 side-effect 必须幂等 [22]。

### 沙箱方案

`bash` 工具的执行环境按信任级别选择：

| 方案 | 隔离机制 | 启动延迟 | 安全性 | 适用场景 |
|------|----------|----------|--------|----------|
| subprocess | 无隔离 | 即时 | 低 | 个人工具、受信任环境（默认） |
| Docker | namespace + cgroup | ~500ms | 中 | 本地开发、运行不可信代码 |
| E2B | Firecracker microVM | ~150ms | 高 | 云原生、多租户（可选集成） |

默认使用 subprocess（零部署成本），通过配置切换到 Docker。Docker 方案中挂载项目目录为可写，其余文件系统只读，断网执行防止数据外泄。

### 文件改动回滚

每次 `edit_file` 或 `write_file` 前快照当前文件内容到内存或临时目录。用户按两次 `Esc` 可回退到先前状态，或让 Agent 撤销最近的改动。这是 Claude Code 的 checkpoints 机制 [20]，独立于 git，会话恢复后仍可用。但回滚只覆盖文件改动——影响外部系统（数据库、API、部署）的操作无法回滚，因此这类命令执行前必须征求用户同意。

---

## 10. 流式输出与 CLI 交互

编程 Agent 的终端体验决定了它的可用性。用户需要看到模型正在"思考"什么、调用了哪个工具、工具输出是什么——这一切必须实时呈现，而非等整个循环结束后一次性吐出。token 逐步生成时即可开始渲染，这就是流式输出的来源 [2]。

### LangGraph 流式 API

LangGraph 的 `stream_events(version="v3")` 提供类型化的流式投影，可独立消费 token 流、状态快照和中断事件 [23]：

```python
config = {"configurable": {"thread_id": "session-001"}}
stream = agent.stream_events(
    {"messages": [HumanMessage(content="修复登录 bug")]},
    config=config,
    version="v3"
)

# 消费 token 流——实时渲染到终端
for message in stream.messages:
    for token in message.text:
        console.print(token, end="")

# 检测中断——弹出审批提示
if stream.interrupted:
    for i in stream.interrupts:
        approved = prompt_user(i.value)
        stream = agent.stream_events(
            Command(resume=approved), config=config, version="v3"
        )

final = stream.output
```

流式 API 同时返回多种事件类型：`messages` 模式提供 LLM token 流（含 metadata，可按 node/tag 过滤）；`values` 模式提供每步后的完整状态快照；`interrupts` 投影提供中断事件 [23]。CLI 层只需消费这些投影，无需在 chunk 上做分支判断。

### 终端渲染

使用 Rich 的 `Live` 组件实时渲染 Markdown，代码块用 `Syntax` 高亮 [8]。关键设计原则：让对话流进入终端正常 scrollback，文本保持可选可滚动 [24]。避免使用全屏 TUI 模式做主输出流，否则无法复制粘贴历史。

```python
from rich.live import Live
from rich.markdown import Markdown

with Live(Markdown(""), refresh_per_second=10) as live:
    buffer = ""
    for token in token_stream:
        buffer += token
        live.update(Markdown(buffer))
```

### 工具调用状态展示

模型输出含 `tool_calls` 时，流中混合返回文本块和工具调用块。需要增量解析器累积 tool_use 块的 input JSON，完整后触发工具执行。工具执行期间显示状态指示器：

| 阶段 | 终端显示 |
|------|----------|
| 模型推理中 | 逐字渲染 Markdown 输出 |
| 工具调用解析 | `Calling: read_file(path="src/auth.py")` |
| 工具执行中 | 旋转指示器 `Running: read_file...` |
| 等待审批 | 高亮提示 `Approve bash("npm test")? [y/n]` |
| 工具完成 | 折叠显示输出摘要 |

### 输入处理

prompt_toolkit 提供多行编辑（Enter 发送，Alt+Enter 换行）、历史记录、自动补全 [9]。斜杠命令（`/help`、`/compact`、`/undo`、`/diff`）和 `@file` 文件提及通过补全器实现。用户可随时按 `Esc` 中断当前 Agent 循环，提供额外上下文或要求改换思路。

---

## 11. 持久化与记忆

LangGraph 把持久化分为两层：Checkpointer 持久化图状态快照（短期、线程内），Store 持久化应用自定义 KV 数据（长期、跨会话） [10]。这一分层恰好对应编程 Agent 的两类记忆需求。

### 短期记忆：会话连续性

Checkpointer 通过 `thread_id` 维持会话连续性。复用同一 `thread_id` 则自动加载历史消息，继续上一轮对话；换新 `thread_id` 则是全新空状态 [22]。这是 Claude Code（JSONL 日志）和 Codex（SQLite thread）都具备的能力，但 LangGraph 把它做成了图的原语。

| Checkpointer | 场景 | 特点 |
|--------------|------|------|
| `InMemorySaver` | 开发测试 | 进程内，重启即丢 |
| `SqliteSaver` | 本地单机 | 文件持久化，零配置 |
| `PostgresSaver` | 生产部署 | 多实例共享，需 `setup()` 建表 |

本地工具默认使用 `SqliteSaver`，会话状态自动写入 `~/.coding-agent/sessions.db`。用户退出后重新启动，通过 `--resume <session_id>` 恢复上次会话，Agent 的完整上下文（消息历史、工具输出、中断状态）全部还原。

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.utils.uuid import uuid7

# 每次会话生成唯一 thread_id（UUID7，时间有序）
thread_id = str(uuid7())

with SqliteSaver.from_conn_string("~/.coding-agent/sessions.db") as ckpt:
    ckpt.setup()
    agent = builder.compile(checkpointer=ckpt)
    # 首次调用
    agent.invoke({"messages": [...]}, config={"configurable": {"thread_id": thread_id}})
    # 续接同一会话——自动加载历史
    agent.invoke({"messages": [HumanMessage("继续刚才的任务")]},
                 config={"configurable": {"thread_id": thread_id}})
```

### 长期记忆：跨会话偏好

Store 用于跨会话的长期记忆，如用户编码偏好、项目约定、常见错误模式 [10]。Store 支持语义搜索（向量检索），namespace 通常按用户或项目组织：

```python
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

def call_model(state, store):
    ns = ("preferences", user_id)
    # 检索相关记忆注入系统提示
    memories = store.search(ns, query=state["messages"][-1].content)
    # Agent 完成任务后写入新记忆
    store.put(ns, str(uuid7()), {"data": "用户偏好 4 空格缩进"})
```

### 会话管理功能

基于 Checkpointer 的状态快照，可以实现会话的**回溯（time travel）**——回到任意历史检查点重新执行。这意味着用户可以"撤销"Agent 的某次错误决策，从更早的状态重新开始，而非只能线性前进。Codex 的 SQLite thread 支持类似的 rollback 能力 [21]。

---

## 12. 项目指令文件

系统提示词之外，编程 Agent 需要项目级的"持久指令"——编码规范、架构约定、常见命令。Claude Code 用 `CLAUDE.md`，Codex 用 `AGENTS.md` [20][21]。LangChain 官方的实践表明，`AGENTS.md` 静态基础知识 + RAG 动态检索文档片段的组合效果最好 [25]。

### 分层加载

指令文件从多个位置分层聚合，越具体的越靠后（优先级越高）：

```
~/.coding-agent/AGENTS.md          # 用户全局偏好
<project-root>/AGENTS.md           # 项目级约定
<project-root>/src/AGENTS.md       # 子目录级约定
<cwd>/AGENTS.md                    # 当前工作目录约定
```

Codex 从 git root 到 cwd 各目录聚合 `AGENTS.md`，默认 32 KiB 限制 [21]。本方案采用相同策略，启动时读取并拼接到系统提示末尾。

### AGENTS.md 内容结构

```markdown
# 项目概述
这是一个基于 FastAPI 的电商后端，Python 3.12，使用 SQLAlchemy 2.0。

# 编码规范
- 使用 4 空格缩进，行宽 100 字符
- 所有函数必须有类型注解和 docstring
- 测试用 pytest，放在 tests/ 目录，文件名 test_*.py

# 常用命令
- 运行测试: pytest tests/ -v
- 启动开发服务器: uvicorn app.main:app --reload
- 数据库迁移: alembic upgrade head

# 架构约定
- API 层在 app/api/，业务逻辑在 app/services/
- 数据模型在 app/models/，Schema 在 app/schemas/
- 不要直接在 API 层写业务逻辑

# 注意事项
- 不要修改 app/core/security.py，它包含敏感的加密逻辑
- 数据库操作必须使用 session 上下文管理器
```

> **为什么不用对话历史存规则**：自动压缩（compaction）接近上限时会清除较早的工具输出并总结对话，早期详细指令可能丢失 [20]。持久规则必须放在 `AGENTS.md` 中，每次调用都注入系统提示，而非依赖对话历史。这也是 Claude Code 强调"持久规则放 CLAUDE.md"的原因。

---

## 13. 实施路线图

整个项目分四个阶段递进，总计约 10-13 周。每个阶段产出可独立使用的版本，前一阶段的产出是后一阶段的前置条件。核心原则是**先跑通最小闭环，再逐层加固**——不要在 Agent Loop 还没跑通时就纠结沙箱安全，也不要在单文件编辑还没稳定时就去做多 Agent 编排。

### 总览：阶段与时间线

| 阶段 | 名称 | 时长 | 核心交付物 | 前置条件 |
|------|------|------|------------|----------|
| P1 | MVP：Agent Loop + 核心工具 | 3 周 | 能读写文件、执行命令、单文件修复 | 无 |
| P2 | 上下文管理 + 权限控制 | 3 周 | 多文件任务、长会话、危险操作防护 | P1 完成 |
| P3 | 沙箱 + 多模型 + 安全加固 | 2-3 周 | 隔离执行、多模型切换、生产可用 | P2 完成 |
| P4 | 子 Agent + 高级特性 | 3-4 周 | 任务分解、多 Agent 协作、MCP 扩展 | P3 完成 |

```
Week 1-3 (P1)              Week 4-6 (P2)             Week 7-9 (P3)            Week 10-13 (P4)
┌─────────────────┐        ┌─────────────────┐       ┌────────────────┐       ┌──────────────────┐
│ Agent Loop 跑通  │──────→ │ 上下文压缩      │─────→ │ Docker 沙箱    │─────→ │ 子 Agent 编排    │
│ 7 个核心工具    │        │ 权限审批        │       │ 多模型切换     │       │ Plan-Execute     │
│ CLI 基础框架    │        │ SqliteSaver     │       │ 会话回溯       │       │ MCP 集成         │
│ 流式输出        │        │ AGENTS.md       │       │ 长期记忆       │       │ Git 集成         │
└─────────────────┘        └─────────────────┘       └────────────────┘       └──────────────────┘
     ↓ 可用版 1                ↓ 可用版 2               ↓ 可用版 3                ↓ 可用版 4
```

### 任务优先级原则

在每周的排期内，任务按以下优先级排序：

1. **P0 阻塞型**：不完成则后续任务无法启动（如 Agent Loop 搭建是所有工具测试的前置）
2. **P1 核心型**：阶段交付的必要功能（如 edit_file 是 MVP 的核心）
3. **P2 增强型**：提升体验但非阻塞（如斜杠命令补全）
4. **P3 锦上添花**：时间充裕才做（如仓库地图）

---

### P1 — MVP：基础 Agent Loop + 核心工具（3 周）

**目标**：能读取代码、执行命令、编辑文件，完成简单的单文件修改任务。这一阶段的核心是**让 Agent Loop 跑起来**——模型能调用工具、工具能返回结果、结果能驱动下一轮决策。

#### Week 1：Agent Loop 骨架 + 只读工具

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | 项目脚手架：Python 项目结构、依赖管理（pyproject.toml）、Typer 入口 | 0.5 天 | 无 |
| P0 | LangGraph StateGraph 搭建：llm_call 节点 + should_continue 条件边 + ToolNode | 1 天 | 脚手架 |
| P0 | ChatAnthropic 模型集成 + bind_tools 绑定 | 0.5 天 | StateGraph |
| P0 | `read_file` 工具：带行号返回、分页参数 | 0.5 天 | StateGraph |
| P0 | `list_dir` 工具：区分文件/目录 | 0.5 天 | 无 |
| P1 | `glob` 工具：按 pattern 匹配、按 mtime 排序 | 0.5 天 | 无 |
| P1 | `grep` 工具：调 ripgrep 子进程、三种输出模式 | 1 天 | 无 |
| P1 | 基础 CLI：prompt_toolkit 输入 + Rich 简单输出（非流式） | 1 天 | StateGraph |

**Week 1 验收**：在终端输入"列出当前目录文件并读取 README.md"，Agent 能调用 list_dir 和 read_file，返回结果。

#### Week 2：文件编辑 + 命令执行

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | `edit_file` 工具：str_replace 核心逻辑 + 唯一性校验 | 1.5 天 | read_file |
| P0 | `edit_file` 先读后写约束 + 读取状态追踪（readFileState 字典） | 0.5 天 | edit_file 核心 |
| P0 | `edit_file` 行号前缀剥离 + 弯引号归一化 | 1 天 | edit_file 核心 |
| P0 | `write_file` 工具：新建/覆盖、编码检测、目录自动创建 | 0.5 天 | 无 |
| P0 | `bash` 工具：subprocess 执行、超时控制、输出截断 | 1 天 | 无 |
| P1 | `bash` 工作目录持久化（跨多次调用保持 cwd） | 0.5 天 | bash 核心 |
| P1 | recursion_limit 配置 + GraphRecursionError 捕获 | 0.5 天 | StateGraph |

**Week 2 验收**：输入"修复 src/utils.py 中的 syntax error"，Agent 能读取文件、定位错误、精确编辑、运行 `python -c "import ast; ast.parse(open('src/utils.py').read())"` 验证修复。

#### Week 3：流式输出 + InMemorySaver + 联调打磨

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | stream_events v3 接入：token 流实时渲染到终端 | 1.5 天 | Week 1-2 |
| P0 | Rich Live 组件：Markdown 流式渲染 + 代码高亮 | 1 天 | stream_events |
| P0 | 工具调用状态展示：Calling / Running / Done 指示器 | 1 天 | Rich Live |
| P1 | InMemorySaver 持久化（进程内，支持同进程多轮对话） | 0.5 天 | StateGraph |
| P1 | 系统提示词编写：工具使用规范、编辑约束说明 | 0.5 天 | 全部工具 |
| P1 | 端到端测试：5 个典型单文件修复场景 | 1 天 | 全部 |
| P2 | 基础错误处理：工具异常捕获 + 友好错误消息 | 0.5 天 | 全部 |

**Week 3 验收（P1 里程碑）**：

| 验收任务 | 成功标准 |
|----------|----------|
| "修复 src/utils.py 中的 syntax error" | 读取文件 → 定位错误 → 精确编辑 → 运行验证 |
| "在 main.py 中添加一个 hello 函数" | 读取文件 → edit_file 插入函数 → 确认无语法错误 |
| "找出所有 TODO 注释" | grep 搜索 → 列出结果带文件路径和行号 |

---

### P2 — 上下文管理 + 权限控制（3 周）

**目标**：能处理多文件复杂任务，长会话不崩溃，危险操作有防护。这一阶段的核心是**让 Agent 安全地长时间运行**——上下文不爆炸、危险操作有确认、会话可恢复。

#### Week 4：上下文压缩 + token 监控

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | token 估算函数：`len(str(messages)) // 4` | 0.5 天 | P1 |
| P0 | context_check 节点：插入 tools → llm_call 之间 | 0.5 天 | token 估算 |
| P0 | micro_compact：替换旧工具输出为占位符（保留最近 3 个） | 1 天 | context_check |
| P0 | auto_compact：超 50000 token 时调用 LLM 生成摘要 | 1.5 天 | micro_compact |
| P1 | auto_compact 摘要提示词：强制三要素（已完成/当前状态/关键决策） | 0.5 天 | auto_compact |
| P1 | compact 工具：模型可主动调用，支持 focus 参数 | 1 天 | auto_compact |
| P1 | 完整对话存盘到 JSONL（压缩前备份，永不丢失） | 0.5 天 | auto_compact |
| P2 | 预算监控面板：终端显示当前 token 用量 / 阈值 | 0.5 天 | token 估算 |

**Week 4 验收**：连续读取 20+ 个文件后，Agent 不会因上下文溢出报错，micro_compact 自动压缩旧工具输出。

#### Week 5：权限控制 + 人机交互

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | 命令风险分类器：只读 / 构建 / 写 / 危险 / 禁用 | 1 天 | P1 bash |
| P0 | interrupt() 集成到 bash 工具：危险命令暂停等待确认 | 1 天 | 命令分类 |
| P0 | interrupt() 集成到 edit_file / write_file | 0.5 天 | interrupt 机制 |
| P0 | Command(resume=...) 恢复机制：CLI 审批提示 → 恢复执行 | 1 天 | interrupt |
| P0 | 权限模式实现：Manual / Accept Edits / Plan / Auto | 1 天 | 命令分类 |
| P1 | Shift+Tab 权限模式切换 | 0.5 天 | 权限模式 |
| P1 | 硬编码禁用命令清单（bash -c、python -e、sudo、eval） | 0.5 天 | 命令分类 |
| P2 | 预批准命令配置（.coding-agent/settings.json） | 0.5 天 | 权限模式 |

**Week 5 验收**：Agent 执行 `rm -rf` 时弹出审批提示，用户拒绝后命令不执行；只读命令（ls、cat）自动执行不提示。

#### Week 6：持久化 + 指令文件 + 斜杠命令

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | SqliteSaver 集成：替换 InMemorySaver，状态写入 SQLite | 1 天 | P1 |
| P0 | --resume 命令行参数：通过 thread_id 恢复历史会话 | 1 天 | SqliteSaver |
| P0 | 编辑前快照（checkpoints）：edit/write 前备份文件内容 | 1 天 | P1 |
| P0 | /undo 斜杠命令：回退到最近一次编辑前的状态 | 0.5 天 | checkpoints |
| P1 | AGENTS.md 分层加载：从 ~ 和各级目录聚合 | 1 天 | 无 |
| P1 | /compact 斜杠命令：手动触发上下文压缩 | 0.5 天 | auto_compact |
| P1 | /diff 斜杠命令：显示本次会话所有文件改动 | 0.5 天 | checkpoints |
| P1 | /help 斜杠命令：列出所有命令 | 0.5 天 | 无 |
| P2 | 斜杠命令自动补全（prompt_toolkit Completer） | 0.5 天 | 所有命令 |

**Week 6 验收（P2 里程碑）**：

| 验收任务 | 成功标准 |
|----------|----------|
| "重构 auth 模块，添加单元测试" | 多文件协调编辑（读 → 改 → 写测试 → 运行 pytest） |
| 连续 50 轮对话 | 上下文不溢出，auto_compact 自动触发，历史不丢失 |
| 退出后 `--resume` 恢复 | 完整上下文还原，可继续上次任务 |
| 执行 `git push --force` | 弹出审批提示，用户确认后才执行 |

---

### P3 — 沙箱 + 多模型 + 安全加固（2-3 周）

**目标**：能安全执行不可信代码，支持多模型切换，达到生产可用级别。这一阶段的核心是**安全性和兼容性**——隔离执行环境、适配不同模型、补齐生产级运维能力。

#### Week 7：Docker 沙箱 + 安全加固

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | Docker 沙箱集成：项目目录可写、其余只读、断网执行 | 2 天 | P2 bash |
| P0 | 沙箱配置开关：subprocess（默认）/ Docker 可切换 | 0.5 天 | Docker 集成 |
| P1 | 沙箱镜像构建：预装常用工具（git、python、node、rg） | 1 天 | Docker |
| P1 | 文件改动回滚增强：checkpoints 持久化到磁盘（跨会话） | 1 天 | P2 checkpoints |
| P1 | 错误恢复：工具执行超时/崩溃后的自动重试 | 0.5 天 | P2 |
| P2 | 日志系统：结构化日志记录所有工具调用和结果 | 0.5 天 | P2 |

**Week 7 验收**：在 Docker 沙箱中执行 `curl http://example.com` 被网络隔离阻断；项目目录内文件可正常读写。

#### Week 8：多模型支持 + 会话回溯

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | 模型配置系统：支持 --model 参数切换 Claude / GPT / DeepSeek | 1 天 | P2 |
| P0 | LangChain ChatModel 适配层：统一工具调用接口 | 1 天 | 模型配置 |
| P1 | 弱模型配置：摘要、提交信息等轻量任务用小模型 | 0.5 天 | 模型配置 |
| P1 | 会话回溯（time travel）：列出历史检查点、回到任意点 | 1.5 天 | SqliteSaver |
| P1 | Store 长期记忆：跨会话的用户偏好（namespace + 语义搜索） | 1 天 | SqliteSaver |
| P2 | 模型能力检测：自动判断是否支持原生工具调用 | 0.5 天 | ChatModel |
| P2 | 弱模型降级策略：不支持工具调用时用 prompt 模拟 | 1 天 | 模型能力检测 |

**Week 8 验收（P3 里程碑）**：

| 验收任务 | 成功标准 |
|----------|----------|
| `--model gpt-4o` 切换模型 | 工具调用正常、编辑功能正常 |
| `--model deepseek-chat` 切换模型 | 工具调用正常（验证 bind_tools 兼容性） |
| `--sandbox docker` 执行脚本 | 隔离执行、断网生效、项目目录可写 |
| `--resume <id>` 后回溯到 3 步前 | 从历史检查点重新执行，后续步骤可不同 |

#### Week 9（缓冲周）：打磨 + 文档 + 测试

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P1 | 集成测试：10 个典型编程任务端到端测试 | 2 天 | P3 全部 |
| P1 | 性能优化：流式输出延迟、工具执行并发 | 1 天 | P3 全部 |
| P2 | 用户文档：README、安装指南、配置说明 | 1 天 | P3 全部 |
| P2 | 边界情况修复：Week 7-8 发现的 bug | 1 天 | 测试 |

> **缓冲周说明**：P1-P2 每阶段预留 0.5-1 天缓冲，P3 整体预留 1 周缓冲。编程 Agent 的工具边界情况（编码、行尾、竞态）远比预期多，缓冲时间用于消化这些意外。

---

### P4 — 子 Agent + 高级特性（3-4 周）

**目标**：支持复杂任务分解、多 Agent 协作、外部工具扩展。这一阶段的核心是**规模化**——从单 Agent 循环升级为多 Agent 编排，从内置工具扩展到 MCP 生态。

#### Week 10：子 Agent + 任务分解

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | LangGraph 子图封装：独立上下文的子 Agent | 1.5 天 | P3 |
| P0 | spawn_subagent 工具：主 Agent 派生子 Agent | 1 天 | 子图 |
| P0 | 子 Agent 结果摘要返回（不污染主上下文） | 1 天 | spawn |
| P1 | Plan-and-Execute 模式：planner 节点 → executor 节点 → replanner | 2 天 | 子 Agent |
| P1 | update_plan 工具：模型显式维护任务步骤列表 | 1 天 | Plan-Execute |
| P2 | 子 Agent 递归限制：最大深度 1、并行上限 6 | 0.5 天 | spawn |

**Week 10 验收**：输入"给整个 src/ 目录的每个文件添加类型注解"，主 Agent 拆分为多个子任务，每个子 Agent 处理一个文件，完成后摘要返回。

#### Week 11：并行 fan-out + Git 集成

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P0 | Send 动态 fan-out：运行时生成并行子任务（map-reduce） | 1.5 天 | 子 Agent |
| P0 | 并行结果聚合：reducer 合并多个子 Agent 输出 | 1 天 | Send |
| P1 | Git 原生集成：每次 edit_file 后自动 git commit | 1 天 | P3 |
| P1 | /undo 增强：基于 git revert 回退（而非仅 checkpoints） | 1 天 | Git 集成 |
| P1 | /diff 增强：显示 git diff 格式的改动 | 0.5 天 | Git 集成 |
| P2 | Git 分支管理：每个会话可选创建独立分支 | 1 天 | Git 集成 |

**Week 11 验收**：输入"并行修复 3 个独立的 bug"，Agent 用 Send 同时派发 3 个子 Agent，各自独立修复后合并结果；每次编辑自动 git commit，/undo 可回退。

#### Week 12：MCP 协议 + 多模态

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P1 | MCP 客户端集成：通过 stdio/HTTP 连接外部 MCP server | 1.5 天 | P3 |
| P1 | MCP 工具动态注册：启动时发现并注册 MCP 工具 | 1 天 | MCP 客户端 |
| P1 | 多模态 read_file：PNG/JPG 图片以多模态方式返回 | 1 天 | P3 |
| P2 | Playwright 集成：UI 截图 + 元素分析工具 | 1.5 天 | 多模态 |
| P2 | tool_search：MCP 工具多时按需加载（BM25 检索） | 1 天 | MCP 工具 |
| P3 | WebSearch / WebFetch 工具：联网搜索和抓取 | 1 天 | 无 |

**Week 12 验收**：连接一个 MCP server（如 GitHub MCP），Agent 能调用其工具（创建 issue、查 PR）；读取 PNG 截图后能描述 UI 内容。

#### Week 13（缓冲周）：仓库地图 + 打磨

| 优先级 | 任务 | 预计工时 | 依赖 |
|--------|------|----------|------|
| P3 | 仓库地图：tree-sitter 解析 AST + 提取定义/引用 | 2 天 | P3 |
| P3 | 仓库地图：个性化 PageRank 排序 + Token 预算分配 | 1.5 天 | tree-sitter |
| P2 | 集成测试：5 个复杂多文件任务端到端测试 | 1 天 | P4 全部 |
| P2 | 性能基准：循环步数、token 消耗、延迟测量 | 0.5 天 | 测试 |

**Week 13 验收（P4 里程碑）**：

| 验收任务 | 成功标准 |
|----------|----------|
| "给整个项目添加类型注解并修复 lint 错误" | Plan-Execute 分解 → 子 Agent 并行处理 → 结果合并 |
| "连接 GitHub MCP，查看最近的 PR 并总结" | MCP 工具调用正常、结果返回主 Agent |
| "截图首页，分析 UI 布局问题" | 多模态读取截图、Playwright 元素分析 |

---

### 实施顺序的依赖关系

下图展示各任务间的依赖关系，箭头表示"必须先完成前者才能做后者"：

```
P1 依赖链:
  脚手架 → StateGraph → bind_tools → [read_file, list_dir, glob, grep]
                                    → [edit_file(依赖read_file), write_file, bash]
                                    → stream_events → Rich Live → 工具状态展示
                                    → InMemorySaver

P2 依赖链:
  P1 → token估算 → context_check → micro_compact → auto_compact → compact工具
  P1 → 命令分类 → interrupt → Command(resume) → 权限模式
  P1 → SqliteSaver → --resume
  P1 → checkpoints → /undo
  无依赖 → AGENTS.md

P3 依赖链:
  P2 bash → Docker沙箱
  P2 → 模型配置 → ChatModel适配 → 弱模型
  P2 SqliteSaver → 会话回溯
  P2 SqliteSaver → Store长期记忆

P4 依赖链:
  P3 → 子图 → spawn_subagent → 摘要返回 → Plan-Execute
                            → Send fan-out → 结果聚合
  P3 → MCP客户端 → MCP工具注册
  P3 → 多模态read_file → Playwright
  P3 → tree-sitter → PageRank → 仓库地图
```

### 里程碑总览

| 里程碑 | 时间 | 可用版本 | 核心能力 |
|--------|------|----------|----------|
| M1 | Week 3 末 | v0.1.0 | 单文件读取、编辑、命令执行、流式输出 |
| M2 | Week 6 末 | v0.2.0 | 多文件任务、上下文压缩、权限审批、会话恢复 |
| M3 | Week 8/9 末 | v0.3.0 | Docker 沙箱、多模型、会话回溯、长期记忆 |
| M4 | Week 12/13 末 | v1.0.0 | 子 Agent、Plan-Execute、MCP、多模态、Git 集成 |

### 风险与缓冲策略

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| edit_file 边界情况超出预期（编码、行尾、竞态） | 高 | P1 延期 2-3 天 | Week 2 预留 0.5 天缓冲；P1 验收降级为"UTF-8/LF 文件" |
| 多模型工具调用格式差异导致循环不收敛 | 中 | P3 延期 1-2 天 | P1 先只支持 Claude；P3 逐个模型验证，不兼容的降级 |
| Docker 沙箱跨平台兼容性问题（macOS/Windows） | 中 | P3 延期 2-3 天 | Docker 设为可选，subprocess 作为默认兜底 |
| LangGraph API 变更（stream_events v3 仍在演进） | 低 | P1/P2 受影响 | 锁定 LangGraph 版本；关注 changelog |
| 上下文压缩摘要质量差导致 Agent 重复读取 | 中 | P2 体验下降 | 摘要后保留最近 N 条完整消息；路径/标识符特殊保留 |

---

## 14. 关键技术难点

### edit_file 的边界情况

文件编辑工具是工程复杂度最高的部分。除了前文描述的三阶段管线，还有大量边界情况：Windows 上云同步服务（OneDrive、Dropbox）和杀毒软件会频繁触碰 mtime 即使内容未变——若 mtime 变了但内容完全相同（且为完整读取），判定为假阳性放行 [15]。当 `new_string` 为空（删除操作）且 `old_string` 不以换行结尾但文件中其后紧跟换行时，需一并删除该换行，避免留下空行。这些细节决定了工具的健壮性。

### 流式输出与工具调用的交织

模型在流式输出时，文本块和工具调用块是交替到达的。一个典型序列是：先输出一段思考文本（"我需要查看 auth.py 的内容"），然后发起 `read_file` 工具调用。CLI 需要增量解析器实时判断当前是文本还是工具调用 JSON，文本部分流式渲染，工具调用部分累积完整后触发执行。如果解析器有 bug，可能导致工具调用 JSON 被当作文本渲染到终端，或文本被当作 JSON 解析失败。

### 上下文压缩的质量

auto_compact 调用 LLM 生成摘要，摘要质量直接影响 Agent 的后续表现。强制包含三要素（已完成工作、当前状态、关键决策）是必要的但不充分的——如果摘要丢失了某个关键文件路径或变量名，Agent 可能重复读取已读过的文件。缓解策略：摘要后保留最近 N 条完整消息（而非全部替换），并对摘要中的文件路径和标识符做特殊保留标记。

### interrupt 的幂等性约束

LangGraph 的 `interrupt()` 恢复时节点从头重跑 [22]。这意味着 interrupt 之前的代码会执行两次：第一次执行到 interrupt 暂停，第二次恢复时从头跑再执行一遍。如果 interrupt 前有 side-effect（如写文件、发请求），必须保证幂等。对于 `bash` 工具，interrupt 发生在命令执行前（审批阶段），命令本身尚未执行，因此天然安全。但对于自定义工具，开发者必须注意这一约束。

### 多模型工具调用格式差异

不同模型提供商的工具调用协议有差异：Anthropic 用 `tool_use` content block，OpenAI 用 `tool_calls` 字段，部分开源模型不支持原生工具调用。LangChain 的 `bind_tools` 层抽象了这些差异 [7]，但弱模型（如本地 7B 模型）的工具调用准确率明显低于强模型，可能导致循环无法收敛或工具参数错误。建议 P3 阶段对弱模型增加参数校验和重试逻辑，或在弱模型上降级为"整文件重写"编辑模式（降低对精确 str_replace 的依赖）。

### 递归限制与任务复杂度

复杂任务（如重构整个模块）可能需要 50+ 步循环，但 `recursion_limit` 设太高又可能导致失控。策略是**分层分解**：主 Agent 负责规划和分解，子 Agent 负责执行子任务（每个子 Agent 有独立的 recursion_limit）。子 Agent 完成后只返回摘要给主 Agent，主 Agent 基于摘要决定下一步。这就是 P4 阶段的 Plan-and-Execute 模式的核心价值。

---

## 参考资料

1. Anthropic, *How Claude Code works*. Claude Code 官方文档，描述 agentic loop 的三阶段循环机制. https://code.claude.com/docs/en/how-claude-code-works.md
2. OpenAI, *Unrolling the Codex agent loop*. 工程博客，解析 Codex CLI 的 agent loop 与流式输出原理. https://openai.com/de-DE/index/unrolling-the-codex-agent-loop/
3. LangChain, *LangGraph overview*. 官方文档，StateGraph、Checkpointer、interrupt 等核心机制说明. https://docs.langchain.com/oss/python/langgraph/overview
4. LangChain, *Agents (concept)*. 官方概念文档，LangChain 与 LangGraph 的分层定位. https://python.langchain.com/docs/concepts/agents/
5. Kanopy Labs, *Devin vs OpenHands vs SWE-Agent*. 对比编程 Agent 的工具接口设计，SWE-agent 的 ACI 设计理念. https://kanopylabs.com/blog/devin-vs-openhands-vs-swe-agent-autonomous-coding
6. Anthropic, *Building agents with the Claude Agent SDK*. 工程博客，agentic search 优先于 semantic search 的设计理念. https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk
7. LangChain, *Tools*. 官方文档，@tool 装饰器、StructuredTool、bind_tools、ToolRuntime 用法. https://docs.langchain.com/oss/python/langchain/tools
8. CSDN, *如意Agent终端版深度解析*. Typer + Rich 打造 AI 命令行客户端的实践. https://blog.csdn.net/qq_37703224/article/details/160844514
9. python-prompt-toolkit, *Python Prompt Toolkit 3.0*. 官方文档，多行编辑、历史记录、自动补全. https://python-prompt-toolkit.readthedocs.io/en/latest/
10. LangChain, *Memory (checkpointers + stores)*. 官方文档，Checkpointer 与 Store 的分层持久化机制. https://docs.langchain.com/oss/python/langgraph/add-memory
11. Anthropic, *Claude Code tools reference*. 官方工具参考，Bash、Read、Edit、Write、Glob、Grep 等工具的设计细节. https://code.claude.com/docs/en/tools-reference
12. Anthropic, *How tool use works*. 官方文档，stop_reason == "tool_use" 的循环协议说明. https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works.md
13. LangChain, *LangGraph Quickstart*. 官方快速入门，ToolNode 与条件边循环的实现. https://docs.langchain.com/oss/python/langgraph/quickstart
14. LangChain, *Graph API overview*. 官方文档，Functional API (@entrypoint/@task) 与 Graph API 的对比. https://docs.langchain.com/oss/python/langgraph/graph-api
15. finisky, *Why Claude Code's Edit Tool Doesn't Mangle Your Files*. 深度解析 Claude Code Edit 工具的三阶段处理管线. https://finisky.github.io/en/claude-code-edit-tool/
16. ggprompts, *Aider Architecture*. Aider 的编辑格式、多级回退策略与仓库地图实现. https://ggprompts.com/architecture/aider/index.html
17. Morphllm, *What Is an LLM Context Window*. 上下文窗口的增长趋势与注意力稀释问题. https://www.morphllm.com/llm-context-window
18. finisky, *Claude Code 上下文管理*. 三层压缩策略（micro_compact / auto_compact / compact）的工程实现. https://finisky.github.io/en/claude-code-edit-tool/
19. BodegaOne, *How to plan your LLM context window budget*. 上下文预算分配策略与输出预留. https://www.bodegaone.ai/blog/context-window-planner-for-local-llms
20. Anthropic, *How Claude Code works*. 权限模式、checkpoints 回滚、自动压缩等机制. https://code.claude.com/docs/en/how-claude-code-works.md
21. Zylos, *OpenAI Codex CLI Architecture*. Codex CLI 的 Rust 架构、bubblewrap 沙箱、SQLite thread 持久化. https://zylos.ai/research/2026-03-26-openai-codex-cli-architecture-multi-runtime-patterns/
22. LangChain, *Interrupts (HITL)*. 官方文档，interrupt() 与 Command(resume=...) 的人机交互机制. https://docs.langchain.com/oss/python/langgraph/interrupts
23. LangChain, *Streaming*. 官方文档，stream_events v3 的类型化投影（messages/values/interrupts）. https://docs.langchain.com/oss/python/langgraph/streaming
24. PyPI, *python-xli 0.2.0*. 终端流式输出的 scrollback 兼容性设计. https://pypi.org/project/python-xli/
25. LangChain, *How to turn Claude Code into a domain-specific coding agent*. AGENTS.md 静态基础 + RAG 动态检索的组合方案. https://www.langchain.com/blog/how-to-turn-claude-code-into-a-domain-specific-coding-agent
