"""Entity probes: do the retrieved chunks actually contain the named entities?

只看检索结果（不调 LLM），所以跑得快也不花钱；用来量化"检索有没有捞到该捞的"。

Usage:
  .venv\\Scripts\\python.exe scripts/run_entity_probes.py

保留兼容：这些用例已并入 eval/testset.json（fast 档），新工作请用 scripts/run_eval.py；
两边漂移会被 tests/test_eval_set.py 拦下。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK，装不下「阮•梅」这类官方写法
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import ChatEngine  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.database import VectorDB  # noqa: E402
from app.embedder import Embedder  # noqa: E402

PROBES = json.loads((Path(__file__).parent / "entity_probes.json").read_text(encoding="utf-8"))
RESULT_DIR = ROOT / "test-results"


def main() -> int:
    settings = load_settings()
    db = VectorDB(settings.db_path, dim=Embedder.dim)
    embedder = Embedder(cache_dir=settings.model_cache_dir)
    engine = ChatEngine(settings, db, embedder)

    records = []
    hits = 0
    for probe in PROBES:
        started = time.time()
        chunks = engine._retrieve(probe["question"])
        texts = [chunk.text for chunk in chunks]
        must = probe.get("must_any") or []
        # 真正的"top-k"是进提示词的那几条：深度模式工具取 5 条、普通模式 top_k 条
        head = texts[: settings.top_k]
        head_chunks = chunks[: settings.top_k]
        found = (
            any(term in text for term in must for text in head) if must else True
        )
        unique = len(set(head))
        scenes = Counter(chunk.scene for chunk in head_chunks)
        top_scene, top_scene_count = scenes.most_common(1)[0] if scenes else ("-", 0)
        record = {
            "id": probe["id"],
            "question": probe["question"],
            "must_any": must,
            "hit": found,
            "returned": len(texts),
            "unique_texts": unique,
            "max_same_scene": top_scene_count,
            "top_scene": top_scene,
            "elapsed_ms": round((time.time() - started) * 1000),
            "sample": [text[:70] for text in texts[:3]],
        }
        records.append(record)
        hits += 1 if found else 0
        flag = "PASS" if found else "MISS"
        print(
            f"[{flag}] {probe['id']:<16} 命中 {unique}/{len(texts)} 条不同文本"
            f" 同场景最多 {top_scene_count}  ({probe['question']})",
            flush=True,
        )
        if not found:
            for text in texts[:3]:
                print(f"        - {text[:80]}")

    total = len(records)
    avg_unique = sum(r["unique_texts"] for r in records) / total
    avg_returned = sum(r["returned"] for r in records) / total
    avg_scene = sum(r["max_same_scene"] for r in records) / total
    print(
        f"\n实体命中 {hits}/{total} | 平均返回 {avg_returned:.1f} 条"
        f" | 平均不同文本 {avg_unique:.2f} | 同场景最多平均 {avg_scene:.2f}"
    )

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = RESULT_DIR / f"entity-probes-{stamp}.json"
    payload = {
        "timestamp": stamp,
        "hits": hits,
        "total": total,
        "avg_unique_texts": round(avg_unique, 2),
        "avg_returned": round(avg_returned, 2),
        "avg_max_same_scene": round(avg_scene, 2),
        "records": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULT_DIR / "latest-entity-probes.json").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    print("saved to", path)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
