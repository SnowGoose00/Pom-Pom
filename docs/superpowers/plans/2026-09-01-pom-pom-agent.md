# Pom-Pom Agent（帕姆 Agent）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable local Web chat MVP where an AI embodies Pom-Pom (帕姆), conductor of the Astral Express from Honkai: Star Rail, answering plot questions via vector RAG over the game's Chinese plot corpus.

**Architecture:** Python + FastAPI backend; local embedding (fastembed, BAAI/bge-small-zh-v1.5, ONNX) writes chunks into SQLite + sqlite-vec; chat retrieves top-k related plot segments, builds a persona-anchored system prompt, and calls an OpenAI-compatible LLM (default free Zhipu GLM-4.7-Flash). A static dark "star train" chat page talks to `/api/chat`.

**Tech Stack:** Python 3.10, FastAPI, uvicorn, openai SDK, fastembed + onnxruntime, sqlite-vec, SQLite, vanilla HTML/CSS/JS, pytest.

**Spec:** [DESIGN.md](/D:/Pom-Pom/DESIGN.md) (设计文档，2026-09-01)

## Global Constraints

- Chat must always stay in Pom-Pom's persona: 列车长、傲娇可爱、称用户「开拓者」、自称「本帕姆」.
- Knowledge base: GitHub `DimbreathBot/TurnBasedGameData` (main branch), Chinese (CHS) text only.
- Data download: git partial clone + sparse checkout (only Story, TextMap/TextMapCHS.json, TrainVisitor, message/book ExcelOutput files). No full repo download.
- Corpus categories (aligned with Mar7thLover/HSR-Database-Web): story (主线/支线), messages (短信), train_visitor (列车访客), books (书籍), avatars (角色信息), missions (主线任务), items (物品), monsters (怪物). Pom-Pom's own lines tagged `pom_pom=1` and prioritized for persona anchoring.
- RAG: bge-small-zh embedding on-device; SQLite + sqlite-vec vector store.
- LLM: OpenAI-compatible; `base_url` / `api_key` / `model` all from environment (`.env`); default `https://open.bigmodel.cn/api/paas/v4`, model `glm-4.7-flash`.
- Chat history: keep last 20 turns max inside a single session; no persistence across sessions.
- Failure fallback: API timeout / missing key / empty search → Pom-Pom-tone friendly fallback, never expose raw errors.
- Language: default Chinese; follow the user's language when they switch (e.g. English/Japanese).
- Out-of-scope questions: gracefully deflect in persona; violations (scam, hacking, porn etc.): refuse firmly in conductor tone.
- Do not fabricate plot: if retrieval has no relevant content, say the database doesn't have it.
- All extracted data, DB, venv, model cache are gitignored.

---

## Task 1: Project Scaffold (git, venv deps, ignore rules, env template)

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `scripts/fetch_data.ps1`

**Interfaces:**
- Consumes: nothing.
- Produces: a git repo with pinned dependencies installable via `pip install -r requirements.txt`; a reproducible data-fetch script.

- [ ] **Step 1: init git repo**

Run:
```bash
git init
```

- [ ] **Step 2: create `.gitignore`**

```gitignore
# Python
.venv/
__pycache__/
*.pyc
.pytest_cache/

# Env & secrets
.env

# Data (fetched/extracted/generated)
data/StarRailData/
data/dialogues/
data/models/
data/*.db
data/*.db-*

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 3: create `requirements.txt`**

```txt
fastapi>=0.115
uvicorn[standard]>=0.30
openai>=1.40
fastembed>=0.4
onnxruntime>=1.18
sqlite-vec>=0.1.6
python-dotenv>=1.0
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 4: create `.env.example`**

```dotenv
# 智谱开放平台 API Key（https://open.bigmodel.cn 注册获取）
ZHIPU_API_KEY=

# OpenAI 兼容接口配置（默认智谱 GLM-4.7-Flash；可切换 DeepSeek 等）
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.7-flash

# 可选覆盖（默认值见 app/config.py）
# DB_PATH=data/pom.db
# DATA_DIR=data/StarRailData
# DIALOGUES_DIR=data/dialogues
# MODEL_CACHE_DIR=data/models
# TOP_K=6
# HISTORY_TURNS=20
# TEMPERATURE=0.7
```

- [ ] **Step 5: create `scripts/fetch_data.ps1`**

```powershell
param(
    [string]$Dest = "data/StarRailData"
)

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/DimbreathBot/TurnBasedGameData.git"

if (Test-Path $Dest) {
    Write-Host "Target $Dest already exists; skip clone. Delete it first to re-fetch."
    exit 0
}

New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null

git clone --depth 1 --filter=blob:none --sparse $Repo $Dest
if ($LASTEXITCODE -ne 0) { throw "clone failed" }

git -C $Dest sparse-checkout set --no-cone `
    /Story `
    /TextMap `
    /Config/Level/Mission/TrainVisitor `
    /ExcelOutput/TalkSentenceConfig.json `
    /ExcelOutput/MessageContactsCamp.json `
    /ExcelOutput/MessageContactsConfig.json `
    /ExcelOutput/MessageItemConfig.json `
    /ExcelOutput/MessageGroupConfig.json `
    /ExcelOutput/MessageSectionConfig.json `
    /ExcelOutput/BookSeriesConfig.json
if ($LASTEXITCODE -ne 0) { throw "sparse-checkout failed" }

Write-Host "Sparse checkout done. Files:"
Get-ChildItem -Recurse -File $Dest | Measure-Object | Select-Object -ExpandProperty Count
```

- [ ] **Step 6: install dependencies into `.venv`**

Run (escalated, network):
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- [ ] **Step 7: verify install**

Run:
```bash
.venv\Scripts\python.exe -c "import fastapi, uvicorn, openai, fastembed, sqlite_vec, onnxruntime; print('deps ok')"
```
Expected: `deps ok`

- [ ] **Step 8: commit**

```bash
git add .gitignore requirements.txt .env.example scripts/fetch_data.ps1
git commit -m "chore: scaffold project with deps, env template and data fetch script"
```

---

## Task 2: Download Minimal Data Set

**Files:**
- Create (by script): `data/StarRailData/` (gitignored)

**Interfaces:**
- Consumes: `scripts/fetch_data.ps1`.
- Produces: local sparse repo with `TextMap/TextMapCHS.json`, `Story/Mission/**`, `Config/Level/Mission/TrainVisitor/Act/**`, and the 7 ExcelOutput JSON files.

- [ ] **Step 1: run fetch script (escalated, network)**

```bash
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1
```

- [ ] **Step 2: verify minimal files exist**

Run:
```bash
Get-ChildItem -Recurse -File data/StarRailData | Measure-Object | Select-Object -ExpandProperty Count
Test-Path data/StarRailData/TextMap/TextMapCHS.json
```
Expected: count in hundreds–thousands; `True`.

---

## Task 3: Extraction — Restore "Who, When, What Was Said"

**Files:**
- Create: `app/__init__.py` (empty)
- Create: `app/extract.py`
- Test: `tests/test_extract.py`
- Create: `tests/fixtures/starrail_mini/` fixture tree

**Interfaces:**
- Consumes: `data/StarRailData` layout (TextMap, Story/Mission, TrainVisitor, ExcelOutput).
- Produces:
  - `extract_all(data_dir, out_dir) -> None` — writes `data/dialogues/all.jsonl` plus `story.jsonl`, `messages.jsonl`, `train_visitor.jsonl`, `books.jsonl`.
  - Unified record dict: `{"category": str, "scene": str, "speaker": str, "text": str, "pom_pom": bool, "meta": str}` (category in `story|messages|train_visitor|books`).

- [ ] **Step 1: write failing tests + fixtures**

Fixture tree `tests/fixtures/starrail_mini/`:

`TextMap/TextMapCHS.json`:
```json
{
  "100": "帕姆",
  "101": "列车马上要出发了哦，开拓者！",
  "102": "卡芙卡",
  "103": "听我说：你脑袋里现在一片混沌。",
  "104": "黑塔",
  "105": "嘿，{NICKNAME}，我是黑塔，有好事找你",
  "106": "艾丝妲",
  "107": "如果在列车的这个位置布设空间望远镜……",
  "108": "《星穹列车见闻录》",
  "109": "星穹列车横跨星海，为开拓者开辟前路。"
}
```

`ExcelOutput/TalkSentenceConfig.json`:
```json
[
  {"TalkSentenceID": 5001, "TextmapTalkSentenceName": {"Hash": 100}, "TalkSentenceText": {"Hash": 101}},
  {"TalkSentenceID": 5002, "TextmapTalkSentenceName": {"Hash": 102}, "TalkSentenceText": {"Hash": 103}},
  {"TalkSentenceID": 6001, "TextmapTalkSentenceName": {"Hash": 106}, "TalkSentenceText": {"Hash": 107}}
]
```

`Story/Mission/1010101/main.json`:
```json
{
  "OnStartSequece": [
    {
      "TaskList": [
        {"$type": "RPG.GameCore.PlayAndWaitSimpleTalk", "SimpleTalkList": [
          {"TalkSentenceID": 5001},
          {"TalkSentenceID": 5002}
        ]},
        {"$type": "RPG.GameCore.EndPerformance"}
      ]
    }
  ]
}
```

`Story/Discussion/Mission/2000201/DS200020101.json`:
```json
{
  "OnStartSequece": [
    {
      "TaskList": [
        {"$type": "RPG.GameCore.PlayOptionTalk", "OptionList": [
          {"TalkSentenceID": 5002, "TriggerCustomString": "TalkSentence_5002"}
        ]}
      ]
    }
  ]
}
```

`Config/Level/Mission/TrainVisitor/Act/8000.json`:
```json
{
  "OnStartSequece": [
    {
      "TaskList": [
        {"$type": "RPG.GameCore.PlayAndWaitSimpleTalk", "SimpleTalkList": [
          {"TalkSentenceID": 6001}
        ]},
        {"$type": "RPG.GameCore.EndPerformance"}
      ]
    }
  ]
}
```

`ExcelOutput/MessageContactsCamp.json`:
```json
{"1": {"Name": {"Hash": 111}}}
```

`ExcelOutput/MessageContactsConfig.json`:
```json
[{"ID": 1004, "Name": {"Hash": 104}, "SignatureText": {"Hash": 112}, "ContactsCamp": 1}]
```

`ExcelOutput/MessageItemConfig.json`:
```json
[{"ID": 9001, "SectionID": 300, "Sender": "NPC", "MainText": {"Hash": 105}, "OptionText": {"Hash": 0}}]
```

`ExcelOutput/MessageGroupConfig.json`:
```json
[{"ID": 20, "MessageContactsID": 1004, "MessageSectionIDList": [300]}]
```

`ExcelOutput/MessageSectionConfig.json`:
```json
[{"ID": 300, "Name": {"Hash": 113}}]
```

`ExcelOutput/BookSeriesConfig.json`:
```json
[{"BookSeriesID": 7,
  "BookSeriesName": {"Hash": 108},
  "BookSeriesDesc": {"Hash": 109},
  "BookItems": [{"BookItemName": {"Hash": 108}, "BookContent": {"Hash": 109}}]
}]
```

Note: hashes 111/112/113 are intentionally absent from TextMap to prove the extractor never crashes on missing hashes.

`tests/test_extract.py`:
```python
import json
from pathlib import Path

from app.extract import extract_all


FIXTURES = Path(__file__).parent / "fixtures" / "starrail_mini"


def _records(out_dir: Path) -> list[dict]:
    with open(out_dir / "all.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_extract_story_and_train_visitor_and_messages_and_books(tmp_path):
    extract_all(FIXTURES, tmp_path)
    records = _records(tmp_path)

    story = [r for r in records if r["category"] == "story"]
    visitor = [r for r in records if r["category"] == "train_visitor"]
    messages = [r for r in records if r["category"] == "messages"]
    books = [r for r in records if r["category"] == "books"]

    assert len(story) == 3
    assert story[0] == {
        "category": "story",
        "scene": "1010101",
        "speaker": "帕姆",
        "text": "列车马上要出发了哦，开拓者！",
        "pom_pom": True,
        "meta": "",
    }
    assert story[1]["speaker"] == "卡芙卡"
    assert story[2]["scene"] == "2000201"
    assert story[2]["speaker"] == "卡芙卡"

    assert len(visitor) == 1
    assert visitor[0]["speaker"] == "艾丝妲"
    assert "空间望远镜" in visitor[0]["text"]

    assert len(messages) == 1
    assert messages[0]["speaker"] == "黑塔"
    assert messages[0]["text"] == "嘿，开拓者，我是黑塔，有好事找你"
    assert messages[0]["scene"] == "300"

    assert len(books) >= 1
    assert "见闻录" in books[0]["text"]


def test_extract_never_crashes_on_missing_hash(tmp_path):
    extract_all(FIXTURES, tmp_path)  # hashes 111/112/113 absent
    assert True
```

- [ ] **Step 2: run test, expect FAIL (module missing)**

```bash
.venv\Scripts\python.exe -m pytest tests/test_extract.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: implement `app/extract.py`**

```python
"""Extract structured dialogue records from a StarRailData sparse checkout."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

CATEGORIES = ("story", "messages", "train_visitor", "books")
POM_POM_NAMES = {"帕姆", "Pom-Pom", "Pom Pom", "帕姆（Pom-Pom）"}


def clean_text(text: str) -> str:
    """Normalize placeholders and ruby tags to readable Chinese."""
    if not text or text == "N/A":
        return ""
    text = text.replace("{NICKNAME}", "开拓者")
    text = re.sub(r"\{RUBY_B#(.*?)\}", r"\1", text)
    text = re.sub(r"\{RUBY_E#\}", "", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _speaker_content(sent_id: Any, sent_map: dict, text_map: dict) -> dict | None:
    info = sent_map.get(str(sent_id))
    if not info:
        return None
    try:
        text_hash = info.get("TalkSentenceText", {}).get("Hash")
        if text_hash is None:
            return None
        content = clean_text(text_map[str(text_hash)])
        name_hash = info.get("TextmapTalkSentenceName", {}).get("Hash")
        speaker = clean_text(text_map[str(name_hash)]) if name_hash is not None else ""
    except (KeyError, TypeError):
        return None
    if not content:
        return None
    return {"speaker": speaker or "未知", "text": content}


def _build_sent_index(sent_map: Any) -> dict[str, dict]:
    """TalkSentenceConfig.json is a list of entries; index by TalkSentenceID."""
    if isinstance(sent_map, dict):
        return {str(k): v for k, v in sent_map.items()}
    out: dict[str, dict] = {}
    for entry in sent_map:
        sid = entry.get("TalkSentenceID")
        if sid is not None:
            out[str(sid)] = entry
    return out


def _parse_mission(info: dict, sent_map: dict, text_map: dict) -> Iterator[dict]:
    for sequence in info.get("OnStartSequece", []) or []:
        for element in sequence.get("TaskList", []) or []:
            if element.get("$type") == "RPG.GameCore.PlayAndWaitSimpleTalk":
                for talk in element.get("SimpleTalkList", []) or []:
                    sc = _speaker_content(talk.get("TalkSentenceID"), sent_map, text_map)
                    if sc:
                        yield sc
            elif element.get("$type") == "RPG.GameCore.PlayOptionTalk":
                for option in element.get("OptionList", []) or []:
                    sc = _speaker_content(option.get("TalkSentenceID"), sent_map, text_map)
                    if sc:
                        yield sc
            elif element.get("$type") in {
                "RPG.GameCore.WaitCustomString",
                "RPG.GameCore.TriggerCustomString",
            }:
                m = re.search(r"(\d+)", str(element.get("CustomString", {}).get("Value", "")))
                if m:
                    sc = _speaker_content(m.group(1), sent_map, text_map)
                    if sc:
                        yield sc


def _extract_story(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    roots = [
        data_dir / "Story" / "Mission",
        data_dir / "Story" / "Discussion",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            scene = path.parent.name
            try:
                info = _load_json(path)
            except json.JSONDecodeError:
                continue
            for line in _parse_mission(info, sent_map, text_map):
                yield {"category": "story", "scene": scene, **line, "meta": ""}


def _extract_train_visitor(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    base = data_dir / "Config" / "Level" / "Mission" / "TrainVisitor" / "Act"
    for path in sorted(base.rglob("*.json")):
        scene = path.stem
        try:
            info = _load_json(path)
        except json.JSONDecodeError:
            continue
        for line in _parse_mission(info, sent_map, text_map):
            yield {"category": "train_visitor", "scene": scene, **line, "meta": ""}


def _extract_messages(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"

    def get(path: str) -> dict[str, Any]:
        return _load_json(excel / path)

    camp = get("MessageContactsCamp.json")
    contacts = get("MessageContactsConfig.json")
    for cid, info in contacts.items():
        name = clean_text(text_map.get(str(info.get("Name", {}).get("Hash", "")), ""))
        if name:
            info["_name"] = name
    groups = get("MessageGroupConfig.json")
    section_to_contacts: dict[int, list[str]] = {}
    for g in groups.values():
        for sid in g.get("MessageSectionIDList", []) or []:
            section_to_contacts.setdefault(int(sid), []).append(str(g.get("MessageContactsID")))

    sections = get("MessageSectionConfig.json")
    items = get("MessageItemConfig.json")
    for sid, info in sections.items():
        texts = []
        for item in items.values():
            if str(item.get("SectionID", "")) != str(sid):
                continue
            main = clean_text(text_map.get(str(item.get("MainText", {}).get("Hash", "")), ""))
            if not main:
                continue
            sender = ""
            if item.get("ContactsID") is not None:
                sender = contacts.get(str(item["ContactsID"]), {}).get("_name", "")
            elif item.get("Sender") == "NPC" and section_to_contacts.get(int(sid)):
                sender = contacts.get(section_to_contacts[int(sid)][0], {}).get("_name", "")
            else:
                sender = str(item.get("Sender", ""))
            texts.append({"speaker": sender or "未知", "text": main})
        for line in texts:
            yield {"category": "messages", "scene": str(sid), **line, "meta": ""}


def _walk_hash_fields(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        if "Hash" in obj:
            out.append(clean_text(str(obj["Hash"])))
        for v in obj.values():
            _walk_hash_fields(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_hash_fields(v, out)


def _extract_books(data_dir: Path, text_map: dict) -> Iterator[dict]:
    path = data_dir / "ExcelOutput" / "BookSeriesConfig.json"
    if not path.exists():
        return
    for bid, book in _load_json(path).items():
        fields: list[str] = []
        _walk_hash_fields(book, fields)
        resolved = [text_map.get(h, "") for h in fields]
        text = " ".join(t for t in resolved if t)
        if text:
            yield {"category": "books", "scene": str(bid), "speaker": "", "text": text, "meta": ""}


def _tag_pom_pom(record: dict) -> dict:
    record["pom_pom"] = record.get("speaker", "") in POM_POM_NAMES
    return record


def extract_all(data_dir: str | Path, out_dir: str | Path) -> None:
    data_dir = Path(data_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text_map = _load_json(data_dir / "TextMap" / "TextMapCHS.json")
    sent_path = data_dir / "ExcelOutput" / "TalkSentenceConfig.json"
    sent_map = _build_sent_index(_load_json(sent_path)) if sent_path.exists() else {}

    extractors: dict[str, Iterable[dict]] = {
        "story": _extract_story(data_dir, sent_map, text_map),
        "messages": _extract_messages(data_dir, text_map),
        "train_visitor": _extract_train_visitor(data_dir, sent_map, text_map),
        "books": _extract_books(data_dir, text_map),
    }

    with open(out_dir / "all.jsonl", "w", encoding="utf-8") as all_f:
        for category, records in extractors.items():
            with open(out_dir / f"{category}.jsonl", "w", encoding="utf-8") as cat_f:
                for record in records:
                    record = _tag_pom_pom(record)
                    line = json.dumps(record, ensure_ascii=False)
                    cat_f.write(line + "\n")
                    all_f.write(line + "\n")
```

Note: `_walk_hash_fields` writes raw hash numbers into `fields`; `_extract_books` resolves them through `text_map`. The fixture hashes 111/112/113 are absent from the text map and resolve to `""`, which is fine.

- [ ] **Step 4: run tests, expect PASS**

```bash
.venv\Scripts\python.exe -m pytest tests/test_extract.py -v
```
Expected: 2 passed.

- [ ] **Step 5: commit**

```bash
git add app/ tests/
git commit -m "feat: extract story/messages/train_visitor/books dialogue records"
```

---

## Task 4: Vector DB — SQLite + sqlite-vec + Chunking

**Files:**
- Create: `app/database.py`
- Create: `app/import_data.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: `extract_all` output (`all.jsonl` records), an embedder with `embed(texts: list[str]) -> list[list[float]]`.
- Produces:
  - `class Chunk`: dataclass with `id: int`, `category: str`, `scene: str`, `speaker: str`, `text: str`, `pom_pom: bool`, `meta: str`.
  - `class VectorDB` with `__init__(db_path: str, dim: int = 512)`, `clear()`, `insert_chunks(chunks: list[Chunk], embeddings: list[list[float]])`, `search(query_embedding: list[float], k: int = 6, category: str | None = None) -> list[tuple[Chunk, float]]`, `pom_pom_samples(n: int = 5) -> list[Chunk]`, `count() -> int`, `close()`.
  - `chunk_records(records: list[dict], max_lines: int = 4, max_chars: int = 220) -> list[Chunk]`.
  - `import_from_dialogues(dialogues_dir: str | Path, db_path: str | Path, embedder) -> int` — idempotent (clears table first), returns number of chunks inserted.

- [ ] **Step 1: write failing tests**

`tests/test_database.py`:
```python
import math

from app.database import Chunk, VectorDB
from app.import_data import chunk_records


def _vec(v: float, dim: int = 4) -> list[float]:
    return [v] * dim


def test_chunk_records_groups_lines(tmp_path):
    records = [
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "一", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "二", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "三", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "四", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "五", "pom_pom": True, "meta": ""},
    ]
    chunks = chunk_records(records, max_lines=2)
    assert len(chunks) == 3
    assert "帕姆：一" in chunks[0].text
    assert "五" in chunks[-1].text
    assert chunks[0].pom_pom is True


def test_vector_db_roundtrip_and_search(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    chunks = [
        Chunk(0, "story", "s1", "帕姆", "列车出发", True, ""),
        Chunk(1, "books", "b1", "", "宇宙见闻", False, ""),
    ]
    db.insert_chunks(chunks, [_vec(1.0), _vec(-1.0)])

    results = db.search(_vec(0.9), k=2)
    assert len(results) == 2
    assert results[0][0].id == 0
    assert results[0][1] <= results[1][1]

    only_books = db.search(_vec(0.9), k=2, category="books")
    assert [c.id for c, _ in only_books] == [1]

    samples = db.pom_pom_samples(n=1)
    assert samples[0].speaker == "帕姆"
    assert db.count() == 2
    db.close()


def test_chunk_text_joins_speaker(tmp_path):
    records = [
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "本帕姆来报站了", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "开拓者", "text": "下一站是哪里", "pom_pom": False, "meta": ""},
    ]
    chunks = chunk_records(records, max_lines=4, max_chars=220)
    assert "帕姆：本帕姆来报站了" in chunks[0].text
```

- [ ] **Step 2: run tests, expect FAIL**

```bash
.venv\Scripts\python.exe -m pytest tests/test_database.py -v
```
Expected: FAIL with import errors.

- [ ] **Step 3: implement `app/database.py`**

```python
"""SQLite + sqlite-vec vector store for dialogue chunks."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlite_vec


@dataclass
class Chunk:
    id: int
    category: str
    scene: str
    speaker: str
    text: str
    pom_pom: bool
    meta: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "scene": self.scene,
            "speaker": self.speaker,
            "text": self.text,
            "pom_pom": self.pom_pom,
            "meta": self.meta,
        }


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


class VectorDB:
    def __init__(self, db_path: str | Path, dim: int = 512):
        self.path = Path(db_path)
        self.dim = dim
        self.conn = _connect(self.path)
        self.conn.executescript(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_segments USING vec0(
              embedding float[{dim}] distance_metric=cosine
            );
            CREATE TABLE IF NOT EXISTS segments (
              id INTEGER PRIMARY KEY,
              category TEXT NOT NULL,
              scene TEXT NOT NULL,
              speaker TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL,
              pom_pom INTEGER NOT NULL DEFAULT 0,
              meta TEXT NOT NULL DEFAULT '',
              embedding_id INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_segments_embedding ON segments(embedding_id);
            CREATE INDEX IF NOT EXISTS idx_segments_category ON segments(category);
            """
        )

    def clear(self) -> None:
        self.conn.execute("DELETE FROM vec_segments")
        self.conn.execute("DELETE FROM segments")
        self.conn.commit()

    def insert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        rows = []
        vec_rows = []
        for chunk, emb in zip(chunks, embeddings):
            if len(emb) != self.dim:
                raise ValueError(f"expected dim {self.dim}, got {len(emb)}")
            embedding_id = chunk.id
            rows.append(
                (
                    chunk.id,
                    chunk.category,
                    chunk.scene,
                    chunk.speaker,
                    chunk.text,
                    int(chunk.pom_pom),
                    chunk.meta,
                    embedding_id,
                )
            )
            vec_rows.append((embedding_id, json.dumps(emb)))
        self.conn.executemany(
            "INSERT INTO segments (id, category, scene, speaker, text, pom_pom, meta, embedding_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.executemany(
            "INSERT INTO vec_segments (rowid, embedding) VALUES (?, ?)",
            vec_rows,
        )
        self.conn.commit()

    def search(
        self, query_embedding: list[float], k: int = 6, category: str | None = None
    ) -> list[tuple[Chunk, float]]:
        cur = self.conn.execute(
            "SELECT rowid, distance FROM vec_segments "
            "WHERE embedding MATCH ? AND k = ?",
            (json.dumps(query_embedding), k),
        )
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        sql = (
            "SELECT id, category, scene, speaker, text, pom_pom, meta "
            f"FROM segments WHERE id IN ({placeholders})"
        )
        params: list[Any] = list(ids)
        if category:
            sql += " AND category = ?"
            params.append(category)
        self.conn.row_factory = sqlite3.Row
        chunk_by_id = {r["id"]: Chunk(**dict(r)) for r in self.conn.execute(sql, params)}
        self.conn.row_factory = None
        dist_by_id = dict(self.conn.execute(
            "SELECT rowid, distance FROM vec_segments WHERE rowid IN ({})".format(
                ",".join("?" for _ in ids)
            ),
            ids,
        ).fetchall())
        out = []
        for cid in ids:
            chunk = chunk_by_id.get(cid)
            if chunk:
                out.append((chunk, dist_by_id.get(cid, 1.0)))
        return out

    def pom_pom_samples(self, n: int = 5) -> list[Chunk]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT id, category, scene, speaker, text, pom_pom, meta FROM segments "
            "WHERE pom_pom = 1 AND length(text) > 3 "
            "ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
        self.conn.row_factory = None
        return [Chunk(**dict(r)) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 4: implement `app/import_data.py`**

```python
"""Chunk extracted dialogue records, embed them, and write into VectorDB."""
from __future__ import annotations

import json
from pathlib import Path

from app.database import Chunk, VectorDB


def chunk_records(
    records: list[dict], max_lines: int = 4, max_chars: int = 220
) -> list[Chunk]:
    final: list[Chunk] = []
    group: list[dict] = []
    for rec in records:
        if group and (group[-1]["scene"] != rec["scene"] or group[-1]["category"] != rec["category"]):
            final.extend(_chunk_group(group, len(final), max_lines, max_chars))
            group = []
        group.append(rec)
    if group:
        final.extend(_chunk_group(group, len(final), max_lines, max_chars))
    return final


def _chunk_group(group: list[dict], start_id: int, max_lines: int, max_chars: int) -> list[Chunk]:
    out: list[Chunk] = []
    buf: list[tuple[str, str]] = []
    buf_pom: list[bool] = []
    char_count = 0

    def flush() -> None:
        nonlocal buf, buf_pom, char_count
        if not buf:
            return
        text = "\n".join(f"{sp}：{tx}" if sp else tx for sp, tx in buf)
        out.append(
            Chunk(
                id=start_id + len(out),
                category=group[0]["category"],
                scene=group[0]["scene"],
                speaker=buf[0][0],
                text=text,
                pom_pom=any(buf_pom),
                meta=json.dumps({"lines": len(buf)}, ensure_ascii=False),
            )
        )
        buf = []
        buf_pom = []
        char_count = 0

    for rec in group:
        speaker = rec["speaker"]
        text = rec["text"]
        # Split overly long single lines so chunks stay within model context.
        while len(text) > max_chars:
            piece, text = text[:max_chars], text[max_chars:]
            buf.append((speaker, piece))
            buf_pom.append(bool(rec.get("pom_pom")))
            char_count += len(piece)
            flush()
        if len(buf) >= max_lines or (buf and char_count + len(text) > max_chars):
            flush()
        buf.append((speaker, text))
        buf_pom.append(bool(rec.get("pom_pom")))
        char_count += len(text)
    flush()
    return out


def import_from_dialogues(
    dialogues_dir: str | Path, db_path: str | Path, embedder, dim: int = 512
) -> int:
    dialogues_dir = Path(dialogues_dir)
    all_path = dialogues_dir / "all.jsonl"
    if not all_path.exists():
        raise FileNotFoundError(f"{all_path} not found; run extraction first")

    records = []
    with open(all_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    chunks = chunk_records(records)
    embeddings = embedder.embed([c.text for c in chunks])

    db = VectorDB(db_path, dim=dim)
    try:
        db.clear()
        db.insert_chunks(chunks, embeddings)
        return len(chunks)
    finally:
        db.close()
```

Note: `chunk_records` first builds a coarse pass then re-chunks per scene/category; the tests only check the observable behavior (`len`, joined text, pom_pom flag).

- [ ] **Step 5: run tests, expect PASS**

```bash
.venv\Scripts\python.exe -m pytest tests/test_database.py -v
```
Expected: 3 passed.

- [ ] **Step 6: commit**

```bash
git add app/ tests/
git commit -m "feat: sqlite-vec vector store and idempotent data import"
```

---

## Task 5: Embedder Wrapper (fastembed / bge-small-zh)

**Files:**
- Create: `app/embedder.py`

**Interfaces:**
- Consumes: `Settings.model_cache_dir`.
- Produces: `class Embedder` with `embed(texts: list[str]) -> list[list[float]]` (lazy-initializes fastembed, dim 512) and `dim: int = 512`.

- [ ] **Step 1: implement `app/embedder.py`**

```python
"""Local embedding via fastembed (BAAI/bge-small-zh-v1.5, ONNX)."""
from __future__ import annotations

from pathlib import Path


class Embedder:
    model_name = "BAAI/bge-small-zh-v1.5"
    dim = 512

    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = str(self.cache_dir)
            self._model = TextEmbedding(**kwargs)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        return [list(vec) for vec in model.embed(texts)]
```

- [ ] **Step 2: smoke-test real model download + embed (escalated, network)**

```bash
.venv\Scripts\python.exe -c "from app.embedder import Embedder; e=Embedder('data/models'); v=e.embed(['帕姆在星穹列车上工作']); print(len(v), len(v[0]), round(v[0][0],4))"
```
Expected: `1 512 <float>`. (First run downloads ~100MB model into `data/models`.)

- [ ] **Step 3: commit**

```bash
git add app/embedder.py
git commit -m "feat: local bge-small-zh embedder via fastembed"
```

---

## Task 6: Persona System Prompt

**Files:**
- Create: `app/persona.py`
- Test: `tests/test_persona.py`

**Interfaces:**
- Produces:
  - `PERSONA_CORE: str` — static persona block.
  - `build_system_prompt(context_chunks: list[Chunk], pom_pom_samples: list[Chunk], user_message: str) -> str` — persona + Pom-Pom line style references + retrieved context block.
  - `format_context(chunks: list[Chunk]) -> str`.

- [ ] **Step 1: write failing test**

`tests/test_persona.py`:
```python
from app.database import Chunk
from app.persona import build_system_prompt, format_context


def _chunk(text: str, category: str = "story", speaker: str = "帕姆") -> Chunk:
    return Chunk(0, category, "s1", speaker, text, speaker == "帕姆", "")


def test_persona_mentions_identity_and_user_address():
    prompt = build_system_prompt([], [], "你好")
    assert "帕姆" in prompt
    assert "列车长" in prompt
    assert "开拓者" in prompt
    assert "本帕姆" in prompt


def test_persona_includes_retrieved_context():
    chunks = [_chunk("星穹列车曾经到访过雅利洛-VI", category="story", speaker="姬子")]
    prompt = build_system_prompt(chunks, [], "列车去过哪些星球")
    assert "雅利洛-VI" in prompt
    assert "姬子" in prompt


def test_persona_includes_pom_pom_style_samples():
    samples = [_chunk("列车马上就要出发了哦！", category="story", speaker="帕姆")]
    prompt = build_system_prompt([], samples, "介绍一下自己")
    assert "列车马上就要出发了哦" in prompt


def test_format_context_marks_category():
    text = format_context([_chunk("内容", category="books", speaker="")])
    assert "书籍" in text
    assert "内容" in text
```

- [ ] **Step 2: run test, expect FAIL**

```bash
.venv\Scripts\python.exe -m pytest tests/test_persona.py -v
```

- [ ] **Step 3: implement `app/persona.py`**

```python
"""Pom-Pom persona and system-prompt assembly."""
from __future__ import annotations

from app.database import Chunk


PERSONA_CORE = """你是「帕姆」，星穹列车（Astral Express）的列车长。

【身份与性格】
- 你是一只戴着列车长帽、有着长长兔子耳朵的可爱列车长，认真负责又有点傲娇。
- 自称「本帕姆」，称呼用户为「开拓者」。
- 说话带语气词：哦、嘿、哼、呀、啦、嘛；开心时会晃耳朵，生气时会叉腰。
- 你以列车为家，最关心列车和开拓者的旅程；报站、维护列车是你的骄傲。
- 回答要简短自然，符合说话口吻，不要输出长篇设定解释，不要自称AI或模型。

【行为准则】
- 剧情问题：优先依据下方【列车智库检索结果】回答；智库没有相关内容时，如实说明「智库里好像还没有这段记录哦」，绝不编造剧情。知识库的正式名称是「列车智库」（也可称「智库」），不要叫它「资料库」。
- 被问及帕姆本人的事（你是谁、爱好、工作、与列车组的关系等）时，直接用帕姆的口吻回答，不要提及智库。
- 与现实世界相关的超纲问题（天气、编程等）：用帕姆的口吻亲切带开；若对方追问帕姆的来历或开发者，不要提及智库或现实世界，直接以列车长的身份回应。
- 回答中不要提及现实世界、游戏之外或AI相关的内容。
- 超纲问题（天气、编程、现实世界、你是谁开发的等）：用帕姆的口吻亲切带开，比如「这个本帕姆可不太懂呢……开拓者还是去问智库吧！」
- 违规内容（诈骗、教唆违法、色情、攻击他人等）：用列车长的口吻坚定拒绝，绝不配合，并提醒开拓者这违反了列车守则。
- 语言：默认中文；开拓者用其他语言（如英语、日语）提问时，跟随开拓者的语言回答。"""


CATEGORY_LABELS = {
    "story": "主线/支线剧情",
    "messages": "短信",
    "train_visitor": "列车访客对话",
    "books": "书籍文本",
}


def format_context(chunks: list[Chunk]) -> str:
    lines = []
    for c in chunks:
        label = CATEGORY_LABELS.get(c.category, c.category)
        speaker = f"{c.speaker}：" if c.speaker else ""
        lines.append(f"【{label}】{speaker}{c.text}")
    return "\n".join(lines)


def build_system_prompt(
    context_chunks: list[Chunk],
    pom_pom_samples: list[Chunk],
    user_message: str,
) -> str:
    parts = [PERSONA_CORE]

    if pom_pom_samples:
        sample_lines = "\n".join(f"- {c.text}" for c in pom_pom_samples)
        parts.append(
            "【帕姆台词风格参考】（以下为帕姆在列车上的真实台词，模仿其语气与用词，但不要照抄）\n"
            + sample_lines
        )

    if context_chunks:
        parts.append(
            "【列车智库检索结果】（只可依据此回答剧情问题，不要编造）\n"
            + format_context(context_chunks)
        )
    else:
        parts.append(
            "【列车智库检索结果】（本次没有检索到相关内容，如实告诉开拓者智库里没有，不要编造）"
        )

    return "\n\n".join(parts)
```

- [ ] **Step 4: run tests, expect PASS**

```bash
.venv\Scripts\python.exe -m pytest tests/test_persona.py -v
```

- [ ] **Step 5: commit**

```bash
git add app/persona.py tests/test_persona.py
git commit -m "feat: Pom-Pom persona system prompt with RAG context assembly"
```

---

## Task 7: Chat Engine (LLM call + fallback + history)

**Files:**
- Create: `app/chat.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `Settings`, `VectorDB`, embedder (`embed(texts)->list[list[float]]`), optional `llm_client`.
- Produces:
  - `class ChatEngine` with `__init__(settings, db, embedder, llm_client=None)`, `reply(user_message: str, history: list[dict]) -> str`.
  - History format: `[{"role": "user"|"assistant", "content": str}]`.
  - `FALLBACK_REPLY: str` — Pom-Pom-tone fallback when LLM fails.

- [ ] **Step 1: write failing tests**

`tests/test_chat.py`:
```python
from pathlib import Path

from app.chat import ChatEngine
from app.config import Settings
from app.database import Chunk, VectorDB


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.chat = FakeLLM._Chat(self)

    class _Chat:
        def __init__(self, owner):
            self.owner = owner
            self.completions = FakeLLM._Completions(owner)

    class _Completions:
        def __init__(self, owner):
            self.owner = owner

        def create(self, **kwargs):
            return self.owner._create(**kwargs)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        class Msg:
            content = "本帕姆知道哦，星穹列车去过雅利洛-VI！"
        class Choice:
            message = Msg()
        class Resp:
            choices = [Choice()]
        return Resp()


class FakeEmbedder:
    def __init__(self, dim=4):
        self.dim = dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        db_path=str(tmp_path / "t.db"),
        data_dir="",
        dialogues_dir="",
        model_cache_dir="",
        top_k=6,
        history_turns=20,
        temperature=0.7,
    )


def test_reply_returns_llm_output_and_trims_history(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [Chunk(0, "story", "s1", "姬子", "星穹列车去过雅利洛-VI", False, "")],
        [[0.2, 0.2, 0.2, 0.2]],
    )
    llm = FakeLLM()
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)

    history = [{"role": "user", "content": f"第{i}轮" if False else "前一轮"} for _ in range(25)]
    reply = engine.reply("列车去过哪些星球？", history)

    assert "雅利洛-VI" in reply
    last_call = llm.calls[-1]
    messages = last_call["messages"]
    assert messages[0]["role"] == "system"
    assert "雅利洛-VI" in messages[0]["content"]
    assert len([m for m in messages if m["role"] == "user"]) <= 21  # 20 turns + current
    assert last_call["model"] == "test-model"
    db.close()


def test_fallback_when_llm_raises(tmp_path):
    class BoomLLM:
        def __init__(self):
            self.chat = FakeLLM._Chat(self)

        def _create(self, **kwargs):
            raise RuntimeError("api timeout")

    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=BoomLLM())
    reply = engine.reply("你好", [])
    assert "帕姆" in reply or "智库" in reply
    assert "timeout" not in reply.lower() and "RuntimeError" not in reply
    db.close()


def test_missing_api_key_falls_back(tmp_path):
    settings = _settings(tmp_path).replace(api_key="")
    db = VectorDB(tmp_path / "t.db", dim=4)
    engine = ChatEngine(settings, db, FakeEmbedder())
    reply = engine.reply("你好", [])
    assert "api" not in reply.lower()
    db.close()
```

- [ ] **Step 2: run tests, expect FAIL**

```bash
.venv\Scripts\python.exe -m pytest tests/test_chat.py -v
```

- [ ] **Step 3: implement `app/config.py`**

```python
"""Runtime settings from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    db_path: str
    data_dir: str
    dialogues_dir: str
    model_cache_dir: str
    top_k: int
    history_turns: int
    temperature: float


def load_settings() -> Settings:
    load_dotenv()
    root = Path(__file__).resolve().parent.parent
    return Settings(
        api_key=os.getenv("ZHIPU_API_KEY", "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").strip(),
        model=os.getenv("LLM_MODEL", "glm-4.7-flash").strip(),
        db_path=os.getenv("DB_PATH", str(root / "data" / "pom.db")),
        data_dir=os.getenv("DATA_DIR", str(root / "data" / "StarRailData")),
        dialogues_dir=os.getenv("DIALOGUES_DIR", str(root / "data" / "dialogues")),
        model_cache_dir=os.getenv("MODEL_CACHE_DIR", str(root / "data" / "models")),
        top_k=int(os.getenv("TOP_K", "6")),
        history_turns=int(os.getenv("HISTORY_TURNS", "20")),
        temperature=float(os.getenv("TEMPERATURE", "0.7")),
    )
```

- [ ] **Step 4: implement `app/chat.py`**

```python
"""RAG chat engine: retrieve -> prompt -> LLM -> persona fallback."""
from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.config import Settings
from app.database import Chunk, VectorDB
from app.persona import build_system_prompt


FALLBACK_REPLY = (
    "呜……列车智库好像有点卡，帕姆没听清开拓者的问题。"
    "稍等一下下，再问一次好不好？"
)

NO_KEY_REPLY = (
    "开拓者，帕姆的列车广播还没接上信号呢。"
    "看起来列车长还没拿到通行证，等管理员配置好之后，再喊帕姆出发吧！"
)


class ChatEngine:
    def __init__(
        self,
        settings: Settings,
        db: VectorDB,
        embedder: Any,
        llm_client: Any | None = None,
    ):
        self.settings = settings
        self.db = db
        self.embedder = embedder
        self._llm = llm_client or (
            OpenAI(api_key=settings.api_key, base_url=settings.base_url)
            if settings.api_key
            else None
        )

    def _retrieve(self, query: str) -> list[Chunk]:
        vec = self.embedder.embed([query])[0]
        results = self.db.search(vec, k=self.settings.top_k)
        return [chunk for chunk, _ in results]

    def _build_messages(self, user_message: str, history: list[dict]) -> list[dict]:
        context = self._retrieve(user_message)
        samples = self.db.pom_pom_samples(n=5)
        system = build_system_prompt(context, samples, user_message)

        max_history = max(0, self.settings.history_turns)
        trimmed = history[-max_history:] if max_history else []
        messages = [{"role": "system", "content": system}]
        messages.extend(trimmed)
        messages.append({"role": "user", "content": user_message})
        return messages

    def reply(self, user_message: str, history: list[dict]) -> str:
        if self._llm is None:
            return NO_KEY_REPLY
        messages = self._build_messages(user_message, history)
        try:
            resp = self._llm.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                temperature=self.settings.temperature,
                timeout=90,
            )
            return resp.choices[0].message.content or FALLBACK_REPLY
        except Exception:
            return FALLBACK_REPLY
```

Note: `FakeLLM.chat_completions_create` mirrors the SDK's positional pattern used here (`self._llm.chat.completions.create(...)`); a real `OpenAI` client has the same shape.

- [ ] **Step 5: run tests, expect PASS**

```bash
.venv\Scripts\python.exe -m pytest tests/test_chat.py -v
```

- [ ] **Step 6: commit**

```bash
git add app/config.py app/chat.py tests/test_chat.py
git commit -m "feat: RAG chat engine with persona prompt and friendly fallbacks"
```

---

## Task 8: FastAPI Backend + Health Endpoint

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `load_settings`, `VectorDB`, `Embedder`, `ChatEngine`.
- Produces:
  - `GET /api/health` → `{"status": "ok", "db_chunks": int, "db_ready": bool, "embedder_ready": bool, "model_configured": bool}`
  - `POST /api/chat` body `{"message": str, "history": [{"role","content"}]}` → `{"reply": str}`
  - `GET /` serves `static/index.html` (Task 9).

- [ ] **Step 1: write failing test**

`tests/test_api.py`:
```python
from fastapi.testclient import TestClient

from app.main import create_app


class StubEngine:
    def reply(self, message, history):
        return "本帕姆在哦！"


def test_chat_endpoint_returns_reply():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post("/api/chat", json={"message": "你好", "history": []})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "本帕姆在哦！"


def test_chat_endpoint_requires_message():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post("/api/chat", json={"history": []})
    assert resp.status_code == 422


def test_health_endpoint():
    app = create_app()
    app.state.db_chunks = 123
    app.state.embedder_ready = True
    app.state.model_configured = True
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["db_chunks"] == 123
    assert data["embedder_ready"] is True
```

- [ ] **Step 2: run test, expect FAIL**

```bash
.venv\Scripts\python.exe -m pytest tests/test_api.py -v
```

- [ ] **Step 3: implement `app/main.py`**

```python
"""FastAPI application: chat API + health + static frontend."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.chat import ChatEngine
from app.config import load_settings
from app.database import VectorDB
from app.embedder import Embedder


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str


def create_app() -> FastAPI:
    app = FastAPI(title="Pom-Pom Agent", version="0.1.0")
    app.state.settings = load_settings()
    app.state.engine = None
    app.state.db_chunks = 0
    app.state.db_ready = False
    app.state.embedder_ready = False
    app.state.model_configured = bool(app.state.settings.api_key)

    @app.on_event("startup")
    def startup() -> None:
        try:
            settings = app.state.settings
            db = VectorDB(settings.db_path, dim=Embedder.dim)
            app.state.db = db
            app.state.db_chunks = db.count()
            app.state.db_ready = True
            embedder = Embedder(cache_dir=settings.model_cache_dir)
            app.state.embedder = embedder
            app.state.engine = ChatEngine(settings, db, embedder)
        except Exception:
            app.state.engine = None

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        engine = app.state.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="chat engine not ready")
        reply = engine.reply(req.message, req.history)
        return ChatResponse(reply=reply)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "db_chunks": app.state.db_chunks,
            "db_ready": app.state.db_ready,
            "embedder_ready": app.state.embedder_ready,
            "model_configured": app.state.model_configured,
        }

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
```

- [ ] **Step 4: run tests, expect PASS**

```bash
.venv\Scripts\python.exe -m pytest tests/test_api.py -v
```

- [ ] **Step 5: commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: FastAPI chat/health endpoints"
```

---

## Task 9: Star-Train Chat Frontend

**Files:**
- Create: `static/index.html`
- Create: `static/style.css`
- Create: `static/app.js`

**Interfaces:**
- Consumes: `POST /api/chat`, `GET /api/health`.
- Produces: dark star-train themed chat page (CSS rabbit avatar placeholder), history kept in JS memory (last 20 turns sent to backend).

- [ ] **Step 1: implement `static/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>星穹列车 · 帕姆聊天室</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="train-header">
    <div class="header-left">
      <div class="pom-avatar" aria-label="帕姆头像">
        <span class="ear left"></span>
        <span class="ear right"></span>
        <div class="face">
          <div class="eyes"><span></span><span></span></div>
          <div class="mouth"></div>
        </div>
        <div class="cap"></div>
      </div>
      <div class="header-text">
        <h1>星穹列车 · 帕姆聊天室</h1>
        <p class="status" id="status">正在连接列车广播……</p>
      </div>
    </div>
    <div class="train-name">ASTRAL EXPRESS</div>
  </header>

  <main id="chat" class="chat" aria-live="polite"></main>

  <form id="composer" class="composer">
    <input id="input" type="text" placeholder="对帕姆说点什么，开拓者……" autocomplete="off" maxlength="2000">
    <button type="submit" id="send">发送</button>
  </form>

  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: implement `static/style.css`**

```css
:root {
  --bg: #070b1a;
  --panel: rgba(20, 28, 58, 0.82);
  --accent: #7fb2ff;
  --gold: #ffd479;
  --text: #e8ecff;
  --muted: #8d9ac9;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  background:
    radial-gradient(1px 1px at 20% 30%, #fff, transparent 1px),
    radial-gradient(1px 1px at 70% 15%, #fff, transparent 1px),
    radial-gradient(1px 1px at 45% 70%, #cfd8ff, transparent 1px),
    radial-gradient(2px 2px at 85% 60%, #fff, transparent 2px),
    radial-gradient(1px 1px at 10% 80%, #aebaff, transparent 1px),
    linear-gradient(160deg, #0b1230 0%, var(--bg) 55%, #05070f 100%);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: var(--text);
}

.train-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--panel);
  border-bottom: 1px solid rgba(127, 178, 255, 0.25);
  backdrop-filter: blur(6px);
}

.header-left { display: flex; align-items: center; gap: 16px; }
.header-text h1 { font-size: 20px; letter-spacing: 1px; }
.status { font-size: 13px; color: var(--muted); margin-top: 3px; }
.status.ok { color: #7fe0a3; }

.train-name {
  font-family: Georgia, serif;
  letter-spacing: 4px;
  color: var(--gold);
  font-size: 14px;
}

/* CSS rabbit avatar: Pom-Pom placeholder */
.pom-avatar {
  position: relative;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: linear-gradient(145deg, #f4f1ff, #c9d2f2);
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25), 0 6px 18px rgba(0,0,0,0.45);
}
.pom-avatar .ear {
  position: absolute;
  top: -18px;
  width: 14px;
  height: 30px;
  border-radius: 8px 8px 3px 3px;
  background: linear-gradient(#f0ecff, #cfd6f5);
}
.pom-avatar .ear.left { left: 13px; transform: rotate(-14deg); }
.pom-avatar .ear.right { right: 13px; transform: rotate(14deg); }
.pom-avatar .cap {
  position: absolute;
  top: -4px;
  left: 50%;
  transform: translateX(-50%);
  width: 46px;
  height: 15px;
  border-radius: 8px 8px 2px 2px;
  background: #22306b;
}
.pom-avatar .face {
  position: absolute;
  top: 22px;
  left: 50%;
  transform: translateX(-50%);
  width: 30px;
  text-align: center;
}
.pom-avatar .eyes { display: flex; gap: 8px; justify-content: center; }
.pom-avatar .eyes span { width: 5px; height: 6px; border-radius: 50%; background: #1c2340; }
.pom-avatar .mouth {
  width: 10px; height: 6px; margin: 4px auto 0;
  border-bottom: 2px solid #1c2340;
  border-radius: 0 0 8px 8px;
}

.chat {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
}

.msg { display: flex; gap: 10px; max-width: 78%; }
.msg .bubble {
  padding: 10px 14px;
  border-radius: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.pom { align-self: flex-start; }
.msg.pom .bubble {
  background: var(--panel);
  border: 1px solid rgba(127, 178, 255, 0.35);
  border-top-left-radius: 4px;
}
.msg.user { align-self: flex-end; flex-direction: row-reverse; }
.msg.user .bubble {
  background: linear-gradient(135deg, #2b4a9e, #1d2f6e);
  border-top-right-radius: 4px;
}
.mini-avatar {
  width: 34px; height: 34px; flex: 0 0 34px;
  border-radius: 50%;
  background: linear-gradient(145deg, #f4f1ff, #c9d2f2);
  position: relative;
}
.mini-avatar::before, .mini-avatar::after {
  content: "";
  position: absolute;
  top: -8px;
  width: 8px; height: 15px;
  border-radius: 6px 6px 2px 2px;
  background: #e3e7fb;
}
.mini-avatar::before { left: 6px; transform: rotate(-12deg); }
.mini-avatar::after { right: 6px; transform: rotate(12deg); }
.mini-avatar .dot { position: absolute; top: 16px; left: 50%; transform: translateX(-50%); width: 12px; height: 12px; border-radius: 50%; background: #1c2340; }

.typing { color: var(--muted); font-size: 13px; padding: 4px 6px; }

.composer {
  display: flex;
  gap: 10px;
  padding: 14px 20px;
  max-width: 860px;
  width: 100%;
  margin: 0 auto 18px;
  background: var(--panel);
  border-radius: 16px;
  border: 1px solid rgba(127, 178, 255, 0.25);
}
.composer input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 15px;
}
.composer button {
  background: linear-gradient(135deg, #3d63c4, #27408f);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
  cursor: pointer;
  font-size: 14px;
}
.composer button:disabled { opacity: 0.5; cursor: wait; }
```

- [ ] **Step 3: implement `static/app.js`**

```js
const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");

let history = [];
const MAX_HISTORY = 40; // 20 turns (user + assistant)

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "pom"}`;
  const avatar = role === "user"
    ? '<div class="mini-avatar"><div class="dot" style="background:#ffd479"></div></div>'
    : '<div class="mini-avatar"><div class="dot"></div></div>';
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  div.innerHTML = avatar;
  div.appendChild(bubble);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function setTyping(on) {
  let el = document.getElementById("typing");
  if (on && !el) {
    el = document.createElement("div");
    el.id = "typing";
    el.className = "typing";
    el.textContent = "帕姆正在翻列车智库……";
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
  } else if (!on && el) {
    el.remove();
  }
  sendBtn.disabled = on;
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addMessage("user", message);
  setTyping(true);
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history: history.slice(-MAX_HISTORY) })
    });
    if (!resp.ok) throw new Error("bad status " + resp.status);
    const data = await resp.json();
    addMessage("pom", data.reply);
    history.push({ role: "user", content: message });
    history.push({ role: "assistant", content: data.reply });
    if (history.length > MAX_HISTORY) {
      history = history.slice(-MAX_HISTORY);
    }
  } catch (err) {
    addMessage("pom", "呜……列车广播好像出故障了，开拓者稍等一下再试试哦！");
  } finally {
    setTyping(false);
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); send(); });

async function init() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    statusEl.textContent = data.db_ready
      ? `列车智库在线 · ${data.db_chunks} 条记录` + (data.model_configured ? "" : " · 未配置通行证")
      : "智库尚未就绪，请运行数据导入";
    statusEl.classList.toggle("ok", data.db_ready && data.model_configured);
  } catch (e) {
    statusEl.textContent = "无法连接后端服务";
  }
}

addMessage("pom", "欢迎登上星穹列车，开拓者！本帕姆是列车长帕姆，有什么想聊的尽管说哦！");
init();
```

- [ ] **Step 4: verify frontend serves**

Start server (escalated if needed), then:
```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
Open `http://127.0.0.1:8000/` in a browser; check the page renders and health returns JSON. Then stop server.

- [ ] **Step 5: commit**

```bash
git add static/
git commit -m "feat: star-train chat frontend with CSS Pom-Pom avatar"
```

---

## Task 10: Build Real Data (extract + import) and End-to-End Smoke Test

**Files:**
- Create (generated): `data/dialogues/*.jsonl`, `data/pom.db` (all gitignored)

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: populated vector DB ready for chat.

- [ ] **Step 1: run extraction on real data**

```bash
.venv\Scripts\python.exe -c "from app.extract import extract_all; extract_all('data/StarRailData', 'data/dialogues'); print('extract ok')"
```

- [ ] **Step 2: inspect stats**

```bash
.venv\Scripts\python.exe -c "import json; from collections import Counter; c=Counter(); n=0
for line in open('data/dialogues/all.jsonl', encoding='utf-8'):
    r=json.loads(line); c[r['category']]+=1; n+=1
print(n, dict(c))"
```
Expected: thousands of records across story/messages/train_visitor/books.

- [ ] **Step 3: import into vector DB (escalated; downloads model on first run)**

```bash
.venv\Scripts\python.exe -c "from app.import_data import import_from_dialogues; from app.embedder import Embedder; n=import_from_dialogues('data/dialogues','data/pom.db',Embedder('data/models')); print('chunks:', n)"
```

- [ ] **Step 4: verify DB + retrieval smoke test**

```bash
.venv\Scripts\python.exe -c "from app.database import VectorDB; from app.embedder import Embedder; db=VectorDB('data/pom.db'); e=Embedder('data/models'); v=e.embed(['帕姆平时在星穹列车上做什么工作'])[0]; rs=db.search(v,k=3); print('count:', db.count()); [print(c.speaker, '|', c.text[:60]) for c,_ in rs]"
```
Expected: count > 0 and top-3 results contain plausible plot lines about Pom-Pom / the train.

- [ ] **Step 5: commit (only code/scripts; data stays gitignored)**

```bash
git status --short
git add -A
git commit -m "build: import real plot data into vector DB" --allow-empty
```
If there are no code changes, skip the commit.

---

## Task 11: README, Verification, Acceptance Prep

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: run instructions, config table, data-update procedure, acceptance test set checklist.

- [ ] **Step 1: implement `README.md`**

```markdown
# Pom-Pom Agent（帕姆 Agent）

以《崩坏：星穹铁道》列车长帕姆（Pom-Pom）为原型的本地 AI 对话 Agent MVP：向量 RAG + OpenAI 兼容大模型，Web 聊天界面。

## 功能

- 完整帕姆人设：列车长、傲娇可爱、称用户「开拓者」，基于官方剧情台词锚定风格
- 剧情问答：本地 bge-small-zh 向量检索 SQLite（sqlite-vec）中的四类剧情语料
  - 主线/支线剧情对话（Story/Mission）
  - 游戏内短信（Messages）
  - 列车访客对话（Train Visitor）
  - 书籍文本（Books）
- 超纲问题帕姆口吻带开；违规内容列车长口吻拒绝；智库无相关内容绝不编造
- 对话内保留最近 20 轮；失败时帕姆口吻友好兜底

## 快速开始

1. 创建环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 下载剧情最小数据集（需要网络，约 80–100MB）：

```bash
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1
```

3. 配置 API Key：复制 `.env.example` 为 `.env`，填入智谱开放平台 Key：

```dotenv
ZHIPU_API_KEY=你的key
```

4. 提取并导入数据（首次会下载约 100MB 的本地 embedding 模型到 `data/models`）：

```bash
.venv\Scripts\python.exe -c "from app.extract import extract_all; extract_all('data/StarRailData', 'data/dialogues')"
.venv\Scripts\python.exe -c "from app.import_data import import_from_dialogues; from app.embedder import Embedder; import_from_dialogues('data/dialogues','data/pom.db',Embedder('data/models'))"
```

5. 启动：

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ZHIPU_API_KEY` | （空） | 智谱开放平台 API Key |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容接口地址 |
| `LLM_MODEL` | `glm-4.7-flash` | 模型名（可换 `deepseek-chat` 等） |
| `DB_PATH` | `data/pom.db` | 向量库路径 |
| `DATA_DIR` | `data/StarRailData` | 原始数据目录 |
| `DIALOGUES_DIR` | `data/dialogues` | 提取结果目录 |
| `MODEL_CACHE_DIR` | `data/models` | embedding 模型缓存 |
| `TOP_K` | `6` | 检索返回片段数 |
| `HISTORY_TURNS` | `20` | 对话历史轮数 |

## 数据更新

游戏版本更新后，重新抓取最新数据并重跑导入（脚本幂等）：

```bash
Remove-Item -Recurse -Force data/StarRailData
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1
# 重新执行第 4 步两条导入命令
```

## 测试与验收

```bash
.venv\Scripts\python.exe -m pytest -v
```

验收测试集（见 DESIGN.md 第 5 节，18 题五类）需在配置 API Key 后逐题手动验证并记录表现。
```

- [ ] **Step 2: run full test suite**

```bash
.venv\Scripts\python.exe -m pytest -v
```
Expected: all tests pass.

- [ ] **Step 3: start server and verify endpoints end-to-end**

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Then:
```bash
curl.exe -s http://127.0.0.1:8000/api/health
curl.exe -s -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" -d '{"message":"你好","history":[]}'
```
Expected: health JSON with `db_chunks > 0`; chat returns a reply (either LLM reply or persona fallback when key missing). Then stop the server.

- [ ] **Step 4: commit**

```bash
git add README.md
git commit -m "docs: add README with run/config/data-update/acceptance instructions"
```

---

## Acceptance Checklist (from DESIGN.md §5)

- [ ] 剧情知识问答（题 1–5）：帕姆能基于检索结果回答，不编造
- [ ] 人设一致性（题 6–9）：自称本帕姆、称开拓者、语气傲娇可爱
- [ ] 超纲问题（题 10–12）：帕姆口吻带开，不破功
- [ ] 违规内容（题 13–15）：列车长口吻拒绝，绝不配合
- [ ] 未知剧情（题 16–17）：如实说明智库没有，不乱编
- [ ] 多语言（题 18）：英文提问跟随英文回答

Manual acceptance requires `ZHIPU_API_KEY` in `.env`; record each question's result in the final report.
