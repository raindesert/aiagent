# 对话 AI Agent

一个配置驱动的对话 Agent 框架, 三个核心能力: **可设置的上下文提示**、**模型调用接口**、**Tool 调用**。

当前实现面向 **OpenAI 兼容 API** + **原生 function calling**。在 `agent.yaml` 里改配置就能切换模型、提示词、工具, 不用改代码。

## 特性

- **配置驱动** —— 提示词、模型、工具、循环策略全部在 `agent.yaml` 里
- **OpenAI 兼容协议** —— 一行配置切换 OpenAI / DeepSeek / 通义 / Ollama / llama.cpp server
- **原生 function calling** —— 用模型原生的 `tool_calls` 字段, 工具定义用 JSON Schema
- **多轮记忆** —— 滚动窗口 + 粗略 token 预算, 超出自动截断
- **持久化记忆** —— SQLite 存对话历史, 跨 session / 跨重启保留 (`/session` 管理)
- **流式输出** —— 模型回答时逐 token 实时显示
- **多模型切换** —— 在 `models.yaml` 配多个后端, 交互中 `/model <name>` 切换
- **Web UI** —— `python -m streamlit run ui.py`, 浏览器里对话 + 流式显示 + 侧边栏切模型/管会话
- **工具沙箱** —— 每个工具独立超时, 异常被捕获并以 tool message 形式回喂
- **可扩展** —— 新增工具只需写函数 + 在 yaml 里加一段配置

## 已实现工具 (12 个)

| 工具 | 能力 |
|------|------|
| `get_current_time` | 返回当前时间, 支持时区 |
| `get_weather` | 调 [wttr.in](https://wttr.in) 查实时天气 (中英文城市名) |
| `search_files` | 在目录里按 glob 模式搜索文件 |
| `send_rabbit_message` | 调本地 RabbitMQ HTTP 网关 (`127.0.0.1:8081`) 发消息 |
| `run_shell` | 执行 shell 命令, Windows 默认调 PowerShell (UTF-8 编码, 避免引号问题); 内置危险命令拦截 |
| `read_file` | 读文件, 带行号 (`cat -n` 风格), 支持起止行 |
| `write_file` | 写文件, 自动建父目录 |
| `edit_file` | 精确字符串替换, `old` 必须唯一匹配 |
| `web_fetch` | 抓 URL, HTML 自动转纯文本 |
| `http_request` | 通用 HTTP 客户端 (GET/POST/PUT/DELETE/PATCH/HEAD) |
| `grep_search` | 在目录里按正则搜索, 输出 `file:行号: 片段` 格式 |
| `python_run` | 子进程跑 Python 代码, 用 `sys.executable`, 通过 stdin 喂代码 (无长度限制) |

## 目录结构

```
aiagent/
├── agent.yaml              # agent 配置 (提示词/工具/模型引用)
├── models.yaml             # 模型后端配置 (可多个, /model 切换)
├── requirements.txt
├── requirements-dev.txt    # 测试依赖 (pytest)
├── pytest.ini              # 测试配置 + e2e / network 标记
├── cli.py                  # CLI 入口
├── ui.py                   # Web UI 入口 (streamlit)
├── main.py                 # cli 别名
├── README.md
├── .gitignore
├── agent/                  # 核心库
│   ├── __init__.py
│   ├── config.py           # Pydantic 配置模型 + ${ENV} 展开
│   ├── context.py          # 上下文/系统提示管理
│   ├── memory.py           # 多轮对话记忆 + 截断
│   ├── memory_store.py     # SQLite 持久化 session
│   ├── model.py            # OpenAI 兼容模型客户端 (流式 + 工具调用)
│   ├── tools.py            # 工具注册表 (schema + 执行 + 必填校验 + 超时)
│   ├── ui_support.py       # Web UI 纯逻辑 (消息视图 + 流式转次), 不依赖 streamlit
│   └── core.py             # Agent 主循环 (ReAct + tool call)
├── tools/                  # 工具实现
│   ├── time.py             # get_current_time
│   ├── weather.py          # get_weather (wttr.in)
│   ├── search_files.py     # search_files
│   ├── rabbit.py           # send_rabbit_message
│   ├── shell.py            # run_shell
│   ├── files.py            # read_file / write_file / edit_file
│   ├── web.py              # web_fetch (HTML→text)
│   ├── http.py             # http_request
│   ├── grep.py             # grep_search
│   └── python_run.py       # python_run
└── tests/                  # pytest 套件 (见下文"测试")
    ├── conftest.py         # fixture: 配置 / 临时 db / 本地模型可用性
    ├── fakes.py            # 脚本化假模型
    ├── test_config.py      # 配置加载 + system prompt
    ├── test_memory.py      # 截断 + SQLite 持久化
    ├── test_tools.py       # 12 个工具 + 注册表防护
    ├── test_agent_loop.py  # 主循环 / session / 模型切换 (假模型)
    ├── test_cli.py         # CLI 斜杠命令 + 回归
    └── test_e2e_local_model.py  # 真模型端到端
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置模型 (models.yaml)

模型配置独立在 `models.yaml`, 可以放多个后端, CLI 里用 `/model <name>` 切换。

```yaml
# models.yaml
default: qwen-local

models:
  - name: qwen-local            # 别名, CLI 切换用
    backend: openai
    model: CPM5-2B              # 上游真实模型名
    base_url: http://localhost:8080/v1
    api_key: ${OPENAI_API_KEY}  # 支持 ${ENV_VAR} 占位
    temperature: 0.7
    max_tokens: 2048
    timeout: 300
    stream: true

  - name: deepseek
    backend: openai
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    ...
```

`agent.yaml` 里只放引用:

```yaml
# 注意: 这些字段是顶层的, 不要缩进到 `agent:` 下面 (那一段会被静默忽略)
models_file: models.yaml       # 指向模型配置
default_model: qwen-local      # 启动用哪个, 留空用 models.yaml 里的 default
...
```

设置环境变量 (Windows PowerShell):

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
$env:MS_API_KEY = "sk-..."
```

### 3. 跑起来

```bash
# 交互模式 (不打 banner / INFO 日志 / 每轮统计, 这些统一归 -v)
python cli.py

# 单轮
python cli.py -m "现在几点了?"

# 打印解析后的配置
python cli.py --print-config

# 看每次 tool call 的 INFO 日志
python cli.py -v

# 排查问题时看 DEBUG
python cli.py --debug

# 完全静默 (只显示 ERROR)
python cli.py -q
```

交互命令: `/quit` 退出, `/reset` 清空对话, `/tools` 列工具, `/models` 列所有模型, `/model <name>` 切换 (历史保留), `/session` 管理持久化 session。

### 4. 浏览器里聊 (Web UI)

```bash
pip install -r requirements.txt        # streamlit 在里面

python -m streamlit run ui.py                              # 默认端口 8501
python -m streamlit run ui.py --server.port 8601           # 换端口
python -m streamlit run ui.py --server.headless true       # 服务器上跑, 不自动开浏览器
```

> 用 `python -m streamlit` 而不是裸 `streamlit`: pip 在没有管理员权限时会把包装到用户目录
> (`%APPDATA%\Python\PythonX.Y\site-packages`), 那个 `Scripts` 目录通常不在 PATH 里, 于是
> `streamlit` 命令报"无法识别"。想直接用裸命令就把该目录加进 PATH:
> `$env:PATH += ";$env:APPDATA\Python\Python313\Scripts"` (或写进注册表 User PATH 永久生效)。

UI 和 CLI 共用同一份 `agent.yaml` + `models.yaml` + `~/.aiagent/memory.db`, 所以命令行聊到一半的 session 网页上能接着聊, 反过来也一样。

- **侧边栏**: 改配置路径、下拉切模型、session 下拉 (列出/切换) + 新建/清空按钮、展开看工具列表和渲染后的系统提示词
- **聊天区**: 用户/助手气泡; 工具调用和结果折叠在 expander 里 (结果超长截断显示), 模型配 `stream: true` 时逐 token 刷新
- **统计**: `iter=N tools=[...]` 默认不显示, 勾上侧边栏"显示每轮 iter/工具统计"才有 (对应 CLI 的 `-v`)
- 以 `/` 开头的输入在网页端不当命令解析 (那些操作都做成按钮了), 会提示改用侧边栏

## 切换模型后端

`models.yaml` 里加新条目即可, 任何 OpenAI 兼容服务都能直接用:

| 服务 | base_url | name 示例 |
|------|----------|-----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 DashScope (兼容模式) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama (本地) | `http://localhost:11434/v1` | `qwen2.5:7b-instruct` |
| llama.cpp server (本地) | `http://localhost:8080/v1` | `local` |

CLI 里切换:

```
>>> /models
   qwen-local     CPM5-2B                http://localhost:8080/v1
 * deepseek       deepseek-chat          https://api.deepseek.com/v1
   modelscope     Qwen/Qwen3.8-Flash-Next https://api-inference.modelscope.cn/v1
>>> /model modelscope
已切换: qwen-local (model: CPM5-2B) → modelscope (model: Qwen/Qwen3.8-Flash-Next)
```

切换**不**清空对话历史, 新模型能直接看到之前聊的内容。

> Ollama / llama.cpp 启动时会自动暴露 `/v1` 端点, 直接用 OpenAI SDK 调即可。**Tool calling 依赖模型本身支持, 小模型请选 Qwen2.5-7B-Instruct / Hermes-3 / Functionary / CPM5 这类专门微调过的。**

## 改提示词

`agent.yaml` 里的 `context.system_prompt`:

```yaml
context:
  system_prompt:
    template: |
      你是 {role}。当前时间 {current_time}。
      规则: ...
    variables:
      role: 编程助手
```

模板用 Python `str.format`, 已注入的运行时变量: `current_time` (你也可以在 `Agent.chat()` 里通过 `runtime_vars` 传更多)。

## 改 / 新增工具

**1) 写函数** (放在 `tools/` 任意文件里):

```python
# tools/my_tool.py
def add(a: int, b: int) -> int:
    return a + b
```

**2) 在 `agent.yaml` 里声明**:

```yaml
tools:
  - name: add
    description: 求两个整数之和
    enabled: true
    parameters:
      type: object
      properties:
        a: {type: integer}
        b: {type: integer}
      required: [a, b]
    handler: tools.my_tool:add
```

**3) 不用重启代码**, `python cli.py` 会自动加载。

> Tool 函数返回值必须是 `str`。复杂结果用 `json.dumps(..., ensure_ascii=False)` 序列化。错误时直接 `return "错误描述"`, 工具注册表会自动捕获异常并以 tool message 形式回喂给模型。

## 嵌入到自己的代码

```python
from agent import Agent, load_config

cfg = load_config("agent.yaml")
agent = Agent(cfg)
result = agent.chat("上海今天天气怎么样?")
print(result.content)
print(f"共 {result.iterations} 轮, 调用了 {result.tool_calls_made}")
```

## CLI 选项

```
python cli.py [-h] [-c CONFIG] [--print-config] [-v] [--debug] [-q] [-m MESSAGE]

  -c, --config CONFIG    agent 配置路径 (默认 agent.yaml)
  --print-config         打印 agent 配置后退出
  -v, --verbose          显示 INFO 级日志 + 启动 banner + 每轮 [iter=N tools=[...]] 统计行 (默认都不显示)
  --debug                显示 DEBUG 级日志 (排查问题用)
  -q, --quiet            只显示 ERROR 及以上 (静默模式)
  -m, --message MESSAGE  单轮模式: 直接发一条消息并打印回答
```

交互命令:

| 命令 | 作用 |
|------|------|
| `/quit` `/exit` `:q` | 退出 |
| `/reset` | 清空当前 session 的对话历史 |
| `/tools` | 列出可用工具 |
| `/models` | 列出所有配置的模型后端 (带 `*` 标记当前) |
| `/model` | 显示当前模型 |
| `/model <name>` | 切换到指定模型 (历史保留) |
| `/session` | 显示当前 session |
| `/session list` | 列出所有持久化的 session |
| `/session new [name]` | 开新 session (旧 session 自动保存) |
| `/session switch <id>` | 切换到指定 session |
| `/session save [title]` | 手动保存当前 session (可选标题) |

## 流式输出 + 持久化记忆

**流式**: 模型回答时**逐 token 实时显示**到 stderr, 不再等整段生成完。
内部 `ModelClient.chat_streaming(messages, tools, on_token)` 接受回调。
非流式模型 (`stream=false`) 自动降级到一次性输出。

**持久化**: 对话历史存到 SQLite (`~/.aiagent/memory.db`), 跨重启 / 跨 session 保留。每次 `chat()` 后自动 save, 启动时自动加载。

```python
from agent import Agent, load_config, MemoryStore

cfg = load_config("agent.yaml")
store = MemoryStore("~/.aiagent/memory.db")  # 默认路径, 可改
agent = Agent(cfg, store=store, session_id="proj1")
# 启动时如果 proj1 已有历史, 自动加载; 否则从空开始
```

`session_id` 是任意字符串 (项目名 / 任务名 / 用户名都行)。
`agent.switch_session("other-id")` 在不同 session 间切换, 类似浏览器标签页。

## 配置字段说明

### `agent.yaml`

| 字段 | 说明 |
|------|------|
| `agent.name` | 仅用于日志/CLI banner |
| `models_file` | 指向 `models.yaml`, 默认 `models.yaml` |
| `default_model` | 启动用哪个, 留空用 `models.yaml` 里的 `default` |
| `context.system_prompt.template` | 系统提示词模板 |
| `context.system_prompt.variables` | 模板变量, 运行时可覆盖 |
| `context.max_history_messages` | 记忆里最多保留多少条消息 |
| `context.max_history_tokens` | 粗略 token 预算, 超出截断 |
| `tools[].name` | 工具名 (全英文, 模型用此调用) |
| `tools[].description` | 工具描述 (模型靠这个判断何时调用) |
| `tools[].enabled` | 是否启用 |
| `tools[].parameters` | JSON Schema, 定义参数 |
| `tools[].handler` | 形如 `pkg.mod:func` 的可调用对象路径 |
| `loop.max_iterations` | 防止 tool call 死循环 |
| `loop.tool_timeout` | 单个 tool 调用的最长秒数 |

### `models.yaml`

| 字段 | 说明 |
|------|------|
| `default` | 默认激活的模型名 (在 models 列表里) |
| `models[].name` | 别名, CLI `/model <name>` 切换用 |
| `models[].backend` | 当前仅 `openai` |
| `models[].model` | 上游真实模型名 (发给 API 的) |
| `models[].base_url` | API 端点 |
| `models[].api_key` | API key, 支持 `${ENV_VAR}` 占位 |
| `models[].temperature` / `max_tokens` / `timeout` / `stream` | 标准参数 |

## 测试

```bash
pip install -r requirements-dev.txt

pytest                     # 离线套件 (假模型), 不碰任何外部服务
pytest -m e2e              # 端到端: 真模型 + 真工具 + 真 SQLite (模型服务没起会自动 skip)
pytest -m network          # 需要外网的用例 (wttr.in)
pytest -m "e2e or network" -v
```

约定:

- `xfail(strict)` 的用例 = 已知缺陷, 修好了会变 XPASS 报错提醒; 当前 4 个:
  `agent.yaml` 的 `agent:` 块被忽略、未设置的 `${ENV}` 占位符被当成 api_key、
  `auto-` 前缀的 session 标题改不掉、PowerShell 报错文本被 CLIXML 清理时一并丢掉
- 工具类用例全部通过 `ToolRegistry.execute` 走真实路径 (含超时/必填校验), 不直接调函数
- `test_ui.py` 用 streamlit 自带的 `AppTest` 无头执行 `ui.py` 来验渲染和侧边栏 (没装 streamlit 会自动 skip);
  真发消息那条标了 `e2e`, 且把 `HOME`/`USERPROFILE` 指到 `tmp_path`, 不碰用户真实的 `~/.aiagent/memory.db`
- 写文件/跑命令的用例一律在 `tmp_path` 里, 危险命令只测拦截器本身, 不执行

## 已知限制 (MVP)

- 只支持 OpenAI 兼容协议 (其他后端后续可加)
- 只支持原生 function calling (ReAct 文本解析模式后续可加)
- 记忆截断是简单 FIFO, 没有摘要压缩
- 单进程, 不支持服务端多用户
- `python_run` 没有危险代码拦截, agent 拿到这个工具等于能跑任意 Python (要更安全可改用受限子进程或 WASM)

## 后续可加的东西

- ReAct 文本解析作为 fallback, 给 4B 以下小模型用
- 对话持久化 (SQLite / JSON)
- FastAPI 服务化
- 流式输出 token 到前端 (SSE / WebSocket)
- Tool 调用结果缓存 (相同输入直接返回)
- `python_run` 的危险代码拦截 (类似 `run_shell` 的 confirm 机制)
- 危险命令的 CLI 交互确认 (现在靠模型把 "需要确认" 信息转给用户)

## `run_shell` 默认 shell 行为

| 平台 | shell | 备注 |
|------|-------|------|
| Windows | `powershell -NoProfile -NonInteractive -EncodedCommand <b64>` | 自动切 UTF-8 编码, 中文不乱码; 用 EncodedCommand 避开所有引号转义 |
| Linux / macOS | `/bin/sh -c <cmd>` | POSIX 兼容 |

Windows 命令按 **PowerShell 语法**解析:

```powershell
# OK
Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Get-ChildItem | Select-Object -First 3 Name
Remove-Item -Recurse -Force build

# 旧 cmd 语法不工作
dir /B tools          # ✗ /B 是 cmd 开关, PowerShell 不识别 (用 Get-ChildItem)
rmdir /s /q foo        # ✗ (用 Remove-Item -Recurse -Force foo)
```

中文变量和字符串都正常:

```powershell
$name = '张三'; Write-Host "用户: $name"   # 输出: 用户: 张三
```

> 历史变更: 早期版本默认走 cmd, 后来切换到 PowerShell。如果之前有依赖 cmd 语法的脚本, 需要改成 PowerShell 等价写法。

## `run_shell` 安全机制

内置常见危险模式拦截 (正则, 大小写不敏感):

- **递归删除**: `rm -rf` / `rmdir /s /q` / `del /s /q` / `Remove-Item -Recurse`
- **格式化 / 写裸设备**: `format X:` / `Format-Volume` / `diskpart` / `mkfs*` / `dd of=/dev/sd*`
- **启动破坏**: `bcdedit /delete`
- **关机/重启**: `shutdown` / `reboot` / `Stop-Computer` / `Restart-Computer`
- **Fork bomb**: `:(){ :|:& };:`
- **磁盘擦除**: `cipher /w` / `sdelete` / `shred`
- **下载并执行**: `curl | sh` / `iwr | iex`
- **系统破坏**: `net user/delete` / `reg delete` / `Remove-Item HK*`

被拦截时返回错误, 告诉模型 "需要用户确认, 设置 `confirm=true` 重试"。模型会把这条信息转给用户, 用户明确同意后模型再带 `confirm=true` 调用, 实际执行时输出含 `⚠️` 警告前缀。

普通命令 (`echo`, `dir`, `git`, `npm`, `curl <url>` 等) 不受影响。`rm` 单文件、`del` 单文件也不在拦截列表 (这些可恢复)。
