"""
Filesystem persistence for historical setup research results
(Product Phase 6D).

Follows the Phase 6C persistence architecture (manifest + serialized
files; atomic same-dir temp + ``os.replace``; safe-id validation
against path traversal; schema version checked BEFORE model
reconstruction). One JSON file per research result
(``<directory>/<research_id>.research.json``). No unrelated database
system; stdlib only. No ``pickle`` / ``eval`` / ``exec``.

Research results are reproducible from ``corpus + research request +
research configuration``; persistence is a convenience for audit and
reuse, never a second source of intelligence.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from engine.data.setup_research_serialization import (
    SETUP_RESEARCH_SCHEMA_VERSION,
    deserialize_result,
    parse_result_header,
    serialize_result,
)
from engine.models.setup_research import SetupResearchResult


class SetupResearchStoreError(Exception):
    """Base error for setup research persistence."""


class SetupResearchNotFoundError(SetupResearchStoreError):
    """A requested research result was not found."""


class SetupResearchIntegrityError(SetupResearchStoreError):
    """A persisted research result failed an integrity check."""


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


def _validate_research_id(research_id: str) -> None:
    if not research_id or not _SAFE_ID_RE.match(research_id):
        raise SetupResearchStoreError(
            f"Unsafe research id {research_id!r}: ids must match "
            f"{str(_SAFE_ID_RE.pattern)!r}.",
        )


def default_setup_research_directory() -> Path:
    """Default research root (``./data/setup_research``)."""

    return Path.cwd() / "data" / "setup_research"


class SetupResearchStore:
    """Filesystem persistence for historical setup research results."""

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = default_setup_research_directory()
        self.directory = Path(directory)

    def path_for(self, research_id: str) -> Path:
        _validate_research_id(research_id)
        return self.directory / f"{research_id}.research.json"

    def exists(self, research_id: str) -> bool:
        return self.path_for(research_id).exists()

    def save(
        self,
        result: SetupResearchResult,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Persist a research result. No silent overwrite."""

        path = self.path_for(result.research_id)
        if path.exists() and not overwrite:
            raise SetupResearchStoreError(
                f"Research result {result.research_id!r} already exists; "
                "pass overwrite=True to replace it.",
            )
        self._atomic_write(path, serialize_result(result))
        return path

    def load(self, research_id: str) -> SetupResearchResult:
        """Load a research result (schema + integrity checked)."""

        path = self.path_for(research_id)
        if not path.exists():
            raise SetupResearchNotFoundError(
                f"Research result {research_id!r} not found at {path!s}.",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SetupResearchIntegrityError(
                f"Corrupted research result {research_id!r}: {exc}",
            ) from exc
        try:
            header = parse_result_header(text)
            if header.get("schema_version") != SETUP_RESEARCH_SCHEMA_VERSION:
                raise SetupResearchIntegrityError(
                    f"Unsupported research schema version "
                    f"{header.get('schema_version')!r} in {research_id!r} "
                    f"(supported {SETUP_RESEARCH_SCHEMA_VERSION}).",
                )
            if header.get("research_id") != research_id:
                raise SetupResearchIntegrityError(
                    f"Research id mismatch: file {research_id!r} contains "
                    f"{header.get('research_id')!r}.",
                )
            return deserialize_result(text)
        except SetupResearchIntegrityError:
            raise
        except ValueError as exc:
            raise SetupResearchIntegrityError(
                f"Malformed research result {research_id!r}: {exc}",
            ) from exc

    def list_results(self) -> tuple[str, ...]:
        """All persisted research ids (deterministic order)."""

        if not self.directory.exists():
            return ()
        return tuple(
            sorted(
                path.name[: -len(".research.json")]
                for path in self.directory.glob("*.research.json")
            ),
        )

    def delete(self, research_id: str) -> None:
        path = self.path_for(research_id)
        if not path.exists():
            raise SetupResearchNotFoundError(
                f"Research result {research_id!r} not found at {path!s}.",
            )
        try:
            path.unlink()
        except OSError as exc:
            raise SetupResearchStoreError(
                f"Failed to delete research result {research_id!r}: {exc}",
            ) from exc

    def _atomic_write(self, target: Path, text: str) -> None:
        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        prefix = target.name + "."
        fd, tmp_name = tempfile.mkstemp(
            prefix=prefix, suffix=".tmp", dir=str(directory),
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
        except BaseException:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise


__all__ = [
    "SetupResearchIntegrityError",
    "SetupResearchNotFoundError",
    "SetupResearchStore",
    "SetupResearchStoreError",
    "default_setup_research_directory",
]
