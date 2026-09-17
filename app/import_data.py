"""Chunk extracted dialogue records, embed them, and write into VectorDB."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.database import Chunk, VectorDB

# 句末标点：长文本优先在这些地方切，保证每块是完整句子
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；…!?;])")


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """把超长文本切成 ≤ max_chars 的**完整句子块**（只有单句本身超长才硬切）。"""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in (part for part in _SENTENCE_SPLIT.split(text) if part):
        while len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + len(sentence) > max_chars:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    return pieces or [text]


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
        # 换人就切块：一个片段只属于一个说话人（否则 speaker 字段会挂错，
        # 按角色检索会漏掉大量台词）。
        if buf and buf[0][0] != speaker:
            flush()
        for piece in _split_long_text(text, max_chars):
            if len(buf) >= max_lines or (buf and char_count + len(piece) > max_chars):
                flush()
            buf.append((speaker, piece))
            buf_pom.append(bool(rec.get("pom_pom")))
            char_count += len(piece)
            if len(piece) >= max_chars:  # 单句超长硬切出来的满块，先收走
                flush()
    flush()
    return out


def chunk_records(
    records: list[dict], max_lines: int = 4, max_chars: int = 220
) -> list[Chunk]:
    records = _dedupe_records(records)
    final: list[Chunk] = []
    group: list[dict] = []
    for rec in records:
        if group and (
            group[-1]["scene"] != rec["scene"] or group[-1]["category"] != rec["category"]
        ):
            final.extend(_chunk_group(group, len(final), max_lines, max_chars))
            group = []
        group.append(rec)
    if group:
        final.extend(_chunk_group(group, len(final), max_lines, max_chars))
    return _dedupe_chunks(final)


_MAX_ALIASES = 12


def _dedupe_records(records: list[dict]) -> list[dict]:
    """Drop lines repeated verbatim inside the same scene (e.g. skill rows)."""
    seen: set[tuple] = set()
    kept: list[dict] = []
    for record in records:
        key = (
            record["category"],
            record["scene"],
            record.get("speaker", ""),
            record["text"],
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(record)
    return kept


def _dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Collapse identical chunk texts (same monster/mission in many config ids).

    The first occurrence is kept; the other scenes are recorded in ``meta`` so no
    information about where the entry appears is lost.
    """
    kept: list[Chunk] = []
    position_by_key: dict[tuple[str, str], int] = {}
    for chunk in chunks:
        key = (chunk.category, chunk.text)
        position = position_by_key.get(key)
        if position is None:
            chunk.id = len(kept)
            position_by_key[key] = len(kept)
            kept.append(chunk)
            continue
        target = kept[position]
        meta = json.loads(target.meta or "{}")
        meta["alias_count"] = int(meta.get("alias_count", 1)) + 1
        aliases = meta.setdefault("aliases", [])
        if chunk.scene != target.scene and chunk.scene not in aliases:
            aliases.append(chunk.scene)
            if len(aliases) > _MAX_ALIASES:
                aliases.pop()
        target.meta = json.dumps(meta, ensure_ascii=False)
    return kept


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
