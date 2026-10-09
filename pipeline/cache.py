"""The raw-download cache. Every download is kept exactly as received.

Layout: data/raw/<key>/<snapshot id>/<file> plus meta.json, where the snapshot
id is the UTC time of the download (e.g. 20261009T143005Z). Files are made
read-only once written, and their SHA-256 fingerprint is checked every time
they are read, so a changed raw file fails loudly instead of being used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class CacheIntegrityError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    key: str
    dir: Path
    meta: dict[str, Any]

    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def file(self) -> Path:
        return self.dir / self.meta["filename"]

    @property
    def retrieved_at(self) -> str:
        return self.meta["retrieved_at"]

    @property
    def sha256(self) -> str:
        return self.meta["sha256"]

    def relative_file(self) -> str:
        """The raw file's path relative to the project folder, with / separators."""
        try:
            return self.file.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            return self.file.as_posix()

    def verify(self) -> None:
        actual = sha256_bytes(self.file.read_bytes())
        if actual != self.sha256:
            raise CacheIntegrityError(
                f"The raw file {self.relative_file()} no longer matches the fingerprint recorded when it "
                "was downloaded, so it has been changed. Raw files must never be edited. Move the snapshot "
                f"folder {self.dir.name} out of the cache and download again with --refresh."
            )

    def read_bytes(self) -> bytes:
        self.verify()
        return self.file.read_bytes()


class RawCache:
    def __init__(self, root: Path):
        self.root = root

    def _key_dir(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(not _SEGMENT.match(p) or p in (".", "..") for p in parts):
            raise ValueError(f"unsafe cache key: {key!r}")
        return self.root.joinpath(*parts)

    def snapshots(self, key: str) -> list[Snapshot]:
        key_dir = self._key_dir(key)
        if not key_dir.is_dir():
            return []
        found = []
        for d in sorted(p for p in key_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            meta_file = d / "meta.json"
            if meta_file.is_file():
                found.append(Snapshot(key, d, json.loads(meta_file.read_text(encoding="utf-8"))))
        return found

    def latest(self, key: str) -> Snapshot | None:
        snaps = self.snapshots(key)
        return snaps[-1] if snaps else None

    def get(self, key: str, snapshot_id: str) -> Snapshot:
        for snap in self.snapshots(key):
            if snap.id == snapshot_id:
                return snap
        raise FileNotFoundError(f"no snapshot {snapshot_id} for {key} in the cache")

    def store(self, key: str, filename: str, content: bytes, meta: dict[str, Any],
              now: datetime | None = None) -> Snapshot:
        if not _SEGMENT.match(filename):
            raise ValueError(f"unsafe file name: {filename!r}")
        key_dir = self._key_dir(key)
        key_dir.mkdir(parents=True, exist_ok=True)
        now = (now or datetime.now(UTC)).astimezone(UTC)
        snap_id = now.strftime("%Y%m%dT%H%M%SZ")
        n = 1
        while (key_dir / snap_id).exists():
            n += 1
            snap_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{n}"

        record = dict(meta)
        record.update(
            key=key,
            filename=filename,
            retrieved_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sha256=sha256_bytes(content),
            bytes=len(content),
        )
        # Write into a hidden folder first, then rename, so a crash never leaves a half snapshot.
        tmp = key_dir / f".tmp-{snap_id}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        (tmp / filename).write_bytes(content)
        (tmp / "meta.json").write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        final = key_dir / snap_id
        os.replace(tmp, final)
        for f in (final / filename, final / "meta.json"):
            os.chmod(f, stat.S_IREAD)
        return Snapshot(key, final, record)
