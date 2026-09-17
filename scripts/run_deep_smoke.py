"""Deep-think smoke test against a running server.

Runs one plot question, one unanswerable question and one build question through
``/api/chat/stream`` with ``mode=deep``, prints the thinking trace and saves the
result to ``test-results/deep-smoke-<timestamp>.jsonl|md`` (plus ``latest`` copies).

保留兼容：这些用例已并入 eval/testset.json（deep 档），新工作请用 scripts/run_eval.py；
两边漂移会被 tests/test_eval_set.py 拦下。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = os.getenv("POM_BASE_URL", "http://127.0.0.1:8000")
RESULT_DIR = Path(__file__).resolve().parent.parent / "test-results"

CASES = (
    ("plot", "姬子和帕姆是什么关系？"),
    ("unknown", "XX 星的剧情是怎样的？"),
    ("nonexistent", "瑟兰星的剧情是怎样的？"),
    ("build", "银狼的遗器主词条怎么选？"),
    ("lv999", "银狼lv999什么配装"),
)

FORBIDDEN_OVERCLAIMS = ("游戏里根本没有", "游戏里没有这个", "星铁根本没")
LEAKED_MARKUP = ("DSML", "tool_call", "invoke name=", "parameter name=")
REPORT_MARKERS = ("**", "\n- ", "\n* ", "\n# ", "\n1. ")
SLANG_MARKERS = (
    "亏大了",
    "摆烂",
    "破防",
    "整活",
    "赛博",
)
# 别称允许少量出现（人设：官方名优先，一段回答最多一两个）
NICKNAME_MARKERS = ("狼尊", "专武", "一图流", "大月卡")
NICKNAME_LIMIT = 2


def ask(question: str, timeout: float = 600.0) -> dict:
    steps: list[dict] = []
    reply: dict = {}
    started = time.time()
    with httpx.stream(
        "POST",
        f"{BASE_URL}/api/chat/stream",
        json={"message": question, "history": [], "mode": "deep"},
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            if event.get("type") == "step":
                steps.append(event)
            elif event.get("type") == "reply":
                reply = event
    return {
        "question": question,
        "reply": reply.get("reply", ""),
        "insufficient": bool(reply.get("insufficient")),
        "no_answer": bool(reply.get("no_answer")),
        "fallback": bool(reply.get("fallback")),
        "rounds": reply.get("rounds"),
        "steps": steps,
        "elapsed_ms": round((time.time() - started) * 1000),
    }


def checks_for(kind: str, record: dict) -> list[tuple[str, bool]]:
    reply = record["reply"]
    actions = [step.get("action") for step in record["steps"]]
    checks = [
        ("有回答", bool(reply.strip())),
        ("没有兜底", not record["fallback"]),
        ("没有报告腔格式", not any(marker in reply for marker in REPORT_MARKERS)),
        ("没有网络用语/社区黑话", not any(word in reply for word in SLANG_MARKERS)),
        (
            f"别称不超过 {NICKNAME_LIMIT} 个",
            sum(reply.count(word) for word in NICKNAME_MARKERS) <= NICKNAME_LIMIT,
        ),
    ]
    if kind == "unknown":
        # A placeholder name may legitimately get a clarifying question instead of a
        # "no record" answer; both are honest, neither may over-claim.
        checks.append(
            ("没有替游戏下结论", not any(bad in reply for bad in FORBIDDEN_OVERCLAIMS))
        )
    if kind == "nonexistent":
        checks.append(("如实说明查无记录", record["no_answer"]))
        checks.append(
            ("没有替游戏下结论", not any(bad in reply for bad in FORBIDDEN_OVERCLAIMS))
        )
    if kind == "plot":
        checks.append(("角色关系正确", "领航员" in reply))
        checks.append(("没有错误头衔", "舰长" not in reply and "摇滚明星" not in reply))
    if kind == "build":
        checks.append(("用了联网工具", "search_web" in actions or "fetch_page" in actions))
    if kind == "lv999":
        checks.append(
            ("没有泄漏工具调用标记", not any(bad in reply for bad in LEAKED_MARKUP))
        )
    return checks


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for kind, question in CASES:
        print(f"[deep] {kind}: {question}", flush=True)
        try:
            record = ask(question)
        except Exception as exc:
            record = {
                "question": question,
                "reply": "",
                "steps": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        record["kind"] = kind
        checks = (
            [("请求成功", False)] if record.get("error") else checks_for(kind, record)
        )
        record["checks"] = [{"name": name, "ok": ok} for name, ok in checks]
        passed = all(ok for _, ok in checks)
        record["passed"] = passed
        records.append(record)
        for name, ok in checks:
            print(f"   {'PASS' if ok else 'FAIL'} {name}", flush=True)
        print(f"   steps: {len(record['steps'])}", flush=True)
        for step in record["steps"]:
            print(
                f"   - {step.get('action')}({step.get('input')}) hits={step.get('hits')}",
                flush=True,
            )
        print(f"   reply: {record['reply'][:200]}", flush=True)

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    jsonl = RESULT_DIR / f"deep-smoke-{stamp}.jsonl"
    with open(jsonl, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (RESULT_DIR / "latest-deep-smoke.jsonl").write_text(
        jsonl.read_text(encoding="utf-8"), encoding="utf-8"
    )

    lines = [f"# Deep-think smoke @ {stamp}", ""]
    for record in records:
        lines.append(f"## {record['kind']} · {'PASS' if record['passed'] else 'FAIL'}")
        lines.append(f"- 问题：{record['question']}")
        lines.append(
            f"- 耗时：{record.get('elapsed_ms', 0)} ms，思考步数：{len(record['steps'])}"
        )
        for step in record["steps"]:
            lines.append(
                f"  - `{step.get('action')}` 「{step.get('input')}」 hits={step.get('hits')}"
            )
        lines.append(f"- 回答：{record['reply']}")
        lines.append("")
    md = RESULT_DIR / f"deep-smoke-{stamp}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    (RESULT_DIR / "latest-deep-smoke.md").write_text(
        md.read_text(encoding="utf-8"), encoding="utf-8"
    )

    failures = [record for record in records if not record["passed"]]
    print(f"deep smoke: {len(records) - len(failures)}/{len(records)} passed -> {md}")
    for record in failures:
        print(f"FAILED: {record['kind']} {record['question']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
