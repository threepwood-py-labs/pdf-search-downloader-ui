"""SQLite-backed manifest persistence for downloaded PDFs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..models import DownloadOutcome, DownloadRecord, ProviderId


class ManifestStore:
    """Persist and query downloaded PDF records using SQLite."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        """Create the manifest schema when missing."""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    source_url TEXT PRIMARY KEY,
                    final_url TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    sha256_hex TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS downloads_final_url_idx
                ON downloads (final_url)
                """
            )

    def _row_to_record(self, row: sqlite3.Row) -> DownloadRecord:
        """Convert one SQLite row into one domain record."""

        return DownloadRecord(
            provider_id=ProviderId(str(row["provider_id"])),
            title=str(row["title"]),
            source_url=str(row["source_url"]),
            final_url=str(row["final_url"]),
            output_path=Path(str(row["output_path"])),
            outcome=DownloadOutcome(str(row["outcome"])),
            sha256_hex=str(row["sha256_hex"]),
            file_size_bytes=int(row["file_size_bytes"]),
            message=str(row["message"]),
        )

    def find_existing(
        self,
        source_url: str,
        final_url: str,
    ) -> DownloadRecord | None:
        """Return an existing record matched by source or final URL."""

        self.initialize()
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT source_url, final_url, provider_id, title, output_path,
                       outcome, sha256_hex, file_size_bytes, message
                FROM downloads
                WHERE source_url = ? OR final_url = ?
                LIMIT 1
                """,
                (source_url, final_url),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def record_download(self, record: DownloadRecord) -> None:
        """Insert or replace one manifest record."""

        if record.output_path is None:
            raise ValueError("Manifest records require a persisted output path.")
        self.initialize()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO downloads (
                    source_url,
                    final_url,
                    provider_id,
                    title,
                    output_path,
                    outcome,
                    sha256_hex,
                    file_size_bytes,
                    message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.source_url,
                    record.final_url,
                    record.provider_id.value,
                    record.title,
                    str(record.output_path),
                    record.outcome.value,
                    record.sha256_hex,
                    record.file_size_bytes,
                    record.message,
                ),
            )
