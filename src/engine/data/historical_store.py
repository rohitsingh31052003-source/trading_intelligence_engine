"""
Filesystem persistence for historical datasets (Product Phase 6A).

A thin, deterministic local persistence layer for research data,
following the repository's established Sprint 11K discipline:

* One directory per ``(instrument, timeframe)`` pair:
  ``<root>/<INSTRUMENT>/<timeframe>/``.
* ``candles.json`` — the full canonical candle series for that pair.
  Ingestion MERGES new candles with the stored series (dedupe by
  timestamp, chronological sort) so REPEATED INGESTION IS IDEMPOTENT —
  the same ingestion never creates duplicates.
* ``provenance.jsonl`` — one JSON line per ingestion operation
  (append-only) so every import retains provenance.
* Atomic write: ``tempfile.mkstemp`` in the SAME directory -> write ->
  flush -> fsync (best-effort) -> ``os.replace``. A temp file is
  cleaned up on any failure and a typed ``HistoricalDataStoreError`` is
  raised (never a partial target).
* Safe-id validation prevents path traversal on instrument / timeframe.
* Schema-versioned candle file; loaders validate the version before
  model reconstruction.
* No ``pickle`` / ``eval`` / ``exec``; only ``json``.
* No hard-coded absolute paths (``pathlib``; default directory relative
  to the current working directory).
* The ``data/historical/`` directory is ignored by Git (see
  ``.gitignore``) so large market-data files are never committed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from engine.data.historical_serialization import (
    HISTORICAL_SCHEMA_VERSION,
    candle_from_dict,
    candle_to_dict,
    serialize_provenance,
)
from engine.data.historical_times import canonical_timeframe
from engine.models.historical_data import HistoricalProvenance
from engine.models.ohlcv import OHLCVCandle


class HistoricalDataStoreError(Exception):
    """Base error for historical-data persistence."""


class HistoricalDatasetNotFoundError(HistoricalDataStoreError):
    """A requested dataset was not found in the store."""


class HistoricalDataIntegrityError(HistoricalDataStoreError):
    """A persisted dataset failed an integrity / correctness check."""


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9&._~-]+$")


def _validate_id(identity: str, kind: str) -> None:
    """Ensure an instrument / timeframe id is safe to use as a path."""

    if not identity or not _SAFE_ID_RE.match(identity):
        raise HistoricalDataStoreError(
            f"Unsafe {kind} id {identity!r}: ids must match "
            f"{str(_SAFE_ID_RE.pattern)!r}.",
        )


def default_historical_data_directory() -> Path:
    """
    Default historical-data root (``./data/historical``), resolved
    relative to the current working directory.

    This avoids hard-coded absolute paths and global mutable state.
    Callers wanting a fixed location should pass an explicit
    ``directory`` to the store.
    """

    return Path.cwd() / "data" / "historical"


def _dataset_dir(root: Path, instrument: str, timeframe: str) -> Path:
    canonical = canonical_timeframe(timeframe) or timeframe
    return root / instrument.upper() / canonical


def _candles_path(root: Path, instrument: str, timeframe: str) -> Path:
    return _dataset_dir(root, instrument, timeframe) / "candles.json"


def _provenance_path(root: Path, instrument: str, timeframe: str) -> Path:
    return _dataset_dir(root, instrument, timeframe) / "provenance.jsonl"


@dataclass(frozen=True)
class StoredDatasetInfo:
    """Summary of a stored dataset (used by status surfaces / listings)."""

    instrument: str
    timeframe: str
    candle_count: int
    first_timestamp: str | None
    last_timestamp: str | None


class HistoricalDataStore:
    """
    Filesystem persistence for historical datasets.

    One ``candles.json`` per (instrument, timeframe); provenance appended
    to ``provenance.jsonl``. Atomic writes; safe-id validation;
    idempotent ingestion (merge by timestamp).
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = default_historical_data_directory()
        self.directory = Path(directory)

    # ------------------------------------------------------------
    # PATH HELPERS
    # ------------------------------------------------------------

    def path_for(self, instrument: str, timeframe: str) -> Path:
        _validate_id(instrument.upper(), "instrument")
        _validate_id(
            canonical_timeframe(timeframe) or timeframe, "timeframe",
        )
        return _candles_path(self.directory, instrument, timeframe)

    # ------------------------------------------------------------
    # READ
    # ------------------------------------------------------------

    def exists(self, instrument: str, timeframe: str) -> bool:
        return self.path_for(instrument, timeframe).exists()

    def load_candles(self, instrument: str, timeframe: str) -> tuple[OHLCVCandle, ...]:
        """Load the stored candle series. Raises if missing / corrupted."""

        path = self.path_for(instrument, timeframe)
        if not path.exists():
            raise HistoricalDatasetNotFoundError(
                f"Dataset {instrument}/{timeframe} not found at {path!s}.",
            )
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoricalDataIntegrityError(
                f"Corrupted dataset {instrument}/{timeframe}: {exc}",
            ) from exc
        if payload.get("schema_version") != HISTORICAL_SCHEMA_VERSION:
            raise HistoricalDataIntegrityError(
                f"Unsupported schema version in {instrument}/{timeframe}: "
                f"{payload.get('schema_version')!r} (supported "
                f"{HISTORICAL_SCHEMA_VERSION}).",
            )
        try:
            candles = tuple(
                candle_from_dict(c) for c in payload.get("candles", [])
            )
        except Exception as exc:
            raise HistoricalDataIntegrityError(
                f"Malformed candle payload in {instrument}/{timeframe}: {exc}",
            ) from exc
        return candles

    def load_provenance(self, instrument: str, timeframe: str) -> tuple[str, ...]:
        """Load provenance lines (raw JSON strings). Empty when missing."""

        path = _dataset_dir(self.directory, instrument, timeframe) / "provenance.jsonl"
        if not path.exists():
            return ()
        try:
            return tuple(
                line for line in path.read_text(encoding="utf-8").splitlines() if line
            )
        except OSError as exc:
            raise HistoricalDataIntegrityError(
                f"Failed to read provenance for {instrument}/{timeframe}: {exc}",
            ) from exc

    def list_datasets(self) -> tuple[StoredDatasetInfo, ...]:
        """Summaries for every stored dataset (deterministic order)."""

        info: list[StoredDatasetInfo] = []
        if not self.directory.exists():
            return ()
        for instrument_dir in sorted(self.directory.iterdir()):
            if not instrument_dir.is_dir():
                continue
            if not _SAFE_ID_RE.match(instrument_dir.name):
                continue
            for timeframe_dir in sorted(instrument_dir.iterdir()):
                if not timeframe_dir.is_dir():
                    continue
                candles_file = timeframe_dir / "candles.json"
                if not candles_file.exists():
                    continue
                try:
                    payload = json.loads(candles_file.read_text(encoding="utf-8"))
                    candles = payload.get("candles", [])
                    first = candles[0]["timestamp"] if candles else None
                    last = candles[-1]["timestamp"] if candles else None
                except Exception:
                    first, last, candles = None, None, []
                info.append(
                    StoredDatasetInfo(
                        instrument=instrument_dir.name,
                        timeframe=timeframe_dir.name,
                        candle_count=len(candles),
                        first_timestamp=first,
                        last_timestamp=last,
                    ),
                )
        return tuple(info)

    def delete(self, instrument: str, timeframe: str) -> None:
        path = self.path_for(instrument, timeframe)
        if not path.exists():
            raise HistoricalDatasetNotFoundError(
                f"Dataset {instrument}/{timeframe} not found at {path!s}.",
            )
        dataset_dir = path.parent
        try:
            path.unlink()
            provenance = dataset_dir / "provenance.jsonl"
            if provenance.exists():
                provenance.unlink()
            # Remove the (now-empty) dataset dir, then the instrument dir
            # if empty (best-effort cleanup; never raise here).
            try:
                dataset_dir.rmdir()
                instrument_dir = dataset_dir.parent
                if instrument_dir.exists() and not any(instrument_dir.iterdir()):
                    instrument_dir.rmdir()
            except OSError:
                pass
        except OSError as exc:
            raise HistoricalDataStoreError(
                f"Failed to delete dataset {instrument}/{timeframe}: {exc}",
            ) from exc

    # ------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------

    def store(
        self,
        instrument: str,
        timeframe: str,
        candles: Iterable[OHLCVCandle],
        provenance: HistoricalProvenance | None = None,
        *,
        overwrite: bool = False,
    ) -> tuple[int, int, int, Path]:
        """
        Persist candles idempotently (merge by timestamp).

        Returns ``(added, existing, total, path)``. New timestamps are
        merged into the stored series; re-ingested timestamps are NOT
        re-stored (deterministic, no duplicates). When ``overwrite`` is
        True the stored series is replaced (used by controlled import
        scenarios only). Provenance is appended to ``provenance.jsonl``
        when supplied.
        """

        _validate_id(instrument.upper(), "instrument")
        _validate_id(
            canonical_timeframe(timeframe) or timeframe, "timeframe",
        )
        instrument = instrument.upper()
        dataset_dir = _dataset_dir(self.directory, instrument, timeframe)
        candles_path = dataset_dir / "candles.json"

        existing: dict[str, OHLCVCandle] = {}
        if candles_path.exists() and not overwrite:
            for c in self.load_candles(instrument, timeframe):
                existing[c.timestamp.isoformat()] = c

        existing_count = len(existing)
        merged: dict[str, OHLCVCandle] = dict(existing)
        added = 0
        for candle in candles:
            key = candle.timestamp.isoformat()
            if key not in merged:
                merged[key] = candle
                added += 1

        ordered = sorted(merged.values(), key=lambda c: c.timestamp)
        payload = {
            "schema_version": HISTORICAL_SCHEMA_VERSION,
            "instrument": instrument,
            "timeframe": canonical_timeframe(timeframe) or timeframe,
            "candles": [candle_to_dict(c) for c in ordered],
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._atomic_write(candles_path, text)

        if provenance is not None:
            prov_path = dataset_dir / "provenance.jsonl"
            line = serialize_provenance(provenance)
            self._atomic_append(prov_path, line + "\n")

        return added, existing_count, len(ordered), candles_path

    # ------------------------------------------------------------
    # ATOMIC WRITE HELPERS
    # ------------------------------------------------------------

    def _atomic_write(self, target: Path, text: str) -> None:
        """Write ``text`` to ``target`` atomically (same-dir temp + rename)."""

        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        prefix = target.name + "."
        fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=str(directory))
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
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise HistoricalDataStoreError(
                f"Failed to atomically write {target!s}: {exc}",
            ) from exc

    def _atomic_append(self, target: Path, line: str) -> None:
        """Append ``line`` to ``target`` (mkdir + open('a')); no temp games."""

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            raise HistoricalDataStoreError(
                f"Failed to append to {target!s}: {exc}",
            ) from exc


__all__ = [
    "HistoricalDataIntegrityError",
    "HistoricalDataStore",
    "HistoricalDataStoreError",
    "HistoricalDatasetNotFoundError",
    "StoredDatasetInfo",
    "default_historical_data_directory",
]
