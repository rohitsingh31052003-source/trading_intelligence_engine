"""
Thin research-corpus METADATA persistence (Product Phase 6C).

Why this exists (kept deliberately minimal): the corpus itself is
reproducible by rebuilding it from the Phase 6B store — the Phase 6B
``HistoricalDataStore`` remains the SINGLE candle persistence mechanism
and is NOT duplicated here. But a research run must be able to
*identify* a built corpus later — its identity, configuration snapshot,
evaluation accounting and data-quality report — WITHOUT rebuilding it.
This layer persists exactly that: a small, schema-versioned JSON
manifest per corpus id. It stores NO candles, NO historical states and
NO slices.

* One file per corpus id: ``<directory>/<corpus_id>.corpus.json``.
* Atomic write (same-dir temp + flush + fsync best-effort + os.replace);
  temp file cleaned up on any failure.
* Safe-id validation prevents path traversal on corpus ids.
* Schema version checked before any interpretation; corrupted payloads
  and filename/id mismatches fail loudly.
* No ``pickle`` / ``eval`` / ``exec``; only ``json``.
* No hard-coded absolute paths (default directory relative to cwd).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from engine.data.research_corpus_serialization import (
    RESEARCH_CORPUS_SCHEMA_VERSION,
    report_from_dict,
    report_to_dict,
)
from engine.models.research_corpus import (
    HistoricalResearchCorpus,
    ResearchCorpusReport,
)


class ResearchCorpusStoreError(Exception):
    """Base error for corpus-metadata persistence."""


class ResearchCorpusNotFoundError(ResearchCorpusStoreError):
    """A requested corpus manifest was not found."""


class ResearchCorpusIntegrityError(ResearchCorpusStoreError):
    """A persisted corpus manifest failed an integrity check."""


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


def _validate_corpus_id(corpus_id: str) -> None:
    if not corpus_id or not _SAFE_ID_RE.match(corpus_id):
        raise ResearchCorpusStoreError(
            f"Unsafe corpus id {corpus_id!r}: ids must match "
            f"{str(_SAFE_ID_RE.pattern)!r}.",
        )


def default_research_corpus_directory() -> Path:
    """Default corpus-metadata root (``./data/research_corpus``)."""

    return Path.cwd() / "data" / "research_corpus"


@dataclass(frozen=True, slots=True)
class ResearchCorpusManifest:
    """The persisted corpus metadata (identity + configuration + report)."""

    corpus_id: str
    instruments: tuple[str, ...]
    setup_timeframe: str
    context_timeframe: str
    configuration: tuple[tuple[str, str], ...]
    label: str
    metadata: tuple[tuple[str, str], ...]
    report: ResearchCorpusReport

    @property
    def valid_count(self) -> int:
        return self.report.valid_count


class ResearchCorpusStore:
    """Filesystem persistence for research-corpus manifests."""

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = default_research_corpus_directory()
        self.directory = Path(directory)

    def path_for(self, corpus_id: str) -> Path:
        _validate_corpus_id(corpus_id)
        return self.directory / f"{corpus_id}.corpus.json"

    def exists(self, corpus_id: str) -> bool:
        return self.path_for(corpus_id).exists()

    def save(
        self,
        corpus: HistoricalResearchCorpus,
        *,
        configuration: tuple[tuple[str, str], ...] = (),
        overwrite: bool = False,
    ) -> Path:
        """Persist the corpus manifest. No silent overwrite."""

        path = self.path_for(corpus.corpus_id)
        if path.exists() and not overwrite:
            raise ResearchCorpusStoreError(
                f"Corpus manifest {corpus.corpus_id!r} already exists; "
                "pass overwrite=True to replace it.",
            )
        payload = {
            "schema_version": RESEARCH_CORPUS_SCHEMA_VERSION,
            "corpus_id": corpus.corpus_id,
            "instruments": list(corpus.instruments),
            "setup_timeframe": corpus.setup_timeframe,
            "context_timeframe": corpus.context_timeframe,
            "configuration": [list(pair) for pair in configuration],
            "label": corpus.label,
            "metadata": [list(pair) for pair in corpus.metadata],
            "report": report_to_dict(corpus.report),
        }
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        self._atomic_write(path, text)
        return path

    def load(self, corpus_id: str) -> ResearchCorpusManifest:
        """Load a corpus manifest (schema + integrity checked)."""

        path = self.path_for(corpus_id)
        if not path.exists():
            raise ResearchCorpusNotFoundError(
                f"Corpus manifest {corpus_id!r} not found at {path!s}.",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchCorpusIntegrityError(
                f"Corrupted corpus manifest {corpus_id!r}: {exc}",
            ) from exc
        version = payload.get("schema_version")
        if version != RESEARCH_CORPUS_SCHEMA_VERSION:
            raise ResearchCorpusIntegrityError(
                f"Unsupported corpus schema version {version!r} in "
                f"{corpus_id!r} (supported {RESEARCH_CORPUS_SCHEMA_VERSION}).",
            )
        if payload.get("corpus_id") != corpus_id:
            raise ResearchCorpusIntegrityError(
                f"Corpus id mismatch: file {corpus_id!r} contains "
                f"{payload.get('corpus_id')!r}.",
            )
        try:
            return ResearchCorpusManifest(
                corpus_id=corpus_id,
                instruments=tuple(payload.get("instruments", ())),
                setup_timeframe=str(payload.get("setup_timeframe", "")),
                context_timeframe=str(payload.get("context_timeframe", "")),
                configuration=tuple(
                    (str(k), str(v)) for k, v in payload.get("configuration", [])
                ),
                label=str(payload.get("label", "")),
                metadata=tuple(
                    (str(k), str(v)) for k, v in payload.get("metadata", [])
                ),
                report=report_from_dict(payload["report"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchCorpusIntegrityError(
                f"Malformed corpus manifest {corpus_id!r}: {exc}",
            ) from exc

    def list_manifests(self) -> tuple[str, ...]:
        """All persisted corpus ids (deterministic order)."""

        if not self.directory.exists():
            return ()
        return tuple(
            sorted(
                path.name[: -len(".corpus.json")]
                for path in self.directory.glob("*.corpus.json")
            ),
        )

    def delete(self, corpus_id: str) -> None:
        path = self.path_for(corpus_id)
        if not path.exists():
            raise ResearchCorpusNotFoundError(
                f"Corpus manifest {corpus_id!r} not found at {path!s}.",
            )
        try:
            path.unlink()
        except OSError as exc:
            raise ResearchCorpusStoreError(
                f"Failed to delete corpus manifest {corpus_id!r}: {exc}",
            ) from exc

    def _atomic_write(self, target: Path, text: str) -> None:
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
            raise ResearchCorpusStoreError(
                f"Failed to atomically write {target!s}: {exc}",
            ) from exc


__all__ = [
    "ResearchCorpusIntegrityError",
    "ResearchCorpusManifest",
    "ResearchCorpusNotFoundError",
    "ResearchCorpusStore",
    "ResearchCorpusStoreError",
    "default_research_corpus_directory",
]
