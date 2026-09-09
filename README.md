# 对话 AI Agent

一个配置驱动的对话 Agent 框架, 三个核心能力: **可设置的上下文提示**、**模型调用接口**、**Tool 调用**。

当前实现面向 **OpenAI 兼容 API** + **原生 function calling**。在 `agent.yaml` 里改配置就能切换模型、提示词、工具, 不用改代码。

## 特性

- **配置驱动** —— 提示词、模型、工具、循环策略全部在 `agent.yaml` 里
- **OpenAI 兼容协议** —— 一行配置切换 OpenAI / DeepSeek / 通义 / Ollama / llama.cpp server
- **原生 function calling** —— 用模型原生的 `tool_calls` 字段, 工具定义用 JSON Schema
- **多轮记忆** —— 滚动窗口 + 粗略 token 预算, 超出自动截断
- **工具沙箱** —— 每个工具独立超时, 异常被捕获并以 tool message 形式回喂
- **可扩展** —— 新增工具只需写函数 + 在 yaml 里加一段配置

## 已实现工具 (9 个)

| 工具 | 能力 |
|------|------|
| `get_current_time` | 返回当前时间, 支持时区 |
| `get_weather` | 调 [wttr.in](https://wttr.in) 查实时天气 (中英文城市名) |
| `search_files` | 在目录里按 glob 模式搜索文件 |
| `send_rabbit_message` | 调本地 RabbitMQ HTTP 网关 (`127.0.0.1:8081`) 发消息 |
| `run_shell` | 执行 shell 命令, Windows 自动 `chcp 65001` 防中文乱码 |
| `read_file` | 读文件, 带行号 (`cat -n` 风格), 支持起止行 |
| `write_file` | 写文件, 自动建父目录 |
| `edit_file` | 精确字符串替换, `old` 必须唯一匹配 |
| `web_fetch` | 抓 URL, HTML 自动转纯文本 |
| `http_request` | 通用 HTTP 客户端 (GET/POST/PUT/DELETE/PATCH/HEAD) |
| `grep_search` | 在目录里按正则搜索, 输出 `file:行号: 片段` 格式 |

## 目录结构

```
aiagent/
├── agent.yaml              # 唯一配置文件 (提示词/模型/工具)
├── requirements.txt
├── cli.py                  # CLI 入口
├── main.py                 # cli 别名
├── README.md
├── .gitignore
├── agent/                  # 核心库
│   ├── __init__.py
│   ├── config.py           # Pydantic 配置模型 + ${ENV} 展开
│   ├── context.py          # 上下文/系统提示管理
│   ├── memory.py           # 多轮对话记忆 + 截断
│   ├── model.py            # OpenAI 兼容模型客户端 (流式 + 工具调用)
│   ├── tools.py            # 工具注册表 (schema + 执行 + 必填校验)
│   └── core.py             # Agent 主循环 (ReAct + tool call)
└── tools/                  # 工具实现
    ├── time.py             # get_current_time
    ├── weather.py          # get_weather (wttr.in)
    ├── search_files.py     # search_files
    ├── rabbit.py           # send_rabbit_message
    ├── shell.py            # run_shell
    ├── files.py            # read_file / write_file / edit_file
    ├── web.py              # web_fetch (HTML→text)
    ├── http.py             # http_request
    └── grep.py             # grep_search
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

在 `agent.yaml` 里把 `model.api_key` 改成你自己的 key, 或者用环境变量:

```bash
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."

# 或在 agent.yaml 里用占位符
# api_key: ${OPENAI_API_KEY}
```

### 3. 跑起来

```bash
# 交互模式 (默认 WARNING 级别, 不打 INFO 日志)
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

交互命令: `/quit` 退出, `/reset` 清空对话, `/tools` 列工具。

## 切换模型后端

`model.base_url` + `model.name` 决定访问哪个端点, 任何 OpenAI 兼容服务都能直接用。

| 服务 | base_url | name 示例 |
|------|----------|-----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 DashScope (兼容模式) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama (本地) | `http://localhost:11434/v1` | `qwen2.5:7b-instruct` |
| llama.cpp server (本地) | `http://localhost:8080/v1` | `local` |

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

  -c, --config CONFIG    配置文件路径 (默认 agent.yaml)
  --print-config         打印配置后退出
  -v, --verbose          显示 INFO 级日志 (默认隐藏, 含每次 tool call)
  --debug                显示 DEBUG 级日志 (排查问题用)
  -q, --quiet            只显示 ERROR 及以上 (静默模式)
  -m, --message MESSAGE  单轮模式: 直接发一条消息并打印回答
```

## 配置字段说明

| 字段 | 说明 |
|------|------|
| `agent.name` | 仅用于日志/CLI banner |
| `context.system_prompt.template` | 系统提示词模板 |
| `context.system_prompt.variables` | 模板变量, 运行时可覆盖 |
| `context.max_history_messages` | 记忆里最多保留多少条消息 |
| `context.max_history_tokens` | 粗略 token 预算, 超出截断 |
| `model.backend` | 当前仅 `openai` |
| `model.name` | 模型名 |
| `model.base_url` | API 端点 |
| `model.api_key` | API key, 支持 `${ENV_VAR}` 占位 |
| `model.temperature` / `max_tokens` / `timeout` / `stream` | 标准参数 |
| `tools[].name` | 工具名 (全英文, 模型用此调用) |
| `tools[].description` | 工具描述 (模型靠这个判断何时调用) |
| `tools[].enabled` | 是否启用 |
| `tools[].parameters` | JSON Schema, 定义参数 |
| `tools[].handler` | 形如 `pkg.mod:func` 的可调用对象路径 |
| `loop.max_iterations` | 防止 tool call 死循环 |
| `loop.tool_timeout` | 单个 tool 调用的最长秒数 |

## 已知限制 (MVP)

- 只支持 OpenAI 兼容协议 (其他后端后续可加)
- 只支持原生 function calling (ReAct 文本解析模式后续可加)
- 记忆截断是简单 FIFO, 没有摘要压缩
- 单进程, 不支持服务端多用户
- `run_shell` 没有任何安全沙箱, agent 拿到这个工具等于能跑任何命令

## 后续可加的东西

- ReAct 文本解析作为 fallback, 给 4B 以下小模型用
- 对话持久化 (SQLite / JSON)
- FastAPI 服务化
- 流式输出 token 到前端 (SSE / WebSocket)
- `run_shell` 的白名单/沙箱
- Tool 调用结果缓存 (相同输入直接返回)
