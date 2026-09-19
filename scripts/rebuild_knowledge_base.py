"""Rebuild the knowledge base: extract the game data, then import it.

    .venv\\Scripts\\python.exe scripts/rebuild_knowledge_base.py
    .venv\\Scripts\\python.exe scripts/rebuild_knowledge_base.py --skip-extract

Extraction takes seconds; importing (chunking + embedding 270k records) takes
tens of minutes. The import validates its input up front and swaps the store in
a single transaction, so a failure or a Ctrl+C leaves the previous knowledge
base untouched.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "StarRailData"))
    parser.add_argument("--dialogues-dir", default=str(ROOT / "data" / "dialogues"))
    parser.add_argument("--db", default=str(ROOT / "data" / "pom.db"))
    parser.add_argument("--models", default=str(ROOT / "data" / "models"))
    parser.add_argument("--skip-extract", action="store_true")
    args = parser.parse_args()

    started = time.time()
    if not args.skip_extract:
        from app.extract import extract_all

        print(f"[1/2] 提取语料 {args.data_dir} -> {args.dialogues_dir}", flush=True)
        extract_all(args.data_dir, args.dialogues_dir)
        print(f"      完成，用时 {time.time() - started:.1f}s", flush=True)

    from app.embedder import Embedder
    from app.import_data import import_from_dialogues

    print(f"[2/2] 切块 + 向量化 + 入库 {args.db}", flush=True)
    print("      这一步最慢（约 30-40 分钟），中途中断不会破坏旧库", flush=True)
    embed_started = time.time()
    count = import_from_dialogues(
        args.dialogues_dir, args.db, Embedder(args.models)
    )
    print(
        f"      完成：{count} 个片段，用时 {time.time() - embed_started:.1f}s"
        f"（总计 {time.time() - started:.1f}s）",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
