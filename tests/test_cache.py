"""SQLite cache: TTL semantics and graceful degradation on every kind of failure."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conftest import forecast_payload, yerevan_location
from weather_cli import codec
from weather_cli.cache import SCHEMA_VERSION, SQLiteCache
from weather_cli.providers.base import GeoMatch
from weather_cli.providers.open_meteo import parse_forecast

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


@pytest.fixture
def cache(tmp_path: Path) -> SQLiteCache:
    return SQLiteCache(tmp_path / "nested" / "cache.sqlite3")


def test_miss_then_hit(cache: SQLiteCache) -> None:
    assert cache.get("ns", "k") is None
    cache.put("ns", "k", "payload", now=NOW, ttl=timedelta(minutes=10))
    entry = cache.get("ns", "k")
    assert entry is not None and entry.payload == "payload"
    assert entry.is_fresh(NOW + timedelta(minutes=9))
    assert not entry.is_fresh(NOW + timedelta(minutes=10))
    assert entry.age(NOW + timedelta(minutes=3)) == timedelta(minutes=3)


def test_persists_across_instances(cache: SQLiteCache) -> None:
    cache.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=10))
    reopened = SQLiteCache(cache.path)
    entry = reopened.get("ns", "k")
    assert entry is not None and entry.payload == "v"


def test_namespaces_and_keys_do_not_collide(cache: SQLiteCache) -> None:
    cache.put("a", "k", "1", now=NOW, ttl=timedelta(minutes=1))
    cache.put("b", "k", "2", now=NOW, ttl=timedelta(minutes=1))
    assert cache.get("a", "k").payload == "1"  # type: ignore[union-attr]
    assert cache.get("b", "k").payload == "2"  # type: ignore[union-attr]


def test_long_expired_rows_are_purged_on_write(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "c.sqlite3", retention=timedelta(hours=1))
    cache.put("ns", "old", "x", now=NOW, ttl=timedelta(minutes=10))
    cache.put("ns", "new", "y", now=NOW + timedelta(hours=2), ttl=timedelta(minutes=10))
    assert cache.get("ns", "old") is None
    assert cache.get("ns", "new") is not None


def test_corrupt_file_is_replaced_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    path.write_bytes(b"this is definitely not a sqlite database" * 50)
    cache = SQLiteCache(path)
    assert cache.get("ns", "k") is None
    cache.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=5))
    assert cache.get("ns", "k") is not None


def test_schema_mismatch_resets_entries(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    old = SQLiteCache(path)
    old.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=5))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    fresh = SQLiteCache(path)
    assert fresh.get("ns", "k") is None
    with closing(sqlite3.connect(path)) as db, db:
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert version == str(SCHEMA_VERSION)


def test_unwritable_location_disables_cache_quietly(tmp_path: Path) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    cache = SQLiteCache(blocker / "cache.sqlite3")  # parent is a file: cannot create
    cache.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=5))
    assert cache.get("ns", "k") is None
    assert cache.stats(NOW).available is False


def test_locked_database_is_a_miss(cache: SQLiteCache) -> None:
    cache.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=5))
    holder = sqlite3.connect(cache.path, timeout=0)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        locked = SQLiteCache(cache.path)
        locked._initialized = True  # skip schema step; exercise the read path itself
        assert locked.get("ns", "k") is None
        locked.put("ns", "k2", "v", now=NOW, ttl=timedelta(minutes=5))  # must not raise
    finally:
        holder.rollback()
        holder.close()


def test_invalid_timestamps_in_row_are_a_miss(cache: SQLiteCache) -> None:
    cache.put("ns", "k", "v", now=NOW, ttl=timedelta(minutes=5))
    with closing(sqlite3.connect(cache.path)) as db, db:
        db.execute("UPDATE entries SET stored_at = 'garbage'")
    assert cache.get("ns", "k") is None


def test_stats_and_clear(cache: SQLiteCache) -> None:
    assert cache.stats(NOW).available is False
    assert cache.clear() == 0
    cache.put("geo", "a", "1", now=NOW, ttl=timedelta(hours=1))
    cache.put("wx", "b", "2", now=NOW - timedelta(minutes=20), ttl=timedelta(minutes=10))
    stats = cache.stats(NOW)
    assert stats.available
    assert stats.entries == {"geo": (1, 0), "wx": (0, 1)}
    assert stats.size_bytes > 0
    assert cache.clear() == 2
    assert not cache.path.exists()
    assert cache.get("geo", "a") is None  # usable again after clearing


def test_report_round_trip_preserves_values_and_zone() -> None:
    report = parse_forecast(forecast_payload("America/New_York"), yerevan_location(), NOW)
    restored = codec.loads_report(codec.dumps_report(report))
    assert restored == report
    assert str(restored.hourly[0].time.tzinfo) == "America/New_York"


def test_matches_round_trip() -> None:
    matches = [GeoMatch(yerevan_location(), True, ("0010",))]
    assert codec.loads_matches(codec.dumps_matches(matches)) == matches


@pytest.mark.parametrize(
    "text", ["", "{", "[]", '{"v": 99, "report": {}}', '{"v": 1, "report": {"timezone": "UTC"}}']
)
def test_corrupt_report_payloads_raise_codec_error(text: str) -> None:
    with pytest.raises(codec.CodecError):
        codec.loads_report(text)


@pytest.mark.parametrize("text", ["{}", '{"v": 1, "matches": {}}', '{"v": 1, "matches": [1]}'])
def test_corrupt_match_payloads_raise_codec_error(text: str) -> None:
    with pytest.raises(codec.CodecError):
        codec.loads_matches(text)
