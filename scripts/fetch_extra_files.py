"""Download extra game-data files that the sparse checkout does not cover.

github.com git access is unreliable here (Connection reset), so this pulls files
straight from raw.githubusercontent.com using the GitHub tree API for the listing.

Groups:
  mission - Config/Level/Mission/**/Act*.json      (主线/支线演出对白)
  rogue   - Config/Level/Rogue*, ExcelOutput/Rogue*.json  (模拟宇宙对白与配置)

Usage:
  .venv\\Scripts\\python.exe scripts/fetch_extra_files.py --group rogue
  .venv\\Scripts\\python.exe scripts/fetch_extra_files.py --group mission
  .venv\\Scripts\\python.exe scripts/fetch_extra_files.py --group rogue --limit 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

REPO = "DimbreathBot/TurnBasedGameData"
BRANCH = "main"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "StarRailData"

GROUPS: dict[str, dict] = {
    "mission": {
        "trees": ("Config/Level/Mission",),
        "match": ("Act",),
        "excel_prefixes": (),
    },
    "rogue": {
        "trees": ("Config/Level/Rogue", "Config/Level/RogueDialogue"),
        "match": (),
        "excel_prefixes": ("Rogue",),
    },
    # 全量 ExcelOutput（用于"某个文本到底被哪个配置引用"的排查）
    "excel": {
        "trees": (),
        "match": (),
        "excel_prefixes": ("",),
    },
}


def tree_files(tree_path: str, match: tuple[str, ...]) -> list[str]:
    response = httpx.get(
        f"{TREE_API}/{BRANCH}:{tree_path}",
        params={"recursive": "1"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("truncated"):
        raise RuntimeError("tree listing truncated; cannot enumerate Mission reliably")
    paths = []
    for item in payload["tree"]:
        if item["type"] != "blob":
            continue
        path = item["path"]
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".json"):
            continue
        if match and not name.startswith(match):
            continue
        if not path.startswith(tree_path.split("/")[0] + "/"):
            path = f"{tree_path}/{path}"
        paths.append(path)
    return paths


def excel_files(prefixes: tuple[str, ...]) -> list[str]:
    response = httpx.get(
        f"{TREE_API}/{BRANCH}:ExcelOutput", params={"recursive": "1"}, timeout=60
    )
    response.raise_for_status()
    names = [
        item["path"].rsplit("/", 1)[-1]
        for item in response.json()["tree"]
        if item["type"] == "blob"
    ]
    return [
        f"ExcelOutput/{name}"
        for name in names
        if name.endswith(".json") and name.startswith(prefixes)
    ]


def file_list(group: str) -> list[str]:
    cache = DEST / f".extra_filelist_{group}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    config = GROUPS[group]
    paths: list[str] = []
    for tree_path in config["trees"]:
        paths.extend(tree_files(tree_path, config["match"]))
    if config["excel_prefixes"]:
        paths.extend(excel_files(config["excel_prefixes"]))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(paths), encoding="utf-8")
    return paths


def download(client: httpx.Client, path: str) -> str:
    target = DEST / path
    if target.exists() and target.stat().st_size > 0:
        return "skip"
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            response = client.get(f"{RAW}/{path}", timeout=30)
            if response.status_code == 200:
                target.write_bytes(response.content)
                return "ok"
            if response.status_code == 404:
                return "missing"
        except Exception:
            pass
        time.sleep(0.5)
    return "fail"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=sorted(GROUPS), default="mission")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    paths = file_list(args.group)
    if args.limit:
        paths = paths[: args.limit]
    print(f"[{args.group}] 待下载文件: {len(paths)} -> {DEST}")

    started = time.time()
    counts = {"ok": 0, "skip": 0, "missing": 0, "fail": 0}
    with httpx.Client(headers={"User-Agent": "pom-pom-fetch"}) as client:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download, client, path): path for path in paths}
            for index, future in enumerate(as_completed(futures), 1):
                counts[future.result()] += 1
                if index % 500 == 0 or index == len(paths):
                    elapsed = time.time() - started
                    rate = index / elapsed if elapsed else 0
                    print(
                        f"  {index}/{len(paths)}  ok={counts['ok']} skip={counts['skip']} "
                        f"fail={counts['fail']}  {rate:.0f} 文件/秒",
                        flush=True,
                    )

    elapsed = time.time() - started
    print(f"完成，用时 {elapsed/60:.1f} 分钟 -> {counts}")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
