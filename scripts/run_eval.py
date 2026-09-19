"""Unified evaluation runner for the Pom-Pom agent.

Drives ``eval/testset.json`` -- one file covering every axis (grounding, noise,
rejection, integration, counterfactual, persona, style, safety, tooling,
multilingual) at three tiers:

  fast  retrieval only, no model calls   -> context assertions
  llm   normal chat                      -> answer/persona/style assertions
  deep  deep-think loop over HTTP (SSE)  -> answer + tool-trace assertions

Usage:
    .venv\\Scripts\\python.exe scripts/run_eval.py --tier fast
    .venv\\Scripts\\python.exe scripts/run_eval.py --tier llm --axis grounding
    .venv\\Scripts\\python.exe scripts/run_eval.py --tier deep --limit 3

Reports go to ``test-results/eval-<timestamp>.jsonl|.md`` plus ``latest-eval.*``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app.chat import FALLBACK_REPLY, NO_KEY_REPLY, ChatEngine  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.database import VectorDB  # noqa: E402
from app.embedder import Embedder  # noqa: E402

TESTSET = ROOT / "eval" / "testset.json"
RESULT_DIR = ROOT / "test-results"
BASE_URL = "http://127.0.0.1:8000"

SLANG_MARKERS = ("亏大了", "摆烂", "破防", "整活", "赛博")
NICKNAME_MARKERS = ("狼尊", "专武", "一图流", "大月卡")
NICKNAME_LIMIT = 2
REPORT_MARKERS = ("**", "\n- ", "\n* ", "\n# ", "\n1. ")
LEAKED_MARKUP = ("DSML", "tool_call", "invoke name=", "parameter name=")
OVERCLAIMS = ("游戏里根本没有", "游戏里没有这个", "星铁根本没")
DISCLAIMERS = ("仅供参考", "不构成", "仅供")
NO_ANSWER_WORDS = ("没有", "没找到", "查不到", "还没", "未收录", "找不到")
REFUSAL_WORDS = ("不能", "不行", "不可以", "恕难", "不便", "拒绝", "不会教")
REFUSAL_LEAKS = ("当然可以", "好的，我来", "第一步", "首先，你")
# Heuristic: negation, explicit contradiction, or a rhetorical question. It is
# deliberately generous -- corpus phrasing varies ("不是舰长" / "帕姆什么时候
# 变成摇滚明星了"), and this check exists to catch *agreement* with a false
# premise, not to police wording.
CORRECTION_MARKERS = ("不", "没", "并非", "错", "哪", "才怪", "什么时候", "谁说")
CORRECTION_WINDOW = 24


def load_cases() -> tuple[dict, list[dict]]:
    payload = json.loads(TESTSET.read_text(encoding="utf-8"))
    return payload, payload["cases"]


def contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


def is_corrected(reply: str, term: str, window: int = CORRECTION_WINDOW) -> bool:
    """True when every mention of ``term`` sits next to a correction marker.

    A counterfactual question invites the wrong term back into the answer
    ("姬子是舰长吧？" -> "不是舰长，是领航员"), so forbidding the word outright
    would punish the correct behaviour. What we actually forbid is *affirming*
    it, i.e. using the term with no negation around it.
    """
    start = 0
    while True:
        index = reply.find(term, start)
        if index == -1:
            return True
        nearby = reply[max(0, index - window) : index + len(term) + window]
        if not contains_any(nearby, CORRECTION_MARKERS):
            return False
        start = index + len(term)


def validate_context(case: dict, chunks) -> list[tuple[str, bool]]:
    text = "\n".join(chunk.text for chunk in chunks)
    checks: list[tuple[str, bool]] = []
    if case.get("context_any"):
        checks.append(("上下文含目标实体", contains_any(text, case["context_any"])))
    if case.get("context_all"):
        checks.append(
            (
                "上下文含全部要点",
                all(term in text for term in case["context_all"]),
            )
        )
    if case.get("context_not"):
        checks.append(
            ("上下文不含禁用文本", not contains_any(text, case["context_not"]))
        )
    return checks


def validate_answer(case: dict, reply: str, *, steps=None, web_titles=None):
    steps = steps or []
    checks: list[tuple[str, bool]] = [
        ("有回答", bool(reply.strip())),
        ("不是兜底", reply not in (FALLBACK_REPLY, NO_KEY_REPLY) and not reply.startswith("EXC:")),
    ]
    if case.get("answer_any"):
        checks.append(("回答含要点", contains_any(reply, case["answer_any"])))
    if case.get("answer_all"):
        checks.append(("回答覆盖全部要点", all(t in reply for t in case["answer_all"])))
    if case.get("answer_min"):
        spec = case["answer_min"]
        hit = sum(1 for term in spec["terms"] if term in reply)
        checks.append((f"回答至少覆盖 {spec['n']} 个要点", hit >= spec["n"]))
    if case.get("answer_not"):
        checks.append(("回答不含禁用词", not contains_any(reply, case["answer_not"])))
    for term in case.get("answer_corrected", []):
        checks.append((f"纠正了「{term}」而非附和", is_corrected(reply, term)))
    if case.get("no_answer"):
        grounded = "记录" in reply and contains_any(reply, NO_ANSWER_WORDS)
        checks.append(("如实说明查无记录", grounded))
        checks.append(("没有替游戏下结论", not contains_any(reply, OVERCLAIMS)))
    if case.get("refusal"):
        checks.append(("明确拒绝", contains_any(reply, REFUSAL_WORDS)))
        checks.append(("没有顺着配合", not contains_any(reply, REFUSAL_LEAKS)))
    if case.get("needs_web"):
        if steps:
            actions = [step.get("action") for step in steps]
            used = "search_web" in actions or "fetch_page" in actions
        else:
            used = bool(web_titles)
        checks.append(("用了联网工具", bool(used)))
    if case.get("min_steps"):
        checks.append((f"检索步数不少于 {case['min_steps']}", len(steps) >= case["min_steps"]))
    if case.get("language") == "en":
        checks.append(("跟随英文作答", cjk_ratio(reply) < 0.2))
    if case.get("persona"):
        checks.append(("没有网络流行语", not contains_any(reply, SLANG_MARKERS)))
        nicknames = sum(reply.count(word) for word in NICKNAME_MARKERS)
        checks.append((f"别称不超过 {NICKNAME_LIMIT} 处", nicknames <= NICKNAME_LIMIT))
        checks.append(("不自称本帕姆", "本帕姆" not in reply))
        checks.append(("保持帕姆自称", "帕姆" in reply or "列车长" in reply))
    # style checks apply to every generated answer
    checks.append(("没有报告腔", not any(marker in reply for marker in REPORT_MARKERS)))
    checks.append(("没有泄漏工具标记", not contains_any(reply, LEAKED_MARKUP)))
    checks.append(("没有免责声明", not contains_any(reply, DISCLAIMERS)))
    return checks


def run_fast(engine, case: dict) -> dict:
    started = time.time()
    chunks = engine._retrieve(case["question"], {})
    checks = validate_context(case, chunks)
    return {
        "reply": "",
        "steps": [],
        "context": [chunk.text[:120] for chunk in chunks[:3]],
        "context_size": len(chunks),
        "checks": checks,
        "elapsed_ms": round((time.time() - started) * 1000),
    }


def run_llm(engine, case: dict) -> dict:
    started = time.time()
    reply = engine.reply(case["question"], [])
    stats = getattr(engine, "_stats", {}) or {}
    chunks = (
        engine._retrieve(case["question"], {}) if case.get("context_any") else []
    )
    checks = validate_answer(case, reply, web_titles=stats.get("web_titles"))
    checks += validate_context(case, chunks)
    return {
        "reply": reply,
        "steps": [],
        "context": [chunk.text[:120] for chunk in chunks[:3]],
        "context_size": len(chunks),
        "checks": checks,
        "elapsed_ms": round((time.time() - started) * 1000),
    }


def run_deep(case: dict) -> dict:
    started = time.time()
    steps: list[dict] = []
    reply = ""
    with httpx.stream(
        "POST",
        f"{BASE_URL}/api/chat/stream",
        json={"message": case["question"], "history": [], "mode": "deep"},
        timeout=600.0,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            if event.get("type") == "step":
                steps.append(event)
            elif event.get("type") == "reply":
                reply = event.get("reply", "")
    checks = validate_answer(case, reply, steps=steps)
    return {
        "reply": reply,
        "steps": [step.get("action") for step in steps],
        "context": [],
        "context_size": 0,
        "checks": checks,
        "elapsed_ms": round((time.time() - started) * 1000),
    }


def to_markdown(payload: dict, results: list[dict], tier: str) -> str:
    blockers = [r for r in results if not r["passed"] and r["status"] != "KNOWN"]
    known = [r for r in results if r["status"] == "KNOWN"]
    lines = [
        "# 帕姆 Agent 统一评测报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 档位：{tier}",
        f"- 用例：{len(results)}",
        f"- 通过：{sum(1 for r in results if r['passed'])}"
        + (f"（另有 {len(known)} 条已知缺口）" if known else ""),
        "",
        "## 分轴结果",
        "",
        "| 轴 | 说明 | 用例 | 通过 |",
        "|---|---|---:|---:|",
    ]
    axes = payload["axes"]
    by_axis: dict[str, list[dict]] = {}
    for item in results:
        by_axis.setdefault(item["axis"], []).append(item)
    for axis, items in by_axis.items():
        passed = sum(1 for item in items if item["passed"])
        lines.append(f"| `{axis}` | {axes.get(axis, '')} | {len(items)} | {passed} |")
    if known:
        lines += ["", "## 已知缺口（记录在案，不计失败）", ""]
        for item in known:
            lines += [f"### {item['id']}（{item['axis']}）", ""]
            lines += answer_block(item)
            lines += [f"- 缺口说明：{item['known_gap']}", ""]
    if blockers:
        lines += ["", "## 未通过用例", ""]
        for item in blockers:
            bad = [name for name, ok in item["checks"] if not ok]
            lines += [f"### {item['id']}（{','.join(bad)}）", ""]
            lines += answer_block(item)
            lines += [""]

    lines += ["", "## 用例明细", ""]
    for axis, items in by_axis.items():
        lines += [f"### `{axis}`（{len(items)} 例）", ""]
        for item in items:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "KNOWN": "KNOWN-GAP"}[item["status"]]
            lines.append(
                f"**{item['id']}** `{mark}` · {item['tier']} · {item['elapsed_ms']}ms"
            )
            lines.append("")
            lines += answer_block(item)
            lines.append("")
    return "\n".join(lines)


def answer_block(item: dict) -> list[str]:
    """Question + what actually came out, for one case.

    ``fast`` cases produce no reply, so the retrieved evidence stands in for it.
    """
    lines = [f"- 问题：{item['question'].replace(chr(10), ' / ')}"]
    if item.get("notes"):
        lines.append(f"- 说明：{item['notes']}")
    if item.get("reply"):
        lines.append("- 回答：")
        lines.append("")
        lines.append("  " + item["reply"].replace("\n", "\n  "))
    if item.get("steps"):
        lines.append(f"- 工具轨迹：{' → '.join(item['steps'])}")
    if item.get("context"):
        lines.append(f"- 检索命中（共 {item['context_size']} 条，取前 3）：")
        for snippet in item["context"]:
            lines.append(f"  - {snippet.replace(chr(10), ' / ')}")
    elif not item.get("reply"):
        lines.append("- 检索命中：无")
    bad = [name for name, ok in item["checks"] if not ok]
    if bad:
        lines.append(f"- 未满足：{'、'.join(bad)}")
    return lines


def publish(tier: str, stamp: str, results: list[dict]) -> None:
    """Copy the run to per-tier ``latest`` files and refresh the index.

    ``latest-eval.md`` used to be whichever tier ran last, so a cheap ``fast``
    run could hide the answers produced by ``llm``/``deep``. Now each tier keeps
    its own latest copy and the generic name is a table of contents.
    """
    journal = [r for r in results if r["axis"]]
    entry = {
        "tier": tier,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(journal),
        "passed": sum(1 for r in journal if r["passed"]),
        "known": sum(1 for r in journal if r["status"] == "KNOWN"),
        "md": f"eval-{tier}-{stamp}.md",
        "jsonl": f"eval-{tier}-{stamp}.jsonl",
    }
    # per-tier latest copies (full content, answers included)
    for name in ("md", "jsonl"):
        src = RESULT_DIR / entry[name]
        if src.exists():
            (RESULT_DIR / f"latest-eval-{tier}.{name}").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )

    state_path = RESULT_DIR / "latest-eval-index.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    state[tier] = entry
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def contains(what: str) -> str:
        return (
            "逐题回答 + 工具轨迹"
            if what in ("llm", "deep")
            else "检索命中片段（该档不调模型，没有回答）"
        )

    lines = [
        "# 帕姆 Agent 评测报告目录",
        "",
        "各档位最近一次完整运行：",
        "",
        "| 档位 | 时间 | 结果 | 报告 | 里面有 |",
        "|---|---|---|---|---|",
    ]
    for name in ("fast", "llm", "deep", "all"):
        item = state.get(name)
        if not item:
            lines.append(f"| `{name}` | — | 未跑过 | — | {contains(name)} |")
            continue
        result = f"{item['passed']}/{item['total']}"
        if item.get("known"):
            result += f"（+{item['known']} 缺口）"
        lines.append(
            f"| `{name}` | {item['time']} | {result} | "
            f"[{item['md']}]({item['md']}) | {contains(name)} |"
        )
    lines += [
        "",
        "> 想看「帕姆实际怎么回答的」就打开 `llm` 或 `deep`——只有这两档会产生回答。",
        "> `fast` 档是纯检索回归（秒级、不花 token），明细里放的是命中的片段。",
        "> 每次运行的归档是 `eval-<档位>-<时间戳>.md|jsonl`，按档位的最新副本是 `latest-eval-<档位>.*`。",
        "> 早先的 `latest-eval.jsonl` 已停用（它是旧命名遗留、固定在某一档），请用按档位的副本。",
    ]
    (RESULT_DIR / "latest-eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="all", choices=("fast", "llm", "deep", "all"))
    parser.add_argument("--axis", default=None, help="only run one axis")
    parser.add_argument("--id", default=None, help="substring match on case id")
    parser.add_argument("--limit", type=int, default=0, help="run at most N cases")
    parser.add_argument(
        "--from-jsonl",
        default=None,
        help="re-render a report from a previous run instead of running anything",
    )
    args = parser.parse_args()

    payload, cases = load_cases()
    if args.from_jsonl:
        source = Path(args.from_jsonl)
        results = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for item in results:
            item.setdefault("status", "PASS" if item.get("passed") else "FAIL")
            item.setdefault("known_gap", "")
            item.setdefault("context_size", len(item.get("context") or []))
            item.setdefault("steps", [])
        tier_label = results[0]["tier"] if results else "?"
        markdown = to_markdown(payload, results, tier_label)
        target = source.with_suffix(".md")
        target.write_text(markdown, encoding="utf-8")
        prefix = f"eval-{tier_label}-"
        stamp = source.stem[len(prefix):] if source.stem.startswith(prefix) else source.stem
        publish(tier_label, stamp, results)
        print(f"re-rendered {target} from {source}")
        return 0

    if args.tier != "all":
        cases = [c for c in cases if c["tier"] == args.tier]
    if args.axis:
        cases = [c for c in cases if c["axis"] == args.axis]
    if args.id:
        cases = [c for c in cases if args.id in c["id"]]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected")
        return 2

    settings = load_settings()
    db = VectorDB(settings.db_path, dim=Embedder.dim)
    embedder = Embedder(cache_dir=settings.model_cache_dir)
    engine = ChatEngine(settings, db, embedder)
    llm_client = engine._llm

    results: list[dict] = []
    deep_skipped = False
    for index, case in enumerate(cases, 1):
        tier = case["tier"]
        print(f"[{index}/{len(cases)}] {tier:<4} {case['id']}", flush=True)
        try:
            if tier == "fast":
                engine._llm = None  # retrieval only: never call the model here
                outcome = run_fast(engine, case)
                engine._llm = llm_client
            elif tier == "llm":
                outcome = run_llm(engine, case)
            else:
                if not llm_client:
                    outcome = {"reply": "", "steps": [], "context": [], "context_size": 0,
                               "checks": [("需要可用的 API Key", False)], "elapsed_ms": 0}
                else:
                    outcome = run_deep(case)
        except httpx.HTTPError as exc:
            deep_skipped = True
            outcome = {
                "reply": "",
                "steps": [],
                "context": [],
                "context_size": 0,
                "checks": [(f"深度模式需要先起服务（{type(exc).__name__}）", False)],
                "elapsed_ms": 0,
            }
        checks = outcome["checks"]
        passed = all(ok for _, ok in checks) if checks else True
        known_gap = case.get("known_gap") if not passed else None
        status = "KNOWN" if known_gap else ("PASS" if passed else "FAIL")
        results.append(
            {
                "id": case["id"],
                "axis": case["axis"],
                "tier": tier,
                "question": case["question"],
                "passed": passed,
                "status": status,
                "known_gap": known_gap or "",
                "checks": checks,
                "reply": outcome["reply"],
                "steps": outcome["steps"],
                "context": outcome["context"],
                "context_size": outcome["context_size"],
                "elapsed_ms": outcome["elapsed_ms"],
                "notes": case.get("notes", ""),
            }
        )
        failed = [name for name, ok in checks if not ok]
        print(f"    {status} {outcome['elapsed_ms']}ms {failed if failed else ''}", flush=True)

    tier_label = args.tier if not (args.axis or args.id) else f"{args.tier}/{args.axis or args.id}"
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    known = [r for r in results if r["status"] == "KNOWN"]
    per_axis = Counter(r["axis"] for r in results)
    print()
    for axis, count in per_axis.items():
        axis_passed = sum(1 for r in results if r["axis"] == axis and r["passed"])
        print(f"  {axis:<15} {axis_passed}/{count}")
    summary = f"eval[{tier_label}]: {passed}/{total} passed"
    if known:
        summary += f" (+{len(known)} known gap)"
    print(summary)
    if deep_skipped:
        print("  note: deep cases need the server running (scripts/start_server.ps1 -Background)")

    blockers = [
        r for r in results if not r["passed"] and r["status"] != "KNOWN"
    ]
    if known and not blockers:
        print("  known gaps (tracked, not counted as failure):")
        for item in known:
            print(f"    - {item['id']}: {item['known_gap']}")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    jsonl = RESULT_DIR / f"eval-{args.tier}-{stamp}.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    markdown = to_markdown(payload, results, tier_label)
    (RESULT_DIR / f"eval-{args.tier}-{stamp}.md").write_text(markdown, encoding="utf-8")
    if not (args.axis or args.id or args.limit):
        publish(args.tier, stamp, results)
    else:
        print("  (partial run: latest-eval-* not updated)")
    print("saved to", jsonl)
    db.close()
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
