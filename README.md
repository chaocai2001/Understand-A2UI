# Understand A2UI：用 Codex SDK 生成 UI 的最小 Demo

一个用来**理解 A2UI 协议机制**的可运行 demo：用 OpenAI Codex SDK 作为 agent，让它输出 A2UI 协议消息（声明式 JSON），服务端经 SSE 流式推给浏览器，前端用一个约 200 行的手写迷你渲染器把 JSON 渲染成真实 UI，并把用户交互回传给 agent，形成完整闭环。

协议规范：https://a2ui.org/ （Google 主导，当前版本 v0.9.1）

## 1. A2UI 解决什么问题

让 AI agent 安全地"造界面"。传统方式只有两种极端：

- **纯文本回答** —— 表达力太弱，收集结构化输入（表单、筛选、确认）很别扭；
- **让 agent 生成 HTML/JS 执行** —— 表达力强但越过了信任边界，等于在客户端执行任意代码。

A2UI 走中间路线：agent 不输出代码，而是输出**声明式的 UI 描述**（JSON 消息流）。组件只能从客户端预先批准的目录（catalog）里选，客户端用自己的原生组件渲染。就像 agent 学会了一门"通用 UI 语言"：

1. 用户给 agent 发消息；
2. agent 生成描述 UI 的 A2UI 消息（结构 + 数据）；
3. 消息**流式**到达客户端，边到边渲染（渐进式渲染）；
4. 用户与 UI 交互（点按钮、填表单），作为 action 回传 agent；
5. agent 回复新的 A2UI 消息，**增量更新** UI。

## 2. 协议的四个核心概念

### 2.1 消息：JSONL，一行一个

A2UI 消息是 JSON Lines：每行一个完整、紧凑的 JSON 对象。这个格式对 LLM 极其友好——不需要一次性输出完美的大 JSON，可以一条一条增量生成；客户端也可以边收边渲染。本 demo 使用 v0.9 的四种消息：

| 消息 | 作用 |
|---|---|
| `createSurface` | 创建一块 UI 区域（surface），声明所用的组件目录 |
| `updateComponents` | 添加/更新组件（发同 id 即为更新） |
| `updateDataModel` | 更新数据模型（JSON Pointer 定位） |
| `deleteSurface` | 删除 surface |

### 2.2 Surface 与组件邻接表

UI 不是嵌套树，而是**扁平的组件列表**，组件之间用 id 互相引用（邻接表）。id 为 `"root"` 的组件是树根。扁平结构让 agent 可以分多条消息增量发送组件：

```json
{"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"demo-catalog"}}
{"version":"v0.9","updateComponents":{"surfaceId":"main","components":[{"id":"root","component":"Column","children":["card"]},{"id":"card","component":"Card","child":"form","title":"订餐"},{"id":"form","component":"Column","children":["name","dish","submit"]}]}}
{"version":"v0.9","updateComponents":{"surfaceId":"main","components":[{"id":"name","component":"TextField","label":"姓名","bindingPath":"/form/name"},{"id":"dish","component":"TextField","label":"菜品","bindingPath":"/form/dish"},{"id":"submit","component":"Button","label":"下单","action":{"name":"submit_order","context":{"name":{"path":"/form/name"},"dish":{"path":"/form/dish"}}}}]}}
```

> 以上是这个 demo 真实跑出来的消息流（"帮我做一个订餐表单"）。

### 2.3 数据模型与数据绑定

组件属性可以是字面量，也可以是 `{"path": "/json/pointer"}` —— 指向 surface 数据模型中的位置。`updateDataModel` 改变数据后，所有绑定该路径的组件自动重渲染：

```json
{"version":"v0.9","updateDataModel":{"surfaceId":"main","path":"/order","value":{"name":"下单人：张三","dish":"菜品：宫保鸡丁"}}}
```

这就是"结构"与"数据"分离：agent 想改文字内容时，不用重发组件，只需 patch 数据。

### 2.4 交互回传（action）

交互组件（如 `Button`）携带 `action` 描述：动作名 + context（值同样支持 path 绑定，点击时由客户端解析成实际值）。用户点击后，客户端把 action 连同当前数据模型发回 agent，agent 用新一轮 A2UI 消息响应——闭环完成。

## 3. Demo 架构：每一层对应协议的哪部分

```
浏览器 (static/index.html)                FastAPI (server/)                 Codex CLI
┌──────────────────────┐  POST /chat   ┌───────────────────────┐  spawn   ┌──────────┐
│ 聊天面板              │ ───────────▶ │ main.py               │ ◀──────▶ │ codex    │
│ A2UI 迷你渲染器       │              │ codex_bridge.py       │  JSONL   │ app-     │
│  ├ 消息状态机          │ ◀─────────── │  ├ Thread 注册表       │  事件流   │ server   │
│  ├ 组件→HTML 映射     │  SSE 事件流   │  └ 增量文本→JSONL 切分 │          └──────────┘
│  └ action 回传        │              │ a2ui_prompt.py        │     ▲
│ 原始协议消息日志        │  POST /action │  (A2UI 规范注入 agent) │ ─────┘
└──────────────────────┘ ───────────▶ └───────────────────────┘  developer_instructions
```

- **agent 侧**（`server/codex_bridge.py` + `server/a2ui_prompt.py`）：A2UI 的组件目录和格式规范通过 `developer_instructions` 注入 Codex 线程（`a2ui_prompt.py` 就是这份规范全文，本身就是一个"如何教 LLM 说 A2UI"的样例）。Codex 的流式输出按行切分，凑齐一条合法 JSON 行立即经 SSE 转发——这就是渐进式渲染在代码里的样子。
- **传输层**（`server/main.py`）：两个 SSE 端点。`/chat` 传用户消息，`/action` 传 UI 交互；都用 `text/event-stream` 返回。A2UI 本身不绑定传输协议，SSE、WebSocket、A2A 都可以。
- **客户端**（`static/index.html`）：手写迷你渲染器，对应协议消费方的三个职责：
  1. **状态机**：`applyA2UI()` 按消息类型维护 `surfaces`（组件表 + 数据模型）；
  2. **渲染**：`renderComponent()` 把组件类型映射为原生 HTML，属性经 `resolve()` 做数据绑定；
  3. **回传**：按钮点击时解析 action context，POST 回 `/action`。

## 4. 一次完整交互的走查

输入"帮我做一个订餐表单，包含姓名、菜品两个输入框和一个下单按钮"后，观察页面底部的原始消息日志：

1. `createSurface` —— 建立 surface，右侧出现虚线框；
2. 第一条 `updateComponents` —— 骨架（root/Card/Column）先渲染出来；
3. 第二条 `updateComponents` —— 输入框和按钮逐个出现（增量）；
4. `updateDataModel` —— 初始化 `/form` 数据；
5. 你在表单填入"张三 / 宫保鸡丁"，点击"下单" → 前端把 `submit_order` action + 数据模型 POST 到 `/action`；
6. agent（同一线程，记得上下文）回复新的 `updateComponents`（追加"下单成功"卡片）和 `updateDataModel`（写入 `/order`），UI 原地增量更新。

左侧对话流里每条 A2UI 消息都有可折叠的 JSON 气泡，可以和右侧渲染结果、底部原始 JSONL 三相对照。

## 5. 运行

前置条件：Python ≥ 3.13（uv 会自动处理）；Codex 已认证（`codex login` 或 `OPENAI_API_KEY`）。

```bash
uv run uvicorn server.main:app --port 8000
```

打开 http://localhost:8000 。

## 6. 代码结构

```
server/
├── main.py           # FastAPI：POST /chat、POST /action（SSE），托管 static/
├── codex_bridge.py   # session→Codex Thread 注册表；Codex 通知流 → A2UI JSONL 行级切分
└── a2ui_prompt.py    # 注入 agent 的 A2UI 组件目录与格式规范（developer_instructions）
static/
└── index.html        # 迷你 A2UI 渲染器（原生 JS，零构建）+ 聊天面板 + 原始消息日志
```

说明：Codex 以只读沙箱 + ephemeral 线程运行，不写本地文件；agent 输出中无法解析为 A2UI 的行会降级为聊天文本。demo 的组件目录是教学用子集（Column/Row/Card/Text/Image/Divider/TextField/Button），完整标准目录见协议规范。

## 参考

- A2UI 官网与规范：https://a2ui.org/
- 协议仓库（含官方 Lit/Angular/Flutter 渲染器）：https://github.com/google/A2UI
- Codex SDK：https://github.com/openai/codex （本 demo 使用 Python 镜像包 `codex-python`）
