# 深度思考模式 Implementation Plan

> 状态：**已执行完毕**（2026-09-10）。执行记录与验收见 `docs/PROJECT-HANDOFF.md` 第 6.7 节与 `test-results/latest-deep-smoke.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给帕姆 Agent 增加可选的「深度思考」模式：以 ReAct 循环反复查询列车智库与星间情报（必要时读网页正文），直到攒够依据再以帕姆口吻作答，查不到时如实说明。

**Architecture:** 新增 `app/deepthink.py`（原生 function calling 的 ReAct 循环，`run_stream` 产事件流、`run` 收集结果）；`ChatEngine` 把现有智能检索、搜狗联网、网页正文抽取注入为三个工具，并统一成 `stream_reply` 事件流；`/api/chat` 增加 `mode`，另加 `/api/chat/stream` SSE 端点；前端加开关与实时思考轨迹。

**Tech Stack:** Python 3.10、FastAPI/Starlette、openai SDK（`tools`）、httpx、pytest、原生 HTML/CSS/JS。

**Spec:** `docs/superpowers/specs/2026-09-10-deep-thinking-mode-design.md`

## Global Constraints

- 人设底线 `PERSONA_CORE` 不改：自称帕姆/列车长、称用户开拓者、句尾「帕」不连用、不提现实世界与 AI 身份。
- 剧情事实必须来自 `search_knowledge_base` 片段；联网结果只能标为攻略参考。
- 无答案协议：查不到就说查不到；禁止「游戏里根本没有」这类越界断言；禁止编造。
- `mode` 默认 `normal`，普通模式路径与响应字段（含 `reply`）保持兼容。
- 只用 `tool_choice="auto"`（真实模型对 `required` 返回 400）。
- 循环默认上限 6 轮 / 180 秒，任何情况下都必须收敛到一条回答。
- 现有 52 条测试不得回归；每条新行为先写失败测试。

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/websearch.py` | 新增 `html_to_text` / `fetch_page_text` | Modify |
| `app/persona.py` | 新增 `DEEP_THINK_RULES` / `build_deep_think_prompt` | Modify |
| `app/config.py` | 新增 6 个 `deep_think_*` 配置项 | Modify |
| `app/deepthink.py` | ReAct 循环、工具执行、收敛与无答案协议 | Create |
| `app/chat.py` | 注入工具、`stream_reply`/`reply(mode=)`、诊断 | Modify |
| `app/main.py` | `mode` 校验、`steps`、`/api/chat/stream` | Modify |
| `static/*` | 深度思考开关 + 思考轨迹 | Modify |
| `tests/test_deepthink.py` | 循环行为测试 | Create |
| `tests/test_websearch.py`、`test_persona.py`、`test_chat.py`、`test_api.py` | 新增用例 | Modify |

---

### Task 1: 网页正文抽取（`fetch_page` 底座）

**Files:**
- Modify: `app/websearch.py`
- Test: `tests/test_websearch.py`

**Interfaces:**
- Produces: `html_to_text(html: str) -> str`；`fetch_page_text(url: str, max_chars: int = 4000, timeout: float = 10.0) -> str`（失败返回 `""`）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_websearch.py`）

```python
from app.websearch import fetch_page_text, html_to_text


def test_html_to_text_strips_scripts_and_tags():
    html = (
        "<html><head><style>p{color:red}</style><script>var a=1;</script></head>"
        "<body><h1>银狼配装</h1><p>推荐遗器：<b>废土客</b>。</p></body></html>"
    )
    text = html_to_text(html)
    assert "银狼配装" in text and "废土客" in text
    assert "var a=1" not in text and "color:red" not in text
    assert "<" not in text


def test_fetch_page_text_truncates_and_rejects_localhost(monkeypatch):
    class Resp:
        text = "<html><body><p>" + "长" * 500 + "</p></body></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.websearch.httpx.get", lambda *a, **k: Resp())
    assert len(fetch_page_text("https://example.com/a", max_chars=50)) == 50
    assert fetch_page_text("http://127.0.0.1:8000/") == ""
    assert fetch_page_text("file:///c:/secret.txt") == ""


def test_fetch_page_text_returns_empty_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.websearch.httpx.get", boom)
    assert fetch_page_text("https://example.com/x") == ""
```

- [ ] **Step 2: 运行确认失败** —— `.venv\Scripts\python.exe -m pytest tests/test_websearch.py -q`，Expected: FAIL（`ImportError: cannot import name 'html_to_text'`）
- [ ] **Step 3: 实现**

```python
_BLOCKED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def html_to_text(page: str) -> str:
    """Strip scripts/styles/tags and return readable plain text."""
    page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    page = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", page)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_page_text(url: str, max_chars: int = 4000, timeout: float = 10.0) -> str:
    """Fetch a public http(s) page and return plain text; '' on any failure."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return ""
    if any(host in url.lower() for host in _BLOCKED_HOSTS):
        return ""
    try:
        resp = httpx.get(
            url, headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=True
        )
        resp.raise_for_status()
        return html_to_text(resp.text)[:max_chars]
    except Exception:
        return ""
```

- [ ] **Step 4: 测试通过** —— Expected: PASS
- [ ] **Step 5: 提交** —— `git commit -m "feat: page-text extraction for deep-think web reading"`

---

### Task 2: 深度思考人设提示词

**Files:**
- Modify: `app/persona.py`
- Test: `tests/test_persona.py`

**Interfaces:**
- Produces: `build_deep_think_prompt(pom_pom_samples: list[Chunk], max_steps: int = 6) -> str`

- [ ] **Step 1: 写失败测试**

```python
from app.persona import PERSONA_CORE, build_deep_think_prompt


def test_deep_think_prompt_keeps_persona_and_adds_loop_rules():
    prompt = build_deep_think_prompt([], max_steps=4)
    assert PERSONA_CORE in prompt
    assert "【深度思考工作方式】" in prompt
    assert "search_knowledge_base" in prompt
    assert "4" in prompt
    assert "游戏里根本没有" in prompt  # 明确禁止越界断言


def test_deep_think_prompt_includes_style_samples():
    chunk = Chunk(1, "story", "s", "帕姆", "列车马上要出发了帕！", True, "")
    assert "列车马上要出发了帕！" in build_deep_think_prompt([chunk])
```

- [ ] **Step 2: 运行确认失败** —— Expected: FAIL（`ImportError`）
- [ ] **Step 3: 实现** —— 新增 `DEEP_THINK_RULES`（含 `{max_steps}` 占位）与 `build_deep_think_prompt`：拼 `PERSONA_CORE` + 台词风格参考 + 深度思考规则。规则要点：可反复查证；查询词逐次更精确、不重复同一查询；够用立刻停止调用并作答；事实只认智库片段；联网仅作攻略参考；查不到时如实说明且不许说「游戏里根本没有」；回答里不得出现「工具/函数/模型/系统」等字眼。
- [ ] **Step 4: 测试通过** —— Expected: PASS
- [ ] **Step 5: 提交** —— `git commit -m "feat: deep-think system prompt with no-answer protocol"`

---

### Task 3: ReAct 循环引擎 `app/deepthink.py`

**Files:**
- Create: `app/deepthink.py`
- Modify: `app/config.py`
- Test: `tests/test_deepthink.py`

**Interfaces:**
- Produces: `TOOL_SPECS`；`DeepThinkResult(reply, steps, rounds, tool_calls, evidence, insufficient, fallback)`；`NO_ANSWER_REPLY`；`DeepThinkEngine(settings, llm, kb_search, web_search, fetch_page=None, samples_fn=None, now=time.time)` 的 `run(question, history)` 与 `run_stream(question, history)`。
- Consumes: `kb_search(query, k) -> list[Chunk]`、`web_search(query, max_results) -> list[dict]`、`fetch_page(url, max_chars) -> str`、`samples_fn(n) -> list[Chunk]`、`persona.build_deep_think_prompt`、`persona.format_context`。

- [ ] **Step 1: 写失败测试**（`tests/test_deepthink.py`：`ScriptedLLM` 按脚本依次返回 `tool_calls` 或最终文本；`_resp()` 造 openai 兼容响应）

```python
def test_deep_think_searches_knowledge_base_then_answers(...)
    # 轮 1: tool_calls=[search_knowledge_base("姬子")]；轮 2: content="姬子是领航员帕。"
    # 断言 reply 来自轮 2；steps[0]["action"] == "search_knowledge_base"；kb 收到 "姬子"
    # 断言 messages 里有 role=tool 的观测；result.insufficient is False


def test_deep_think_uses_web_and_records_titles(...)
def test_duplicate_tool_call_is_not_re_executed(...)          # kb 只被调 1 次，观测含「刚刚查过」
def test_max_steps_forces_final_answer_without_tools(...)      # 收尾调用 kwargs 无 tools
def test_stall_detection_stops_after_two_rounds_without_new_evidence(...)
def test_no_evidence_marks_insufficient_and_adds_strict_instruction(...)
def test_deterministic_no_answer_reply_when_final_call_fails(...)
def test_first_round_failure_returns_fallback(...)
def test_run_stream_yields_steps_then_final(...)
def test_web_tools_hidden_when_web_search_disabled(...)
```

- [ ] **Step 2: 运行确认失败** —— Expected: FAIL（模块不存在）
- [ ] **Step 3: 实现** —— `config.py` 先加 `deep_think_max_steps=6`、`deep_think_timeout_seconds=180.0`、`deep_think_llm_timeout_seconds=60.0`、`deep_think_max_tool_chars=1200`、`deep_think_max_fetches=2`、`deep_think_fetch_enabled=True`（均支持环境变量覆盖），再实现循环：

```python
def run_stream(self, question, history):
    state = _State()
    messages = self._build_messages(question, history)
    deadline = self._now() + self.settings.deep_think_timeout_seconds
    reply, fallback = "", False
    for round_index in range(1, self.settings.deep_think_max_steps + 1):
        if self._now() >= deadline:
            break
        try:
            msg = self._call_llm(messages, timeout=self._round_timeout(deadline))
        except Exception as exc:
            state.last_error = f"{type(exc).__name__}: {exc}"
            if not state.evidence and not state.tool_calls:
                yield {"type": "final", "result": self._result(FALLBACK_REPLY, state, True)}
                return
            break
        state.rounds = round_index
        calls = list(getattr(msg, "tool_calls", None) or [])
        if not calls:
            reply = (msg.content or "").strip()
            break
        messages.append(_assistant_tool_message(msg))
        gained = 0
        for call in calls:
            step, observation, new = self._execute(call, state)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
            yield {"type": "step", **step}
            gained += new
        state.stalled = state.stalled + 1 if gained == 0 else 0
        if state.stalled >= 2:
            break
    if not reply:
        reply, fallback = self._finalize(messages, state, deadline)
    yield {"type": "final", "result": self._result(reply, state, fallback)}
```

  - `_execute`：按 `(name, args_json)` 去重；智库用 `format_context` 渲染、记录新 chunk 数；联网渲染 `- 《标题》 URL` + 摘要；`fetch_page` 受开关与 `max_fetches` 限制；观测统一截断到 `deep_think_max_tool_chars`。
  - `_finalize`：无证据且用过工具时追加系统指令「你刚才的查询都没有结果。请如实告知开拓者智库里没有这段记录，不要编造，也不要说游戏里根本没有。」；调用 LLM **不带** `tools`；异常时无证据 → `NO_ANSWER_REPLY`（查过了确实没有），有证据 → `FALLBACK_REPLY`。注意区分：**首轮**就调用失败（一条都没查过）属于接口故障，走 `FALLBACK_REPLY` 而不是「查无记录」。
- [ ] **Step 4: 测试通过** —— Expected: PASS
- [ ] **Step 5: 提交** —— `git commit -m "feat: ReAct deep-think engine with tool loop and no-answer protocol"`

---

### Task 4: 接入 `ChatEngine`（工具注入 + 统一事件流）

**Files:**
- Modify: `app/chat.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces: `ChatEngine.stream_reply(user_message, history, mode="normal") -> Iterator[dict]`（`step` / `reply` 事件）；`ChatEngine.reply(..., mode="normal") -> str`；诊断新增 `mode`、`steps`、`llm_rounds`、`insufficient`、`deep_think_tool_calls`。

- [ ] **Step 1: 写失败测试**

```python
def test_deep_mode_runs_tool_loop_and_reports_steps(tmp_path):
    # 轮 1: tool_call search_knowledge_base("姬子")；轮 2: content="姬子是领航员帕。"
    events = list(engine.stream_reply("姬子是谁？", [], mode="deep"))
    assert events[0]["type"] == "step"
    assert events[0]["action"] == "search_knowledge_base"
    assert events[-1]["type"] == "reply" and "姬子" in events[-1]["reply"]
    assert engine.last_diagnostics()["mode"] == "deep"


def test_normal_mode_still_returns_single_reply_event(tmp_path):
    events = list(engine.stream_reply("你好", []))
    assert [e["type"] for e in events] == ["reply"]


def test_reply_still_returns_string_and_records_diagnostics(tmp_path):
    assert isinstance(engine.reply("你好", []), str)
```

- [ ] **Step 2: 运行确认失败** —— Expected: FAIL（`stream_reply` 不存在）
- [ ] **Step 3: 实现** —— `stream_reply` 作为唯一执行路径：重置 `self._stats`（含 `steps: []`）→ `self._llm is None` 时直接产出 `NO_KEY_REPLY` 的 `reply` 事件 → `mode == "deep"` 时消费 `self._deep.run_stream(...)`，把 `step` 事件累进 `_stats["steps"]` 并透传，把 `final` 转成 `reply` 事件 → 普通模式沿用现有 `_build_messages` + 3 次重试，结果包成单个 `reply` 事件。`reply()` 收集事件并返回字符串（签名向后兼容）。`_record_diag` 增加新模式字段。
- [ ] **Step 4: 全量测试通过** —— `.venv\Scripts\python.exe -m pytest -q`（`tests/test_chat.py` 的 `_settings()` 等构造点同步补齐新默认值）
- [ ] **Step 5: 提交** —— `git commit -m "feat: wire deep-think engine into ChatEngine event stream"`

---

### Task 5: API（`mode` 参数 + SSE 端点）

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `POST /api/chat`（`mode` 入参、`mode`/`steps` 出参）、`POST /api/chat/stream`（SSE `step` / `reply` / `done`）、`/api/debug/info` 增加深度思考配置。

- [ ] **Step 1: 写失败测试**

```python
class StubEngine:
    def reply(self, message, history, mode="normal"):
        self.mode = mode
        return "本帕姆在哦！"

    def stream_reply(self, message, history, mode="normal"):
        yield {"type": "step", "round": 1, "action": "search_knowledge_base",
               "input": "姬子", "hits": 2, "new_hits": 2, "observation": "…"}
        yield {"type": "reply", "reply": "姬子帕。", "mode": mode,
               "insufficient": False, "rounds": 2, "steps": []}

    def last_diagnostics(self):
        return {"mode": self.mode, "steps": [{"action": "search_knowledge_base"}]}


def test_chat_passes_mode_and_returns_steps(): ...   # 200，json["mode"] == "deep"，json["steps"] 非空
def test_chat_rejects_unknown_mode(): ...            # mode="turbo" → 422
def test_chat_stream_emits_step_reply_done(): ...    # 解析 SSE data 行得到 step → reply → done
```

- [ ] **Step 2: 运行确认失败** —— Expected: FAIL
- [ ] **Step 3: 实现** —— `ChatRequest.mode: Literal["normal", "deep"] = "normal"`；`ChatResponse` 加 `mode` / `steps`（取自 `last_diagnostics()`）；新增流式端点：

```python
    @app.post("/api/chat/stream")
    def chat_stream(req: ChatRequest):
        engine = app.state.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="chat engine not ready")

        def events():
            for event in engine.stream_reply(req.message, req.history, mode=req.mode):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            diag = engine.last_diagnostics() if hasattr(engine, "last_diagnostics") else {}
            record_debug(diag)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
```

- [ ] **Step 4: 测试通过** —— `.venv\Scripts\python.exe -m pytest tests/test_api.py -q`
- [ ] **Step 5: 提交** —— `git commit -m "feat: deep mode API and SSE thinking stream"`

---

### Task 6: 前端「深度思考」开关与思考轨迹

**Files:**
- Modify: `static/index.html`、`static/app.js`、`static/style.css`

- [ ] **Step 1: `index.html`** —— composer 内、发送按钮前插入：

```html
<label class="deep-toggle" title="让帕姆反复查智库和星间情报后再回答">
  <input type="checkbox" id="deep">
  <span>深度思考</span>
</label>
```

- [ ] **Step 2: `app.js`** —— 新增 `sendDeep(message)`：`fetch("/api/chat/stream", {method: "POST", ...})`，`resp.body.getReader()` 读流，按 `\n\n` 切 SSE 块、取 `data:` 行 `JSON.parse`；`step` → 追加到思考块（`🔍/🌐/📖` + `input` + 命中数），`reply` → 写回答气泡并收起思考块，`done` → 收尾；`send()` 按 `deepCheckbox.checked` 分流，普通路径不变。

```js
const DEEP_ICONS = { search_knowledge_base: "🔍", search_web: "🌐", fetch_page: "📖" };
```

- [ ] **Step 3: `style.css`** —— `.deep-toggle`（小号、金色描边、勾选高亮）、`.think`（半透明面板、小字、`max-height` + 滚动）、`.think-step`。
- [ ] **Step 4: 手工验证** —— 起服务，开深度思考问「银狼遗器怎么配」，确认轨迹逐条出现、回答随后到达、思考块可折叠；关掉开关再问一次，确认与旧行为一致。
- [ ] **Step 5: 提交** —— `git commit -m "feat: deep-think toggle and live thinking trace in UI"`

---

### Task 7: 文档与端到端验证

**Files:**
- Modify: `README.md`、`docs/PROJECT-HANDOFF.md`、`.env.example`

- [ ] **Step 1:** `.env.example` 与 README 配置表补 6 个 `DEEP_THINK_*` 变量；README 增「深度思考模式」功能说明（开关、耗时预期、无答案协议）。
- [ ] **Step 2:** `PROJECT-HANDOFF.md` 增补模块职责（`app/deepthink.py`）、新端点、诊断字段、已知限制（更慢更费 token）。
- [ ] **Step 3:** 全量测试 `.venv\Scripts\python.exe -m pytest -q`，Expected: 全绿（52 + 新增）。
- [ ] **Step 4:** 真实链路烟测：`mode=deep` 各问一道剧情题、一道查无此题（如「XX 星的剧情是怎样的」）、一道攻略题，检查思考轨迹、`insufficient` 标记、无编造、无越界断言，结果写入 `test-results/`。
- [ ] **Step 5:** 提交 `git commit -m "docs: document deep thinking mode"`

---

## Self-Review

- **Spec 覆盖**：§3 接口 → Task 5；§4 循环 → Task 3；§5 工具 → Task 1/3；§6 无答案协议 → Task 2/3；§7 提示词 → Task 2；§8 配置 → Task 3；§9 诊断与前端 → Task 4/5/6；§10 测试 → 各任务；§11 非目标未实现，符合预期。
- **类型一致性**：`DeepThinkResult` 字段与 `ChatEngine` 诊断字段一致（`rounds`/`tool_calls`/`evidence`/`insufficient`/`fallback`）；`run_stream` 事件的 `type` 取值在 Task 3/4/5/6 中一致（`step`/`reply`/`done`）。
- **占位符扫描**：测试与关键实现均给出可落地代码；前端改动给出确切选择器与事件分流规则。
