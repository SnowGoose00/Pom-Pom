"""Run plot-fact probes against the live model and save results.

Usage:
    python scripts/run_plot_probes.py

Results are saved to test-results/plot-probes-<timestamp>.jsonl|.md plus
latest-plot-probes.jsonl|.md.

保留兼容：这些用例已并入 eval/testset.json（llm 档），新工作请用 scripts/run_eval.py；
两边漂移会被 tests/test_eval_set.py 拦下。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import ChatEngine, FALLBACK_REPLY, NO_KEY_REPLY
from app.config import load_settings
from app.database import VectorDB
from app.embedder import Embedder

PROBES_PATH = Path(__file__).resolve().parent / "plot_probes.json"


def ask(engine: ChatEngine, question: str) -> str:
    for attempt in range(3):
        reply = engine.reply(question, [])
        if reply not in (FALLBACK_REPLY, NO_KEY_REPLY) and not reply.startswith("EXC:"):
            return reply
        print(f"  transient/fallback on attempt {attempt + 1}, retrying...", flush=True)
        time.sleep(5)
    return reply


def evaluate(reply: str, probe: dict) -> dict:
    must_any = probe.get("must_any", [])
    any_ok = (not must_any) or any(k in reply for k in must_any)
    missing_any = [] if any_ok else must_any
    missing_all = [k for k in probe.get("must_all", []) if k not in reply]
    violated = [k for k in probe.get("avoid", []) if k in reply]
    passed = any_ok and not missing_all and not violated
    return {
        "passed": passed,
        "missing_any": missing_any,
        "missing_all": missing_all,
        "violated_avoid": violated,
    }


def to_markdown(rows: list[dict]) -> str:
    passed = sum(1 for r in rows if r["passed"])
    lines = [
        "# 剧情事实探针记录",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 通过：{passed}/{len(rows)}",
        "",
    ]
    for row in rows:
        status = "PASS" if row["passed"] else "FAIL"
        lines.append(f"### [{status}] {row['id']}")
        lines.append(f"Q: {row['question']}")
        lines.append(f"A: {row['reply']}")
        if not row["passed"]:
            detail = []
            if row["missing_any"]:
                detail.append("缺少任一: " + "、".join(row["missing_any"]))
            if row["missing_all"]:
                detail.append("缺少全部: " + "、".join(row["missing_all"]))
            if row["violated_avoid"]:
                detail.append("出现禁用词: " + "、".join(row["violated_avoid"]))
            lines.append("问题: " + "；".join(detail))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    probes = json.loads(PROBES_PATH.read_text(encoding="utf-8"))
    settings = load_settings()
    engine = ChatEngine(
        settings, VectorDB(settings.db_path), Embedder(settings.model_cache_dir)
    )
    rows = []
    for i, probe in enumerate(probes, 1):
        print(f"[{i}/{len(probes)}] {probe['id']}", flush=True)
        replies: list[str] = []
        evaluation = {"passed": False, "missing_any": [], "missing_all": [], "violated_avoid": []}
        for attempt in range(2):  # retry once to reduce model variance
            reply = ask(engine, probe["question"])
            replies.append(reply)
            evaluation = evaluate(reply, probe)
            print(
                f"  attempt {attempt + 1}: {'PASS' if evaluation['passed'] else 'FAIL'} -> {reply[:70]!r}",
                flush=True,
            )
            if evaluation["passed"]:
                break
        rows.append(
            {
                "id": probe["id"],
                "question": probe["question"],
                "reply": replies[-1],
                "attempts": replies,
                **evaluation,
            }
        )

    result_dir = ROOT / "test-results"
    result_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H%M%S")
    json_path = result_dir / f"plot-probes-{ts}.jsonl"
    md_path = result_dir / f"plot-probes-{ts}.md"
    json_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    md_path.write_text(to_markdown(rows), encoding="utf-8")
    for target, source in (
        (result_dir / "latest-plot-probes.jsonl", json_path),
        (result_dir / "latest-plot-probes.md", md_path),
    ):
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\nresults saved to:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
