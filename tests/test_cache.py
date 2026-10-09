import os
import stat
from datetime import UTC, datetime

import pytest

from pipeline.cache import CacheIntegrityError, RawCache, sha256_bytes

WHEN = datetime(2026, 10, 9, 14, 30, 5, tzinfo=UTC)


def test_store_and_read_back(tmp_path):
    cache = RawCache(tmp_path)
    snap = cache.store("wdi/data/X/UZB", "data.json", b"[1, 2]", {"url": "https://example.org"}, now=WHEN)
    assert snap.id == "20261009T143005Z"
    assert snap.read_bytes() == b"[1, 2]"
    assert snap.meta["sha256"] == sha256_bytes(b"[1, 2]")
    assert snap.meta["retrieved_at"] == "2026-10-09T14:30:05Z"
    assert snap.meta["url"] == "https://example.org"
    assert cache.latest("wdi/data/X/UZB") == snap
    assert cache.get("wdi/data/X/UZB", snap.id) == snap


def test_raw_files_are_read_only(tmp_path):
    snap = RawCache(tmp_path).store("k", "f.json", b"{}", {}, now=WHEN)
    assert not os.access(snap.file, os.W_OK)
    assert not os.access(snap.dir / "meta.json", os.W_OK)


def test_a_changed_raw_file_fails_loudly(tmp_path):
    snap = RawCache(tmp_path).store("k", "f.json", b"original", {}, now=WHEN)
    os.chmod(snap.file, stat.S_IWRITE | stat.S_IREAD)
    snap.file.write_bytes(b"edited")
    with pytest.raises(CacheIntegrityError, match="no longer matches"):
        snap.read_bytes()


def test_downloads_in_the_same_second_get_separate_snapshots(tmp_path):
    cache = RawCache(tmp_path)
    first = cache.store("k", "f.json", b"1", {}, now=WHEN)
    second = cache.store("k", "f.json", b"2", {}, now=WHEN)
    assert first.id != second.id
    assert [s.id for s in cache.snapshots("k")] == [first.id, second.id]
    assert cache.latest("k").read_bytes() == b"2"


def test_latest_is_the_newest(tmp_path):
    cache = RawCache(tmp_path)
    cache.store("k", "f.json", b"new", {}, now=datetime(2026, 10, 9, tzinfo=UTC))
    cache.store("k", "f.json", b"old", {}, now=datetime(2025, 1, 1, tzinfo=UTC))
    assert cache.latest("k").read_bytes() == b"new"


@pytest.mark.parametrize("key", ["../outside", "a/../b", "a//b", "a b", ""])
def test_unsafe_keys_are_refused(tmp_path, key):
    with pytest.raises(ValueError):
        RawCache(tmp_path).store(key, "f.json", b"", {})


def test_no_half_written_snapshots_are_listed(tmp_path):
    cache = RawCache(tmp_path)
    (tmp_path / "k" / ".tmp-20261009T000000Z").mkdir(parents=True)
    assert cache.snapshots("k") == []
