SoraNimi/
├── pyproject.toml
├── README.md
├── AGENTS.md                          # 本项目自身的 Agent 指令文件
├── coding-agent-plan.md
│
├── src/
│   └── soranimi/
│       ├── __init__.py
│       │
│       │── cli/                       # ══ CLI 交互层 (§4/§10) ══
│       │   ├── __init__.py
│       │   ├── app.py                 # Typer 入口，--model/--resume/--sandbox 参数
│       │   ├── input_handler.py       # prompt_toolkit 多行输入、历史记录、Esc 中断
│       │   ├── completer.py           # 斜杠命令 / @file 自动补全
│       │   └── renderer.py           # Rich Live 流式 Markdown 渲染、Syntax 代码高亮
│       │
│       ├── agent/                     # ══ Agent 编排层 (§5) ══
│       │   ├── __init__.py
│       │   ├── graph.py               # StateGraph 构建：llm_call / should_continue / ToolNode
│       │   ├── nodes.py               # 图节点实现：llm_call, context_check, tool_node
│       │   ├── state.py               # 自定义 State 类型（扩展 MessagesState）
│       │   └── model_factory.py       # 模型工厂：根据 --model 创建 ChatModel 实例 (§8/§P3)
│       │
│       ├── tools/                     # ══ 工具层 (§6/§7) ══
│       │   ├── __init__.py            # 汇总导出 ALL_TOOLS 列表
│       │   ├── read_file.py           # 读文件：行号、分页、图片多模态
│       │   ├── edit_file.py           # 精确编辑：三阶段管线、多级回退 (§7)
│       │   ├── write_file.py          # 新建/覆盖文件
│       │   ├── glob_tool.py           # 文件模式匹配
│       │   ├── grep_tool.py           # ripgrep 封装
│       │   ├── bash.py                # Shell 命令执行 + 工作目录持久化
│       │   ├── list_dir.py            # 目录列表
│       │   └── _edit_pipeline.py      # edit_file 内部：预处理→校验→写入管线 (§7)
│       │
│       ├── context/                   # ══ 上下文管理 (§8) ══ [P2]
│       │   ├── __init__.py
│       │   ├── token_estimator.py     # token 估算：len(str) // 4
│       │   ├── micro_compact.py       # 微压缩：替换旧工具输出为占位符
│       │   ├── auto_compact.py        # 自动压缩：阈值触发 LLM 摘要
│       │   └── compact_tool.py        # compact 工具：模型主动调用，支持 focus
│       │
│       ├── permission/                # ══ 权限控制 (§9) ══ [P2]
│       │   ├── __init__.py
│       │   ├── modes.py               # 权限模式枚举：Manual / AcceptEdits / Plan / Auto
│       │   ├── classifier.py          # 命令风险分类器：只读/构建/写/危险/禁用
│       │   └── approval.py            # interrupt() 审批 + Command(resume) 恢复
│       │
│       ├── sandbox/                   # ══ 沙箱执行 (§9) ══ [P3]
│       │   ├── __init__.py
│       │   ├── executor.py            # 统一执行接口（subprocess / docker 切换）
│       │   ├── subprocess_exec.py     # subprocess 执行器（默认）
│       │   └── docker_exec.py         # Docker 沙箱执行器
│       │
│       ├── persistence/               # ══ 持久化与记忆 (§11) ══
│       │   ├── __init__.py
│       │   ├── checkpointer.py        # Checkpointer 工厂：InMemory / SQLite / Postgres
│       │   ├── session.py             # 会话管理：thread_id 生成、--resume 恢复
│       │   ├── store.py               # 长期记忆 Store：跨会话偏好 [P3]
│       │   └── time_travel.py         # 会话回溯：列出/回到历史检查点 [P3]
│       │
│       ├── streaming/                 # ══ 流式输出 (§10) ══
│       │   ├── __init__.py
│       │   ├── event_consumer.py      # stream_events v3 消费：token/状态/中断分流
│       │   └── tool_status.py         # 工具调用状态展示：Calling/Running/Done
│       │
│       ├── instructions/              # ══ 项目指令文件 (§12) ══ [P2]
│       │   ├── __init__.py
│       │   └── loader.py              # AGENTS.md 分层加载：~ → project → cwd
│       │
│       ├── subagent/                  # ══ 子 Agent (§P4) ══ [P4]
│       │   ├── __init__.py
│       │   ├── spawn.py               # spawn_subagent 工具：派生子 Agent
│       │   ├── subgraph.py            # 子图封装：独立上下文的子 Agent 图
│       │   └── plan_execute.py        # Plan-and-Execute：planner → executor → replanner
│       │
│       ├── mcp/                       # ══ MCP 协议 (§P4) ══ [P4]
│       │   ├── __init__.py
│       │   ├── client.py              # MCP 客户端：stdio/HTTP 连接
│       │   └── registry.py            # MCP 工具动态注册与发现
│       │
│       ├── repo_map/                  # ══ 仓库地图 (§P4) ══ [P4]
│       │   ├── __init__.py
│       │   ├── parser.py              # tree-sitter AST 解析
│       │   └── ranker.py              # PageRank 排序 + token 预算分配
│       │
│       ├── checkpoints/               # ══ 文件快照回滚 (§9) ══ [P2]
│       │   ├── __init__.py
│       │   └── snapshot.py            # edit/write 前快照、/undo 回退
│       │
│       ├── git/                       # ══ Git 集成 (§P4) ══ [P4]
│       │   ├── __init__.py
│       │   └── integration.py         # 自动 commit、revert、分支管理
│       │
│       └── config.py                  # 全局配置：模型、权限模式、沙箱类型、路径
│
├── tests/                             # ══ 测试 ══
│   ├── __init__.py
│   ├── conftest.py                    # 共享 fixture：mock model, temp filesystem
│   ├── unit/
│   │   ├── test_tools/
│   │   │   ├── test_read_file.py
│   │   │   ├── test_edit_file.py
│   │   │   ├── test_edit_pipeline.py
│   │   │   ├── test_write_file.py
│   │   │   ├── test_glob.py
│   │   │   ├── test_grep.py
│   │   │   ├── test_bash.py
│   │   │   └── test_list_dir.py
│   │   ├── test_context/
│   │   │   ├── test_token_estimator.py
│   │   │   ├── test_micro_compact.py
│   │   │   └── test_auto_compact.py
│   │   ├── test_permission/
│   │   │   └── test_classifier.py
│   │   └── test_agent/
│   │       └── test_graph.py
│   └── integration/
│       ├── test_agent_loop.py         # 端到端：Agent Loop 完整流程
│       ├── test_multi_file.py         # 端到端：多文件协调编辑
│       └── test_session_resume.py     # 端到端：会话恢复
│
└── docker/                            # ══ Docker 沙箱镜像 (§P3) ══
    ├── Dockerfile                     # 沙箱镜像：git/python/node/rg
    └── entrypoint.sh
