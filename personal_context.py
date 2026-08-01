#!/usr/bin/env python3
"""Read Mike's historical Second Brain snapshot without the retired Bruce service.

The local Chroma database is treated as a dated evidence corpus, not as live
infrastructure. This module only uses its SQLite full-text index, so it does not
need Chroma, Ollama, or the old HTTP service.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "data" / "chroma_db" / "chroma.sqlite3"

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}

SIGNAL_KINDS = {
    "blog": "written_by_mike",
    "youtube": "spoken_or_published_by_mike",
    "stashit": "saved_or_commented_on_by_mike",
    "github": "built_or_contributed_to_by_mike",
    "articles": "reference_corpus",
    "deep_research": "previous_research",
}


class PersonalContextError(RuntimeError):
    """Raised when the local historical corpus cannot be queried."""


@dataclass(frozen=True)
class CorpusStatus:
    database_path: str
    item_count: int
    latest_created_at: str | None
    source_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_path": self.database_path,
            "item_count": self.item_count,
            "latest_created_at": self.latest_created_at,
            "source_counts": self.source_counts,
            "live": False,
            "warning": (
                "Historical local snapshot only. It does not prove current interests "
                "or private listening history."
            ),
        }


def _fts_query(value: str) -> str:
    """Turn natural language into a conservative FTS5 OR query."""

    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", value.lower()):
        if token in STOP_WORDS or token in terms:
            continue
        terms.append(token)
    if not terms:
        raise PersonalContextError("The context search did not contain usable terms")
    return " OR ".join(f'"{term}"' for term in terms[:16])


def _metadata_value(row: sqlite3.Row) -> Any:
    for key in ("string_value", "int_value", "float_value", "bool_value"):
        value = row[key]
        if value is not None:
            return value
    return None


def _excerpt(value: str, query: str, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    if len(value) <= limit:
        return value

    terms = [
        term
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", query.lower())
        if term not in STOP_WORDS
    ]
    lowered = value.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 180)
    end = min(len(value), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(value) else ""
    return f"{prefix}{value[start:end].strip()}{suffix}"


class PersonalContextIndex:
    def __init__(self, database_path: Path = DEFAULT_DATABASE) -> None:
        self.database_path = database_path.expanduser().resolve()
        if not self.database_path.exists():
            raise PersonalContextError(
                f"Historical context database does not exist: {self.database_path}"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _metadata(self, connection: sqlite3.Connection, row_id: int) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT key, string_value, int_value, float_value, bool_value
            FROM embedding_metadata
            WHERE id = ?
            """,
            (row_id,),
        )
        return {str(row["key"]): _metadata_value(row) for row in rows}

    def status(self) -> CorpusStatus:
        with self._connect() as connection:
            item_count, latest = connection.execute(
                "SELECT COUNT(*), MAX(created_at) FROM embeddings"
            ).fetchone()
            source_rows = connection.execute(
                """
                SELECT string_value AS source, COUNT(*) AS item_count
                FROM embedding_metadata
                WHERE key = 'source' AND string_value IS NOT NULL
                GROUP BY string_value
                ORDER BY item_count DESC
                """
            ).fetchall()
        return CorpusStatus(
            database_path=str(self.database_path),
            item_count=int(item_count),
            latest_created_at=str(latest) if latest is not None else None,
            source_counts={
                str(row["source"]): int(row["item_count"]) for row in source_rows
            },
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 15,
        sources: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return deduplicated personal evidence with explicit signal labels."""

        if limit < 1:
            return []
        allowed_sources = set(sources or [])
        fetch_limit = max(limit * 4, 30)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    embedding_fulltext_search.rowid AS row_id,
                    embedding_fulltext_search.string_value AS document,
                    bm25(embedding_fulltext_search) AS rank,
                    embeddings.embedding_id,
                    embeddings.created_at
                FROM embedding_fulltext_search
                JOIN embeddings
                    ON embeddings.id = embedding_fulltext_search.rowid
                WHERE embedding_fulltext_search MATCH ?
                ORDER BY rank ASC
                LIMIT ?
                """,
                (_fts_query(query), fetch_limit),
            ).fetchall()

            results: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                metadata = self._metadata(connection, int(row["row_id"]))
                source = str(metadata.get("source") or "unknown")
                if allowed_sources and source not in allowed_sources:
                    continue

                title = html.unescape(str(metadata.get("title") or "")).strip()
                url = str(metadata.get("url") or "").strip()
                note = html.unescape(str(metadata.get("note") or "")).strip()
                dedupe_key = url or f"{source}:{title}" or str(row["embedding_id"])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                results.append(
                    {
                        "evidence_id": f"P{len(results) + 1:02d}",
                        "embedding_id": str(row["embedding_id"]),
                        "source": source,
                        "signal_kind": SIGNAL_KINDS.get(source, "historical_reference"),
                        "title": title or "Untitled corpus item",
                        "url": url or None,
                        "mike_note": note or None,
                        "excerpt": _excerpt(str(row["document"]), query),
                        "corpus_created_at": str(row["created_at"]),
                        "fts_rank": float(row["rank"]),
                    }
                )
                if len(results) >= limit:
                    break
        return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Mike Pod's dated local personal-context snapshot."
    )
    parser.add_argument("query", nargs="?")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()

    try:
        index = PersonalContextIndex(args.database)
        if args.status:
            print(json.dumps(index.status().as_dict(), indent=2))
            return 0
        if not args.query:
            parser.error("query is required unless --status is used")
        print(json.dumps(index.search(args.query, limit=args.limit), indent=2))
        return 0
    except PersonalContextError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
