"""
Deterministic serialization for the research corpus metadata
(Product Phase 6C).

Serialization scope (intentional): ONLY the lightweight corpus METADATA
— the corpus identity, the reused configuration snapshot and the
:class:`ResearchCorpusReport` data-quality / coverage accounting — is
persisted. The heavy per-point historical market states and candle
slices are NOT persisted: they are regenerable deterministically by
rebuilding the corpus from the Phase 6B store (the single data
persistence mechanism; no competing candle storage is introduced).

Self-describing canonical JSON: sorted keys, stable value encoding,
schema version checked before any model reconstruction, no ``pickle`` /
``eval`` / ``exec``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from engine.data.historical_serialization import serialize_gap, serialize_issue
from engine.models.historical_data import (
    GapKind,
    HistoricalDataError,
    HistoricalDataIssue,
    HistoricalGap,
)
from engine.models.research_corpus import (
    CorpusBuildIssue,
    CorpusDataQuality,
    ResearchCorpusReport,
)


#: Schema version for persisted corpus-metadata documents.
RESEARCH_CORPUS_SCHEMA_VERSION = 1


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value))


def _gap_from_dict(payload: dict[str, Any]) -> HistoricalGap:
    return HistoricalGap(
        kind=GapKind[payload["kind"]],
        previous_timestamp=datetime.fromisoformat(payload["previous_timestamp"]),
        next_timestamp=datetime.fromisoformat(payload["next_timestamp"]),
        missing_count=int(payload["missing_count"]),
        span_seconds=float(payload["span_seconds"]),
        reason=str(payload.get("reason", "")),
    )


def _issue_from_dict(payload: dict[str, Any]) -> HistoricalDataIssue:
    return HistoricalDataIssue(
        error=HistoricalDataError[payload["error"]],
        reason=str(payload.get("reason", "")),
        instrument=str(payload.get("instrument", "")),
        timeframe=str(payload.get("timeframe", "")),
        timestamp=_parse_dt(payload.get("timestamp")),
    )


def quality_to_dict(quality: CorpusDataQuality) -> dict[str, Any]:
    """Serialize one :class:`CorpusDataQuality` to a JSON mapping."""

    return {
        "source_count": quality.source_count,
        "window_count": quality.window_count,
        "first_timestamp": (
            quality.first_timestamp.isoformat() if quality.first_timestamp else None
        ),
        "last_timestamp": (
            quality.last_timestamp.isoformat() if quality.last_timestamp else None
        ),
        "unexpected_gap_count": quality.unexpected_gap_count,
        "closure_gap_count": quality.closure_gap_count,
        "invalid_records": quality.invalid_records,
        "gaps": [serialize_gap(g) for g in quality.gaps],
        "issues": [serialize_issue(i) for i in quality.issues],
    }


def quality_from_dict(payload: dict[str, Any]) -> CorpusDataQuality:
    """Reconstruct one :class:`CorpusDataQuality` from a JSON mapping."""

    return CorpusDataQuality(
        source_count=int(payload["source_count"]),
        window_count=int(payload["window_count"]),
        first_timestamp=_parse_dt(payload.get("first_timestamp")),
        last_timestamp=_parse_dt(payload.get("last_timestamp")),
        unexpected_gap_count=int(payload["unexpected_gap_count"]),
        closure_gap_count=int(payload["closure_gap_count"]),
        invalid_records=int(payload["invalid_records"]),
        gaps=tuple(_gap_from_dict(g) for g in payload.get("gaps", [])),
        issues=tuple(_issue_from_dict(i) for i in payload.get("issues", [])),
    )


def build_issue_to_dict(issue: CorpusBuildIssue) -> dict[str, Any]:
    return {
        "instrument": issue.instrument,
        "timeframe": issue.timeframe,
        "reason": issue.reason,
        "error": issue.error,
    }


def build_issue_from_dict(payload: dict[str, Any]) -> CorpusBuildIssue:
    return CorpusBuildIssue(
        instrument=str(payload.get("instrument", "")),
        timeframe=str(payload.get("timeframe", "")),
        reason=str(payload.get("reason", "")),
        error=str(payload.get("error", "")),
    )


def report_to_dict(report: ResearchCorpusReport) -> dict[str, Any]:
    """Serialize a :class:`ResearchCorpusReport` to a JSON mapping."""

    return {
        "requested_instruments": list(report.requested_instruments),
        "loaded_instruments": list(report.loaded_instruments),
        "missing_instruments": list(report.missing_instruments),
        "requested_timeframes": list(report.requested_timeframes),
        "available_timeframes": list(report.available_timeframes),
        "per_instrument_quality": [
            {"instrument": name, "quality": quality_to_dict(quality)}
            for name, quality in report.per_instrument_quality
        ],
        "evaluation_count": report.evaluation_count,
        "valid_count": report.valid_count,
        "insufficient_history_count": report.insufficient_history_count,
        "missing_data_count": report.missing_data_count,
        "data_gap_count": report.data_gap_count,
        "invalid_count": report.invalid_count,
        "rejected_future_records": report.rejected_future_records,
        "provider": report.provider,
        "storage_status": report.storage_status,
        "ingestion_version": report.ingestion_version,
        "issues": [build_issue_to_dict(i) for i in report.issues],
    }


def report_from_dict(payload: dict[str, Any]) -> ResearchCorpusReport:
    """Reconstruct a :class:`ResearchCorpusReport` from a JSON mapping."""

    return ResearchCorpusReport(
        requested_instruments=tuple(payload.get("requested_instruments", ())),
        loaded_instruments=tuple(payload.get("loaded_instruments", ())),
        missing_instruments=tuple(payload.get("missing_instruments", ())),
        requested_timeframes=tuple(payload.get("requested_timeframes", ())),
        available_timeframes=tuple(payload.get("available_timeframes", ())),
        per_instrument_quality=tuple(
            (str(entry["instrument"]), quality_from_dict(entry["quality"]))
            for entry in payload.get("per_instrument_quality", [])
        ),
        evaluation_count=int(payload["evaluation_count"]),
        valid_count=int(payload["valid_count"]),
        insufficient_history_count=int(payload["insufficient_history_count"]),
        missing_data_count=int(payload["missing_data_count"]),
        data_gap_count=int(payload["data_gap_count"]),
        invalid_count=int(payload["invalid_count"]),
        rejected_future_records=int(payload["rejected_future_records"]),
        provider=str(payload.get("provider", "")),
        storage_status=str(payload.get("storage_status", "")),
        ingestion_version=str(payload.get("ingestion_version", "")),
        issues=tuple(
            build_issue_from_dict(i) for i in payload.get("issues", [])
        ),
    )


def serialize_report(report: ResearchCorpusReport) -> str:
    """Serialize a :class:`ResearchCorpusReport` to canonical JSON text."""

    payload = {
        "schema_version": RESEARCH_CORPUS_SCHEMA_VERSION,
        "report": report_to_dict(report),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def deserialize_report(payload: str) -> ResearchCorpusReport:
    """Reconstruct a :class:`ResearchCorpusReport` from JSON text."""

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed corpus report payload: {exc}") from exc
    if not isinstance(parsed, dict) or "report" not in parsed:
        raise ValueError("Corpus report payload must be a JSON object.")
    version = parsed.get("schema_version")
    if version != RESEARCH_CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported corpus schema version {version!r}; supported is "
            f"{RESEARCH_CORPUS_SCHEMA_VERSION}.",
        )
    return report_from_dict(parsed["report"])


def canonical_report_json(report: ResearchCorpusReport) -> str:
    """Canonical (sorted-key) JSON text for a corpus report."""

    return serialize_report(report)


def parse_corpus_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse (inspect schema_version before loading)."""

    return json.loads(payload)


__all__ = [
    "RESEARCH_CORPUS_SCHEMA_VERSION",
    "build_issue_from_dict",
    "build_issue_to_dict",
    "canonical_report_json",
    "deserialize_report",
    "parse_corpus_header",
    "quality_from_dict",
    "quality_to_dict",
    "report_from_dict",
    "report_to_dict",
    "serialize_report",
]
