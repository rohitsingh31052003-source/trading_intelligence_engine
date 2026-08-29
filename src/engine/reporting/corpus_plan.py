"""
Corpus-preparation plan reporting (Checkpoint 3B).

Stateless, deterministic formatter for the corpus-preparation plan.
Returns ``str`` (no ``print()`` inside). The formatter renders the
plan's request accounting, per-dataset coverage and the exact missing
chunk keys — the operator action list. It is DESCRIPTIVE ONLY: it
names the data that still has to be fetched; it is NOT a prediction,
NOT a trading recommendation and NEVER triggers ingestion by itself.
"""

from __future__ import annotations

from engine.models.corpus_plan import (
    CorpusPreparationPlan,
    DatasetCoverage,
    DatasetCoverageStatus,
)


class CorpusPreparationFormatter:
    """
    Renders a :class:`CorpusPreparationPlan` as a human-readable report.

    ``width`` controls text wrapping of the reason/detail columns
    (default 72). Pure formatting; deterministic for the same plan.
    """

    def __init__(self, width: int = 72) -> None:
        if width < 1:
            raise ValueError("width must be >= 1.")
        self.width = width

    def format(self, plan: CorpusPreparationPlan) -> str:
        """A full corpus-preparation report for a plan."""

        summary = {
            "datasets": plan.dataset_count,
            "datasets_complete": plan.complete_count,
            "datasets_partial": plan.partial_count,
            "datasets_missing": plan.missing_count,
            "datasets_empty": plan.empty_count,
            "datasets_unavailable": plan.unavailable_count,
            "requests_required": plan.required_request_count,
            "requests_covered": plan.covered_request_count,
            "requests_missing": plan.missing_request_count,
            "rows_unsupported": plan.unsupported_count,
        }
        lines = [
            "CORPUS PREPARATION PLAN",
            "",
            f"Plan id         : {plan.plan_id}",
            f"Instruments     : {', '.join(plan.instruments)}",
            f"Timeframes      : {', '.join(plan.timeframes)}",
            f"Provider        : {plan.provider}",
            f"Window          : {plan.start.isoformat()} -> "
            f"{plan.end.isoformat()}",
            f"Label           : {plan.label or 'unavailable'}",
            "",
            "COVERAGE SUMMARY",
            (
                f"  Datasets           : {summary['datasets']} "
                f"(complete {summary['datasets_complete']} / partial "
                f"{summary['datasets_partial']} / missing "
                f"{summary['datasets_missing']} / empty "
                f"{summary['datasets_empty']} / unavailable "
                f"{summary['datasets_unavailable']})"
            ),
            (
                f"  Chunk requests     : {summary['requests_required']} "
                f"required, {summary['requests_covered']} covered, "
                f"{summary['requests_missing']} MISSING"
            ),
            (
                f"  Unsupported rows   : {summary['rows_unsupported']}"
            ),
            "",
            "DATASET COVERAGE",
        ]
        for row in plan.rows:
            coverage = row.coverage
            status = (
                coverage.status.value if coverage is not None
                else DatasetCoverageStatus.UNAVAILABLE.value
            )
            supported = "supported" if row.provider_supported else "UNSUPPORTED"
            lines.append(
                f"  {row.instrument:<12} {row.timeframe:<6} "
                f"[{status:<10}] ({supported})",
            )
            if coverage is not None:
                lines.append(
                    f"      stored={coverage.stored_count} "
                    f"chunks={coverage.covered_chunks}/"
                    f"{coverage.required_chunks}",
                )
                if coverage.stored_first:
                    lines.append(
                        f"      range={coverage.stored_first} .. "
                        f"{coverage.stored_last}",
                    )
        lines.extend(["", "MISSING CHUNK REQUESTS"])
        missing_rows = [
            (row, row.coverage)
            for row in plan.rows
            if row.coverage is not None
            and row.coverage.status
            in (DatasetCoverageStatus.MISSING, DatasetCoverageStatus.EMPTY,
                DatasetCoverageStatus.PARTIAL)
            and row.provider_supported
        ]
        if not missing_rows:
            lines.append("  (none)")
        for row, coverage in missing_rows:
            for key in coverage.missing_chunk_keys:
                lines.append(
                    f"  {row.instrument:<12} {row.timeframe:<6} {key}",
                )
        lines.extend(
            [
                "",
                (
                    "The plan names the missing requests only; fetching "
                    "must be performed explicitly via the existing "
                    "ingest_historical_data.py CLI. This planning report "
                    "is descriptive research preparation and is NOT a "
                    "prediction, recommendation or trade signal."
                ),
            ],
        )
        return "\n".join(lines)

    def format_missing_request_keys(
        self,
        plan: CorpusPreparationPlan,
    ) -> tuple[tuple[str, str, str], ...]:
        """
        The exact missing chunk-request list as
        ``(instrument, timeframe, chunk_key)`` tuples.

        Deterministic (sorted by instrument, then timeframe, then chunk
        key). Only provider-supported rows contribute.
        """

        result: list[tuple[str, str, str]] = []
        for row in plan.rows:
            if not row.provider_supported:
                continue
            coverage: DatasetCoverage | None = row.coverage
            if coverage is None:
                continue
            if coverage.status not in (
                DatasetCoverageStatus.MISSING,
                DatasetCoverageStatus.EMPTY,
                DatasetCoverageStatus.PARTIAL,
                DatasetCoverageStatus.UNAVAILABLE,
            ):
                continue
            result.extend(
                (row.instrument, row.timeframe, key)
                for key in coverage.missing_chunk_keys
            )
        return tuple(
            sorted(result, key=lambda item: (item[0], item[1], item[2])),
        )


__all__ = ["CorpusPreparationFormatter"]