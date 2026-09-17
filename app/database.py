"""SQLite + sqlite-vec vector store for dialogue chunks."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlite_vec
import numpy as np

# 字面命中的合成距离：相似度 0.75，高于「一次查询不足」的阈值，但比强向量命中稍低
LITERAL_MATCH_DISTANCE = 0.25

# 实体词表：从片段文本里抽「物品「X」」这类带书名号的配置名
_NAME_PATTERN = re.compile(r"(?:物品|怪物|任务|角色|光锥|遗器)「([^」]{2,14})」")
_JUNK_SPEAKERS = {
    "",
    "未知",
    "？？？",
    "???",
    "？",
    "Player",
    "PlayerAuto",
    "System",
}


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
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _decode_vector(raw: Any, dim: int) -> list[float] | None:
    """vec0 返回的是二进制或 JSON 字符串，统一解码成 float 列表。"""
    try:
        if isinstance(raw, (bytes, bytearray, memoryview)):
            vector = np.frombuffer(bytes(raw), dtype=np.float32)
        elif isinstance(raw, str):
            vector = np.array(json.loads(raw), dtype=np.float32)
        else:
            vector = np.asarray(raw, dtype=np.float32)
    except Exception:
        return None
    if vector.size != dim:
        return None
    return [float(value) for value in vector]


class VectorDB:
    def __init__(self, db_path: str | Path, dim: int = 512):
        self.path = Path(db_path)
        self.dim = dim
        self._lock = threading.RLock()
        self.conn = _connect(self.path)
        with self._lock:
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
        with self._lock:
            self.conn.execute("DELETE FROM vec_segments")
            self.conn.execute("DELETE FROM segments")
            self.conn.commit()

    def insert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        with self._lock:
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
        self,
        query_embedding: list[float],
        k: int = 6,
        category: str | None = None,
        speaker: str | None = None,
    ) -> list[tuple[Chunk, float]]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT rowid, distance FROM vec_segments "
                "WHERE embedding MATCH ? AND k = ?",
                (json.dumps(query_embedding), k),
            )
            matches = cur.fetchall()
            ids = [row[0] for row in matches]
            if not ids:
                return []
            dist_by_id = dict(matches)
            placeholders = ",".join("?" for _ in ids)
            sql = (
                "SELECT id, category, scene, speaker, text, pom_pom, meta "
                f"FROM segments WHERE id IN ({placeholders})"
            )
            params: list[Any] = list(ids)
            if category:
                sql += " AND category = ?"
                params.append(category)
            if speaker:
                sql += " AND speaker = ?"
                params.append(speaker)
            self.conn.row_factory = sqlite3.Row
            chunk_by_id = {r["id"]: Chunk(**dict(r)) for r in self.conn.execute(sql, params)}
            self.conn.row_factory = None
            out = []
            for cid in ids:
                chunk = chunk_by_id.get(cid)
                if chunk:
                    out.append((chunk, dist_by_id.get(cid, 1.0)))
            return out

    def search_speaker(
        self,
        query_embedding: list[float],
        speaker: str,
        k: int = 6,
    ) -> list[tuple[Chunk, float]]:
        """Rank only the chunks spoken by a given character (true within-subset search)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT s.id, s.category, s.scene, s.speaker, s.text, s.pom_pom, "
                "s.meta, v.embedding "
                "FROM segments s JOIN vec_segments v ON v.rowid = s.embedding_id "
                "WHERE s.speaker = ?",
                (speaker,),
            ).fetchall()
        query = np.asarray(query_embedding, dtype=np.float32)
        query_norm = float(np.linalg.norm(query))
        scored: list[tuple[float, tuple]] = []
        for row in rows:
            raw = row[7]
            try:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    vec = np.frombuffer(bytes(raw), dtype=np.float32)
                elif isinstance(raw, str):
                    vec = np.array(json.loads(raw), dtype=np.float32)
                else:
                    vec = np.asarray(raw, dtype=np.float32)
            except Exception:
                continue
            if vec.size != self.dim:
                continue
            norm = float(np.linalg.norm(vec))
            if query_norm == 0.0 or norm == 0.0:
                distance = 1.0
            else:
                similarity = float(np.dot(query, vec) / (query_norm * norm))
                distance = round(1.0 - similarity, 6)
            scored.append((distance, row))
        scored.sort(key=lambda item: item[0])
        out: list[tuple[Chunk, float]] = []
        for distance, row in scored[:k]:
            chunk = Chunk(row[0], row[1], row[2], row[3], row[4], bool(row[5]), row[6])
            out.append((chunk, distance))
        return out

    def search_text(
        self,
        keyword: str,
        k: int = 5,
        category: str | None = None,
        query_vector: list[float] | None = None,
        candidate_pool: int = 200,
    ) -> list[tuple[Chunk, float]]:
        """字面检索（SQL LIKE）：实体名这类词向量检索容易漏，用它兜底。

        返回的 distance 是合成的（``LITERAL_MATCH_DISTANCE``），可以直接和其它
        检索结果一起 ``merge_ranked``；更短的片段更聚焦，所以按长度升序。
        """
        keyword = keyword.strip()
        if not keyword:
            return []
        escaped = (
            keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        with self._lock:
            sql = (
                "SELECT id, category, scene, speaker, text, pom_pom, meta FROM segments "
                "WHERE text LIKE ? ESCAPE '\\'"
            )
            params: list[Any] = [f"%{escaped}%"]
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " ORDER BY LENGTH(text) ASC LIMIT ?"
            params.append(max(k, candidate_pool) if query_vector else k)
            self.conn.row_factory = sqlite3.Row
            rows = self.conn.execute(sql, params).fetchall()
            self.conn.row_factory = None
        chunks = [Chunk(**dict(row)) for row in rows]
        if query_vector is None:
            return [(chunk, LITERAL_MATCH_DISTANCE) for chunk in chunks]
        # 字面命中可能几十条，按长度排序会把「长但真正相关」的片段截掉，
        # 所以有查询向量时按语义相似度重排再截断。
        from app.retrieval import cosine_similarity  # 延迟导入，避免循环依赖

        vectors = self.chunk_embeddings([chunk.id for chunk in chunks])
        scored = []
        for chunk in chunks:
            vector = vectors.get(chunk.id)
            similarity = (
                cosine_similarity(query_vector, vector) if vector is not None else 0.0
            )
            scored.append((similarity, chunk))
        scored.sort(key=lambda item: -item[0])
        # 通道内按相似度排序，但对外的距离仍用「精确匹配」的合成值：
        # 字面命中的权重不该被问题相似度稀释（实验证明那样会掉召回）。
        return [(chunk, LITERAL_MATCH_DISTANCE) for _, chunk in scored[:k]]

    def chunk_embeddings(self, chunk_ids: list[int]) -> dict[int, list[float]]:
        """按 id 取回向量（MMR 去冗余、字面命中门控都要用）。"""
        ids = [chunk_id for chunk_id in chunk_ids if chunk_id is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid, embedding FROM vec_segments "
                f"WHERE rowid IN ({placeholders})",
                ids,
            ).fetchall()
        out: dict[int, list[float]] = {}
        for chunk_id, raw in rows:
            vector = _decode_vector(raw, self.dim)
            if vector is not None:
                out[chunk_id] = vector
        return out

    def scene_neighbors(
        self, chunk_id: int, before: int = 1, after: int = 1
    ) -> list[Chunk]:
        """同场景里紧挨着这条的片段（按 id 顺序，用于指代消解）。

        场景内 id 是连续分配的，所以"相邻 id"就是"相邻台词"。
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT category, scene FROM segments WHERE id = ?", (chunk_id,)
            ).fetchone()
            if not row:
                return []
            category, scene = row
            rows = self.conn.execute(
                "SELECT id, category, scene, speaker, text, pom_pom, meta FROM segments "
                "WHERE category = ? AND scene = ? AND id BETWEEN ? AND ? AND id != ? "
                "ORDER BY id",
                (category, scene, chunk_id - before, chunk_id + after, chunk_id),
            ).fetchall()
        return [Chunk(*row) for row in rows]

    def search_text_terms(
        self,
        any_terms: list[str],
        all_terms: list[str],
        k: int = 3,
        query_vector: list[float] | None = None,
        candidate_pool: int = 40,
    ) -> list[tuple[Chunk, float]]:
        """联合字面检索：(any_terms 任一命中) AND (all_terms 全部命中)。

        用于「实体 + 线索」这类查询：问「阿哈在列车上干过什么」时，
        (阿哈|乐子神|欢愉星神) AND 列车 能直接定位到「乐子神…把列车炸成两截」。
        """
        any_terms = [term.strip() for term in any_terms if term.strip()]
        all_terms = [term.strip() for term in all_terms if term.strip()]
        if not any_terms:
            return []

        def like(term: str) -> tuple[str, str]:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return "text LIKE ? ESCAPE '\\'", f"%{escaped}%"

        clauses: list[str] = []
        params: list[Any] = []
        any_clause = " OR ".join(like(term)[0] for term in any_terms)
        clauses.append(f"({any_clause})")
        params.extend(like(term)[1] for term in any_terms)
        for term in all_terms:
            clause, value = like(term)
            clauses.append(clause)
            params.append(value)

        sql = (
            "SELECT id, category, scene, speaker, text, pom_pom, meta FROM segments WHERE "
            + " AND ".join(clauses)
            + " ORDER BY LENGTH(text) ASC LIMIT ?"
        )
        params.append(max(k, candidate_pool) if query_vector else k)
        with self._lock:
            self.conn.row_factory = sqlite3.Row
            rows = self.conn.execute(sql, params).fetchall()
            self.conn.row_factory = None
        chunks = [Chunk(**dict(row)) for row in rows]
        if query_vector is None:
            return [(chunk, LITERAL_MATCH_DISTANCE) for chunk in chunks]
        # 有查询向量时按语义相似度排序：字面命中常有几十条，
        # 真正相关的那条可能很长（按长度排序会被截掉）。
        from app.retrieval import cosine_similarity  # 延迟导入，避免循环依赖

        vectors = self.chunk_embeddings([chunk.id for chunk in chunks])
        scored = []
        for chunk in chunks:
            vector = vectors.get(chunk.id)
            similarity = (
                cosine_similarity(query_vector, vector) if vector is not None else 0.0
            )
            scored.append((similarity, chunk))
        scored.sort(key=lambda item: -item[0])
        return [(chunk, LITERAL_MATCH_DISTANCE) for _, chunk in scored[:k]]

    def entity_vocabulary(self) -> set[str]:
        """实体词表：角色名（speaker）＋ 配置名（物品/怪物/任务/光锥/遗器）。

        供问题里的实体抽取做最长匹配——「阿哈在列车上干过什么」这种句式，
        规则切分会失效，但词表能直接命中「阿哈」。
        """
        names: set[str] = set()
        with self._lock:
            for (speaker,) in self.conn.execute(
                "SELECT DISTINCT speaker FROM segments WHERE speaker != ''"
            ):
                if speaker not in _JUNK_SPEAKERS and not speaker.startswith("Player"):
                    names.add(speaker)
            for (text,) in self.conn.execute(
                "SELECT text FROM segments WHERE text LIKE '%「%」%'"
            ):
                names.update(_NAME_PATTERN.findall(text))
        return {name.strip() for name in names if 2 <= len(name.strip()) <= 14}

    def pom_pom_samples(self, n: int = 5) -> list[Chunk]:
        with self._lock:
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
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]

    def avatar_names(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT speaker FROM segments "
                "WHERE category = 'avatars' AND speaker != '' "
                "ORDER BY speaker"
            ).fetchall()
            return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self.conn.close()
