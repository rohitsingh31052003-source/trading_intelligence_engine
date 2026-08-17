"""
Filesystem persistence for paper trades (Product Phase 5).

A THIN local persistence layer for the personal trading workstation.
Paper-trade history must survive page refreshes / process restarts to
be useful, so Phase 5 (unlike Phase 2 which deliberately had no
watchlist persistence) introduces a small persistence store.

PERSISTENCE CHOICE (documented):

The repository consistently uses filesystem JSON with atomic writes
(Sprint 11K ``ExperimentPersistence`` discipline). This store follows
the SAME established pattern rather than introducing SQLite:

* One file per paper trade (``<directory>/<paper_trade_id>.json``).
* Atomic write: ``tempfile.mkstemp`` in the SAME directory -> write ->
  flush -> fsync (best-effort) -> ``os.replace`` (single-filesystem
  rename, atomic on Windows + POSIX). A temp file is cleaned up on any
  failure and a typed :class:`PaperTradeStoreError` is raised (never a
  partial target).
* Safe-id validation (``_SAFE_ID_RE``) prevents path traversal.
* Schema-versioned: the schema version is checked by the deserializer
  before any model reconstruction; future versions are rejected.
* No ``pickle`` / ``eval`` / ``exec``. Only ``json`` + ``Decimal`` +
  ``datetime``.
* No global mutable state. No hard-coded absolute paths (``pathlib``
  throughout; default directory relative to cwd).
* Graceful corruption handling: a corrupted / unreadable record raises a
  typed error (never silently swallowed); the store never returns a
  fabricated paper trade.

This is infrastructure for a personal research/trading workstation. It
is NOT a database server, NOT cloud infrastructure, NOT multi-user, and
has NO authentication. It does NOT alter the engine's domain semantics.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from engine.intelligence.paper_trading_serialization import (
    deserialize_paper_trade,
    serialize_paper_trade,
)
from engine.models.paper_trade import PaperTrade


class PaperTradeStoreError(Exception):
    """Base error for paper-trade persistence."""


class PaperTradeNotFoundError(PaperTradeStoreError):
    """A requested paper trade was not found in the store."""


class PaperTradeIntegrityError(PaperTradeStoreError):
    """A persisted paper trade failed an integrity check."""


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Suffix used for persisted paper-trade records.
_RECORD_SUFFIX = ".json"


def _validate_id(paper_trade_id: str) -> None:
    """Ensure a paper-trade id is safe to use as a file name."""

    if not paper_trade_id or not _SAFE_ID_RE.match(paper_trade_id):
        raise PaperTradeStoreError(
            f"Unsafe paper-trade id {paper_trade_id!r}: ids must "
            f"match {str(_SAFE_ID_RE.pattern)!r}."
        )


def default_paper_trade_directory() -> Path:
    """
    Default paper-trade directory, resolved relative to the current
    working directory (``./paper_trades``).

    This avoids hard-coded absolute paths and global mutable state.
    Callers wanting a fixed location should pass an explicit
    ``directory`` to the store.
    """

    return Path.cwd() / "paper_trades"


class PaperTradeStore:
    """
    Filesystem-based paper-trade persistence.

    One JSON file per paper trade. Atomic writes. Safe-id validation.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = default_paper_trade_directory()
        self.directory = Path(directory)

    # ------------------------------------------------------------
    # PATH HELPERS
    # ------------------------------------------------------------

    def path_for(self, paper_trade_id: str) -> Path:
        """Absolute path for a paper-trade record file."""

        _validate_id(paper_trade_id)
        return self.directory / f"{paper_trade_id}{_RECORD_SUFFIX}"

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------

    def save(self, trade: PaperTrade, *, overwrite: bool = True) -> Path:
        """
        Persist a paper trade atomically.

        When ``overwrite`` is ``False`` and a record with the same id
        already exists, raises :class:`PaperTradeStoreError` (no silent
        overwrite). The default is ``overwrite=True`` because paper trades
        are updated through their lifecycle (WAITING -> OPEN -> CLOSED)
        and the latest state must replace the prior state.
        """

        _validate_id(trade.paper_trade_id)
        target = self.path_for(trade.paper_trade_id)
        if not overwrite and target.exists():
            raise PaperTradeStoreError(
                f"Paper trade {trade.paper_trade_id!r} already exists; "
                f"pass overwrite=True to replace it."
            )
        text = serialize_paper_trade(trade)
        self._atomic_write(target, text)
        return target

    def load(self, paper_trade_id: str) -> PaperTrade:
        """Load a paper trade by id. Raises if not found / corrupted."""

        _validate_id(paper_trade_id)
        target = self.path_for(paper_trade_id)
        if not target.exists():
            raise PaperTradeNotFoundError(
                f"Paper trade {paper_trade_id!r} not found at {target!s}."
            )
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise PaperTradeStoreError(
                f"Failed to read paper trade {paper_trade_id!r}: {exc}"
            ) from exc
        try:
            trade = deserialize_paper_trade(text)
        except ValueError as exc:
            raise PaperTradeIntegrityError(
                f"Corrupted paper trade {paper_trade_id!r}: {exc}"
            ) from exc
        # Integrity check: the loaded id must match the file-name id.
        if trade.paper_trade_id != paper_trade_id:
            raise PaperTradeIntegrityError(
                f"Paper-trade id mismatch: file name {paper_trade_id!r} "
                f"vs stored id {trade.paper_trade_id!r}."
            )
        return trade

    def exists(self, paper_trade_id: str) -> bool:
        _validate_id(paper_trade_id)
        return self.path_for(paper_trade_id).exists()

    def list_trades(self) -> list[str]:
        """Return the sorted list of stored paper-trade ids."""

        if not self.directory.exists():
            return []
        ids: list[str] = []
        for entry in self.directory.iterdir():
            if entry.is_file() and entry.suffix == _RECORD_SUFFIX:
                stem = entry.stem
                if _SAFE_ID_RE.match(stem):
                    ids.append(stem)
        ids.sort()
        return ids

    def load_all(self) -> list[PaperTrade]:
        """Load all stored paper trades, sorted by id."""

        ids = self.list_trades()
        return [self.load(pid) for pid in ids]

    def delete(self, paper_trade_id: str) -> None:
        _validate_id(paper_trade_id)
        target = self.path_for(paper_trade_id)
        if not target.exists():
            raise PaperTradeNotFoundError(
                f"Paper trade {paper_trade_id!r} not found at {target!s}."
            )
        try:
            target.unlink()
        except OSError as exc:
            raise PaperTradeStoreError(
                f"Failed to delete paper trade {paper_trade_id!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------
    # ATOMIC WRITE
    # ------------------------------------------------------------

    def _atomic_write(self, target: Path, text: str) -> None:
        """
        Write ``text`` to ``target`` atomically.

        Writes to a temporary file in the SAME directory, flushes,
        fsyncs and closes it, then atomically replaces the target.
        Using the same directory guarantees the ``os.replace`` call is a
        single-filesystem rename (atomic on Windows and POSIX). No
        partially written target file is ever left.
        """

        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)

        prefix = target.name + "."
        fd, tmp_name = tempfile.mkstemp(
            prefix=prefix,
            suffix=".tmp",
            dir=str(directory),
        )
        tmp_path = Path(tmp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass

            os.replace(tmp_path, target)
        except PaperTradeStoreError:
            raise
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise PaperTradeStoreError(
                f"Failed to atomically write paper-trade record "
                f"{target!s}: {exc}"
            ) from exc


__all__ = [
    "PaperTradeIntegrityError",
    "PaperTradeNotFoundError",
    "PaperTradeStore",
    "PaperTradeStoreError",
    "default_paper_trade_directory",
]
