"""Persistent TTL cache in one SQLite file. Failures degrade to cache misses.

Stores JSON text produced by :mod:`weather_cli.codec` (canonical metric data,
never display strings). Every operation swallows ``sqlite3`` and filesystem
errors: a corrupted, locked, read-only or version-mismatched cache can make the
app slower, never broken. Diagnostics appear only with ``--verbose``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FILE_NAME = "cache-v1.sqlite3"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    payload: str
    stored_at: datetime
    expires_at: datetime

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at

    def age(self, now: datetime) -> timedelta:
        return now - self.stored_at


@dataclass(frozen=True, slots=True)
class CacheStats:
    path: Path
    available: bool
    entries: dict[str, tuple[int, int]]
    """namespace -> (fresh, expired)."""
    size_bytes: int


class SQLiteCache:
    def __init__(self, path: Path, *, retention: timedelta = timedelta(hours=1)) -> None:
        self.path = path
        self._retention = retention
        self._disabled = False
        self._initialized = False

    # -- lifecycle -----------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # closing() releases the file handle; the inner context commits or rolls back.
        with closing(sqlite3.connect(self.path, timeout=1.0)) as connection, connection:
            yield connection

    def _ensure_schema(self) -> bool:
        if self._disabled:
            return False
        if self._initialized:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._create_or_migrate()
        except sqlite3.OperationalError as exc:  # locked, read-only, cannot open
            log.debug("cache disabled: %s", exc)
            self._disabled = True
            return False
        except sqlite3.DatabaseError as exc:  # e.g. "file is not a database"
            log.debug("cache file unusable (%s); recreating %s", exc, self.path)
            if not self._recreate():
                return False
        except (sqlite3.Error, OSError) as exc:
            log.debug("cache disabled: %s", exc)
            self._disabled = True
            return False
        self._initialized = True
        return True

    def _create_or_migrate(self) -> None:
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is not None and row[0] != str(SCHEMA_VERSION):
                log.debug("cache schema %s != %s; resetting", row[0], SCHEMA_VERSION)
                db.execute("DROP TABLE IF EXISTS entries")
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,"
                " stored_at REAL NOT NULL, expires_at REAL NOT NULL,"
                " PRIMARY KEY (namespace, key))"
            )
            db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _recreate(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            self._create_or_migrate()
            return True
        except (sqlite3.Error, OSError) as exc:
            log.debug("cache disabled after failed recreate: %s", exc)
            self._disabled = True
            return False

    # -- operations ----------------------------------------------------------------

    def get(self, namespace: str, key: str) -> CacheEntry | None:
        """Return the entry even if expired; callers decide freshness."""
        if not self._ensure_schema():
            return None
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload, stored_at, expires_at FROM entries"
                    " WHERE namespace = ? AND key = ?",
                    (namespace, key),
                ).fetchone()
        except sqlite3.Error as exc:
            log.debug("cache read failed: %s", exc)
            return None
        if row is None:
            return None
        payload, stored_at, expires_at = row
        try:
            return CacheEntry(str(payload), _from_epoch(stored_at), _from_epoch(expires_at))
        except (TypeError, ValueError, OverflowError, OSError):
            log.debug("cache entry %s/%s has invalid timestamps", namespace, key)
            return None

    def put(self, namespace: str, key: str, payload: str, *, now: datetime, ttl: timedelta) -> None:
        if not self._ensure_schema():
            return
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO entries"
                    " (namespace, key, payload, stored_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (namespace, key, payload, now.timestamp(), (now + ttl).timestamp()),
                )
                db.execute(
                    "DELETE FROM entries WHERE expires_at < ?",
                    ((now - self._retention).timestamp(),),
                )
        except sqlite3.Error as exc:
            log.debug("cache write failed: %s", exc)

    def clear(self) -> int:
        """Delete the cache file; returns how many entries it held."""
        if not self.path.exists():
            return 0
        count = 0
        if self._ensure_schema():
            try:
                with self._connect() as db:
                    count = int(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
            except sqlite3.Error as exc:
                log.debug("cache count failed: %s", exc)
        try:
            self.path.unlink()
        except OSError as exc:
            log.debug("cache file could not be removed: %s", exc)
            return 0
        self._initialized = False
        return count

    def stats(self, now: datetime) -> CacheStats:
        entries: dict[str, tuple[int, int]] = {}
        available = self.path.exists() and self._ensure_schema()
        if available:
            try:
                with self._connect() as db:
                    rows = db.execute(
                        "SELECT namespace, SUM(expires_at > ?), SUM(expires_at <= ?)"
                        " FROM entries GROUP BY namespace ORDER BY namespace",
                        (now.timestamp(), now.timestamp()),
                    ).fetchall()
                entries = {str(ns): (int(fresh), int(expired)) for ns, fresh, expired in rows}
            except sqlite3.Error as exc:
                log.debug("cache stats failed: %s", exc)
                available = False
        size = self.path.stat().st_size if self.path.exists() else 0
        return CacheStats(self.path, available, entries, size)


def _from_epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)
