# 深度思考模式（Deep Thinking Mode）设计文档

> 状态：方案已确认，进入实现
> 日期：2026-09-10
> 关联：`docs/PROJECT-HANDOFF.md`、`DESIGN.md`

## 1. 目标

在现有 RAG 问答之外，新增一个**可选的「深度思考」模式**：以 ReAct 方式让模型自己规划检索，
反复查询列车智库（本地向量库）与星间攻略情报（联网搜索），必要时打开网页读正文，
直到攒够依据再以帕姆口吻作答；查不到时如实说明，绝不编造。

**成功标准**

1. 复杂问题（多跳、需要对比、需要联网补全）比普通模式答得更完整、依据更清楚；
2. 帕姆人设与「事实必须来自智库片段 / 不编造剧情」两条底线不变；
3. 查无答案时能明确收敛（不空转、不硬编），且措辞不越界（不能说「游戏里根本没有」）；
4. 普通模式行为与现在完全一致；深度模式为逐条消息的可选项。

## 2. 已确认决策

| 决策项 | 结论 |
|---|---|
| 实现路径 | 原生 function calling 的 ReAct 循环（已用真实模型 `deepseek-v4-flash` 验证：支持 `tools` + 多工具并发调用；`tool_choice="required"` 不支持，只能用 `auto`） |
| 工具集 | `search_knowledge_base`（复用现有智能检索）、`search_web`（搜狗攻略）、`fetch_page`（读网页正文，解决「摘要偏薄」历史问题） |
| 循环上限 | 默认最多 6 轮 LLM 调用、总时限 180 秒；每轮 LLM 超时 60 秒 |
| 过程可见 | 思考步骤通过 SSE 实时推送到前端，折叠展示 |
| 兼容性 | `mode` 默认 `normal`，老客户端零影响；普通模式代码路径不改 |

## 3. 交互与接口

### 3.1 非流式

`POST /api/chat`

```json
{
  "message": "姬子和帕姆是什么关系？",
  "history": [{"role": "user", "content": "..."}],
  "mode": "deep"
}
```

响应（新增字段，`reply` 保持兼容）：

```json
{
  "reply": "姬子和帕姆是一起守护列车的老搭档帕。",
  "mode": "deep",
  "steps": [
    {"round": 1, "action": "search_knowledge_base", "input": "姬子 帕姆 关系", "hits": 4, "new_hits": 4, "observation": "【角色信息】姬子：…"}
  ]
}
```

`mode` 只接受 `normal` / `deep`，其它值返回 422。

### 3.2 流式（前端深度模式使用）

`POST /api/chat/stream`，请求体同 `/api/chat`，响应 `text/event-stream`。
每个事件是一行 `data: {json}`，JSON 内的 `type` 区分事件：

| type | 载荷 | 说明 |
|---|---|---|
| `step` | `round, action, input, hits, new_hits, observation` | 一次工具调用及其观察结果 |
| `reply` | `reply, mode, insufficient, rounds, steps` | 最终回答与循环摘要 |
| `done` | `{}` | 结束标记 |

## 4. 循环设计（ReAct）

```
提问 → [思考] → 调用工具（可并发多个）→ [观察] → 是否够用？
                    ↑                              │否
                    └──────────────────────────────┘
                                                   │是 / 到达上限
                                                   ▼
                                        以帕姆口吻给出最终回答
```

单轮流程：把 `tools` 交给模型 → 若返回 `tool_calls` 则逐个执行、把观测结果作为
`role=tool` 消息回填 → 下一轮；若返回纯文本内容（`finish_reason=stop`）即为最终回答。

**收敛保障**

- **步数上限**：`DEEP_THINK_MAX_STEPS`（默认 6）轮 LLM 调用；用满即进入「禁止再调工具」的收尾轮。
- **总时限**：`DEEP_THINK_TIMEOUT_SECONDS`（默认 180 秒）；每轮 LLM 超时取 `min(60s, 剩余时间)`。
- **去重**：相同 `(工具名, 参数)` 不重复执行，直接返回「这个查询刚刚查过了，换个词或直接作答」。
- **停滞检测**：某轮没有带来任何**新证据**（新 chunk id / 新 URL / 新网页正文）时累加停滞计数；
  连续 2 轮无新证据即停止检索，转入收尾。
- **首轮失败**：第一轮就调用失败（还没拿到任何证据）→ 直接走帕姆口吻兜底，不硬撑。

## 5. 工具定义

| 工具 | 参数 | 观测结果格式 |
|---|---|---|
| `search_knowledge_base` | `query: str`, `k: int = 5` | 复用 `persona.format_context` 的「【类别】说话人：文本」；空结果返回「（列车智库中没有找到相关记录）」 |
| `search_web` | `query: str` | `- 《标题》 URL` + 摘要；空结果返回「（没有搜到相关情报）」 |
| `fetch_page` | `url: str` | 网页正文纯文本（截断到 `DEEP_THINK_MAX_TOOL_CHARS`）；失败返回「（网页打开失败）」 |

约束：`fetch_page` 仅允许 `http/https`，拒绝 `localhost` / `127.0.0.1` / `0.0.0.0` 等本机地址；
每次深度会话最多打开 `DEEP_THINK_MAX_FETCHES`（默认 2）个网页；所有观测文本截断到
`DEEP_THINK_MAX_TOOL_CHARS`（默认 1200 字符）再回填模型。

## 6. 无答案协议

核心原则：**智库查无记录 ≠ 游戏里没有**。帕姆只能对自己的资料负责，禁止出现
「星铁根本没有这个」这类越界断言。

| 情形 | 判断依据 | 帕姆的回答 |
|---|---|---|
| 游戏里确实没这段（如「XX 星」） | 多轮智库检索无命中，联网也无结果 | 如实说智库里没有这段记录，不编 |
| 游戏里有、但智库没收录（新版本剧情） | 智库无命中，联网有相关讨论 | 说明「帕姆的智库还没更新到这段」，转述联网情报并标注仅供参考 |
| 只检索到零散无关片段 | 相似度低且补充检索不再涨新证据 | 讲清查到什么、缺什么，不硬凑因果 |
| 超纲 / 违规 | 现有题材规则 | 帕姆口吻带开 / 列车长口吻拒绝 |

代码级保障：

1. 若整轮循环**用过工具但一条证据都没拿到**（`evidence == 0 and tool_calls > 0`），
   收尾轮追加一条系统指令：「你刚才的查询都没有结果，请如实告知，不要编造」；
2. 收尾轮（不带 `tools`）调用失败时，不信任模型自由发挥：
   - 无证据 → 确定性文案 `NO_ANSWER_REPLY`（帕姆口吻「智库翻遍了也没有」）；
   - 有证据 → 现有 `FALLBACK_REPLY`；
3. 诊断记录与思考轨迹都要带 `insufficient` 标记，便于 `test-results/` 与 `/api/debug/logs` 复盘。

## 7. 人设与提示词装配

`app/persona.py` 新增 `build_deep_think_prompt(samples, max_steps)`，由三段拼成：

1. `PERSONA_CORE`（既有全文，人设底线不变）；
2. 【帕姆台词风格参考】（既有抽样台词）；
3. 【深度思考工作方式】：可反复查证、查询词要一次比一次精确、不重复同一查询、
   证据够了立刻停止调用工具并用帕姆口吻作答、回答里不得出现「工具/函数/模型」等字眼
   （要说「帕姆查了查智库」「帕姆翻了翻星间情报」）、事实必须来自 `search_knowledge_base`、
   无答案协议全文。

## 8. 配置项（`app/config.py` + `.env.example`）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEP_THINK_MAX_STEPS` | `6` | 深度模式最多 LLM 轮数 |
| `DEEP_THINK_TIMEOUT_SECONDS` | `180` | 深度模式总时限 |
| `DEEP_THINK_LLM_TIMEOUT_SECONDS` | `60` | 单轮 LLM 超时 |
| `DEEP_THINK_MAX_TOOL_CHARS` | `1200` | 单次工具观测回填上限 |
| `DEEP_THINK_MAX_FETCHES` | `2` | 每次深度会话最多打开的网页数 |
| `DEEP_THINK_FETCH_ENABLED` | `true` | 是否允许 `fetch_page` |

`WEB_SEARCH_ENABLED=false` 时 `search_web` / `fetch_page` 不对外提供。

## 9. 诊断与前端

- `ChatEngine.last_diagnostics()` 新增：`mode`、`steps`、`llm_rounds`、`insufficient`、
  `deep_think_tool_calls`；`/api/debug/logs` 直接可见完整思考链路。
- 前端输入栏新增「深度思考」开关；开启时走 `/api/chat/stream`，在回答气泡上方实时长出
  可折叠的「帕姆的思考过程」列表（🔍 查智库 / 🌐 联星间情报 / 📖 翻页），回答到达后收起；
  关闭时保持现有行为与文案。

## 10. 测试策略

- `tests/test_deepthink.py`：脚本化 FakeLLM 驱动循环 —— 多轮工具调用后收敛作答、
  联网工具与标题记录、重复查询去重、步数上限触发收尾轮、停滞检测、
  无证据时的无答案协议与确定性兜底、首轮异常兜底、`run_stream` 事件顺序。
- `tests/test_websearch.py`：新增 `html_to_text` 与 `fetch_page_text` 的解析/截断/失败用例。
- `tests/test_api.py`：`mode` 透传与非法值 422、响应带 `steps`、SSE 事件序列。
- 现有 52 条测试必须全绿（普通模式零回归）。

## 11. 非目标（本版不做）

- 不做 token 级流式输出（先只流式推送思考步骤）；
- 不做跨会话长期记忆、不做多智能体协作、不做计划树搜索；
- 不引入外部搜索 API（Tavily/Serper）与向量库结构变更。
