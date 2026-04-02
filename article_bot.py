#!/usr/bin/env python3
"""Automated article bot for generating Chinet-focused articles from public sources.

Usage:
  python article_bot.py --run-once
  python article_bot.py --interval-minutes 120

Environment variables:
  OPENAI_API_KEY   Required for article drafting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "ChinetArticleBot/1.0 (+public-source-collector)"


@dataclass
class SourceItem:
    url: str
    name: str


@dataclass
class RunResult:
    article_path: Path | None
    total_sources: int
    fetched_sources: int
    failed_sources: int
    skipped_seen_sources: int


class SourceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_sources (
                source_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                first_seen_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def has_seen(self, source_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_sources WHERE source_hash = ?", (source_hash,)
        ).fetchone()
        return row is not None

    def mark_seen(self, source_hash: str, url: str, title: str) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO seen_sources (source_hash, url, title, first_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (source_hash, url, title, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()


class PublicSourceFetcher:
    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, source: SourceItem) -> str | None:
        req = Request(source.url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                if "text/html" not in (resp.headers.get("Content-Type") or ""):
                    return None
                html = resp.read().decode("utf-8", errors="ignore")
                return self._html_to_text(html)
        except (HTTPError, URLError, TimeoutError):
            return None

    @staticmethod
    def _html_to_text(html: str) -> str:
        # Remove script/style blocks first.
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text


class ArticleWriter:
    def __init__(self, model: str, api_key: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def draft(self, snippets: List[str], source_urls: List[str]) -> str:
        joined = "\n\n".join(snippets)
        prompt = (
            "Write a factual, readable article about the Chinet tableware brand using only the "
            "public-source notes below. Avoid speculation and explicitly mention when details are "
            "uncertain. Include a short headline and subheading, followed by 4-6 short sections.\n\n"
            f"Source URLs:\n- " + "\n- ".join(source_urls) + "\n\n"
            f"Public notes:\n{joined[:16000]}"
        )
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            temperature=0.6,
        )
        return response.output_text.strip()


def load_sources(sources_file: Path) -> List[SourceItem]:
    data = json.loads(sources_file.read_text(encoding="utf-8"))
    return [SourceItem(**item) for item in data["sources"]]


def source_hash(url: str, content: str) -> str:
    return hashlib.sha256(f"{url}::{content[:5000]}".encode("utf-8")).hexdigest()


def write_article(out_dir: Path, article_text: str, source_urls: Iterable[str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"chinet-article-{ts}.md"
    footer = "\n\n---\nSources:\n" + "\n".join(f"- {u}" for u in source_urls)
    out_path.write_text(article_text + footer + "\n", encoding="utf-8")
    return out_path


def run_once(
    sources_file: Path,
    db_path: Path,
    out_dir: Path,
    model: str,
    api_key: str,
) -> RunResult:
    sources = load_sources(sources_file)
    store = SourceStore(db_path)
    fetcher = PublicSourceFetcher()

    new_snippets: List[str] = []
    used_urls: List[str] = []
    fetched_sources = 0
    failed_sources = 0
    skipped_seen_sources = 0

    for src in sources:
        text = fetcher.fetch(src)
        if not text:
            failed_sources += 1
            continue
        fetched_sources += 1
        sig = source_hash(src.url, text)
        if store.has_seen(sig):
            skipped_seen_sources += 1
            continue

        snippet = text[:2000]
        new_snippets.append(f"[{src.name}] {snippet}")
        used_urls.append(src.url)
        store.mark_seen(sig, src.url, src.name)

    if not new_snippets:
        return RunResult(
            article_path=None,
            total_sources=len(sources),
            fetched_sources=fetched_sources,
            failed_sources=failed_sources,
            skipped_seen_sources=skipped_seen_sources,
        )

    writer = ArticleWriter(model=model, api_key=api_key)
    article = writer.draft(new_snippets, used_urls)
    return RunResult(
        article_path=write_article(out_dir, article, used_urls),
        total_sources=len(sources),
        fetched_sources=fetched_sources,
        failed_sources=failed_sources,
        skipped_seen_sources=skipped_seen_sources,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Chinet-focused articles continuously.")
    parser.add_argument("--sources", default="sources.json", help="Path to source config JSON")
    parser.add_argument("--db", default="state.db", help="Path to sqlite state DB")
    parser.add_argument("--out", default="articles", help="Directory for generated markdown files")
    parser.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model for drafting")
    parser.add_argument(
        "--api-key",
        default=None,
        help="OpenAI API key (preferred: use OPENAI_API_KEY env var instead)",
    )
    parser.add_argument(
        "--api-key-file",
        default=None,
        help="Path to a file containing your OpenAI API key",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=120,
        help="Polling interval in minutes for continuous mode",
    )
    parser.add_argument("--run-once", action="store_true", help="Run one cycle and exit")
    return parser.parse_args()


def ensure_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key.strip()

    if args.api_key_file:
        key_path = Path(args.api_key_file)
        if not key_path.exists():
            raise SystemExit(f"API key file not found: {key_path}")
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    raise SystemExit(
        "OpenAI API key is required. Provide one with --api-key, --api-key-file, or OPENAI_API_KEY."
    )


def main() -> None:
    args = parse_args()
    api_key = ensure_api_key(args)

    sources_file = Path(args.sources)
    db_path = Path(args.db)
    out_dir = Path(args.out)

    if args.run_once:
        result = run_once(sources_file, db_path, out_dir, args.model, api_key)
        if result.article_path:
            print(f"Created article: {result.article_path}")
        else:
            print(
                "No new source material. "
                f"Fetched {result.fetched_sources}/{result.total_sources} sources, "
                f"failed {result.failed_sources}, "
                f"already-seen {result.skipped_seen_sources}."
            )
        return

    print(f"Starting continuous mode. Interval: {args.interval_minutes} minutes")
    try:
        while True:
            result = run_once(sources_file, db_path, out_dir, args.model, api_key)
            now = datetime.now(timezone.utc).isoformat()
            if result.article_path:
                print(f"[{now}] Created article: {result.article_path}")
            else:
                print(
                    f"[{now}] No new source material. "
                    f"Fetched {result.fetched_sources}/{result.total_sources}, "
                    f"failed {result.failed_sources}, "
                    f"already-seen {result.skipped_seen_sources}."
                )
            time.sleep(args.interval_minutes * 60)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
