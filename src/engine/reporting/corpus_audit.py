"""
Read-only historical corpus integrity audit reporting (Checkpoint 3B).

Stateless, deterministic formatter for a :class:`CorpusAuditReport`.
Returns ``str`` (no ``print()`` inside). The report is DESCRIPTIVE
ONLY: it reports the PERSISTED corpus condition; it is NOT a
prediction, NOT a trading recommendation, and it NEVER modifies any
stored data.

The report deliberately distinguishes:

* CORPUS COMPLETENESS (the planner-derived coverage classification of
  each dataset over the requested window) from
* DATA-QUALITY ANOMALIES (independent per-candle checks of the
  persisted rows).

A dataset can therefore be COMPLETE in coverage while still carrying an
invalid persisted candle — the audit surfaces both and leaves the final
verdict to the operator (and to ``CorpusAuditReport.verdict``).
"""

from __future__ import annotations

from engine.models.corpus_audit import (
    AuditCheckStatus,
    CorpusAuditReport,
    DatasetAuditResult,
)


def _first(last: str | None, fallback: str = "unavailable") -> str:
    return last if last else fallback


class CorpusAuditFormatter:
    """
    Renders a :class:`CorpusAuditReport` as a concise operator report.

    ``width`` controls text wrapping of long notes (default 72). Pure
    formatting; deterministic for the same report.
    """

    def __init__(self, width: int = 72) -> None:
        if width < 1:
            raise ValueError("width must be >= 1.")
        self.width = width

    # ------------------------------------------------------------
    # CHECK LINE HELPERS
    # ------------------------------------------------------------

    @staticmethod
    def _status(label: str, status: AuditCheckStatus, extra: str = "") -> str:
        suffix = f" ({extra})" if extra else ""
        return f"  {label:<14}: {status.value}{suffix}"

    @classmethod
    def _dataset_check_lines(cls, result: DatasetAuditResult) -> list[str]:
        lines = [f"{result.instrument} / {result.timeframe}"]

        if result.load_error:
            lines.append(
                f"  Load error      : {result.load_error}",
            )

        dup_extra = (
            "0" if result.duplicates is AuditCheckStatus.PASS else
            str(result.duplicate_count)
        )
        lines.append(
            cls._status("Chronology", result.chronological),
        )
        lines.append(
            cls._status("Duplicates", result.duplicates, dup_extra),
        )
        lines.append(
            cls._status("Timezone", result.timezone_aware),
        )
        ohlc_extra = (
            "0"
            if result.ohlc is AuditCheckStatus.PASS
            else str(result.ohlc_invalid_count)
        )
        lines.append(cls._status("OHLC", result.ohlc, ohlc_extra))
        volume_extra = (
            "0"
            if result.volume is AuditCheckStatus.PASS
            else str(result.volume_invalid_count)
        )
        lines.append(cls._status("Volume", result.volume, volume_extra))
        if result.gap_unexpected_count == 0 and result.gap_closure_count == 0:
            gaps_text = "PASS (none)"
        else:
            gaps_text = (
                f"REVIEW ({result.gap_closure_count} possible closure, "
                f"{result.gap_unexpected_count} unexpected)"
            )
        lines.append(f"  {'Gaps':<14}: {gaps_text}")
        lines.append(f"  {'Coverage':<14}: {result.coverage_label}")

        if result.duplicates is AuditCheckStatus.FAIL:
            lines.append(
                f"  Duplicate count : {result.duplicate_count}",
            )
        if (
            result.timezone_aware is AuditCheckStatus.FAIL
            and result.timezone_issues
        ):
            lines.append(
                "  First naive     : " + ", ".join(result.timezone_issues),
            )
        if result.ohlc is AuditCheckStatus.FAIL:
            lines.append(
                "  Invalid OHLC    : "
                + (", ".join(result.ohlc_issue_timestamps) or "unavailable"),
            )
        if result.volume is AuditCheckStatus.FAIL:
            lines.append(
                "  Invalid volume  : "
                + (
                    ", ".join(result.volume_issue_timestamps)
                    or "unavailable"
                ),
            )
        if result.gap_unexpected_count:
            samples = [
                f"{g.previous_timestamp.isoformat()} -> "
                f"{g.next_timestamp.isoformat()}"
                for g in result.gaps
                if g.kind.name == "UNEXPECTED_GAP"
            ][:3]
            lines.append(
                "  Unexpected gaps : " + ("; ".join(samples) or "unavailable"),
            )
        return lines

    # ------------------------------------------------------------
    # PUBLIC FORMATTERS
    # ------------------------------------------------------------

    def format(self, report: CorpusAuditReport) -> str:
        """A full read-only corpus integrity audit report."""

        lines = [
            "HISTORICAL CORPUS INTEGRITY AUDIT",
            "=================================",
            "",
            f"Audit id    : {report.audit_id}",
            f"Instruments : {', '.join(report.instruments)}",
            f"Timeframes  : {', '.join(report.timeframes)}",
            f"Window      : {report.start.isoformat()} -> "
            f"{report.end.isoformat()}",
            f"Label       : {report.label or 'unavailable'}",
            "",
            "DATASET SUMMARY",
            "",
            (_fmt_dataset_header()),
        ]
        for result in report.results:
            lines.append(_fmt_dataset_row(result))
        lines.append(_fmt_dataset_separator())

        lines.extend(["", "INTEGRITY CHECKS", ""])
        for result in report.results:
            lines.extend(self._dataset_check_lines(result))
            lines.append("")

        lines.extend(["KNOWN ANOMALY", ""])
        lines.extend(
            self._format_known_anomaly(report),
        )
        lines.extend(["", "FINAL RESULT", ""])
        lines.extend(self._format_final(report))
        return "\n".join(lines)

    def format_jsonable(self, report: CorpusAuditReport) -> dict:
        """Deterministic JSON projection of an audit report."""

        return {
            "audit_id": report.audit_id,
            "window_start": report.start.isoformat(),
            "window_end": report.end.isoformat(),
            "rows": [
                {
                    "instrument": r.instrument,
                    "timeframe": r.timeframe,
                    "status": r.status.value,
                    "candle_count": r.candle_count,
                    "first_timestamp": (
                        r.first_timestamp.isoformat()
                        if r.first_timestamp
                        else None
                    ),
                    "last_timestamp": (
                        r.last_timestamp.isoformat() if r.last_timestamp else None
                    ),
                    "checks": {
                        "chronology": r.chronological.value,
                        "duplicates": r.duplicates.value,
                        "duplicate_count": r.duplicate_count,
                        "timezone_aware": r.timezone_aware.value,
                        "ohlc": r.ohlc.value,
                        "ohlc_invalid_count": r.ohlc_invalid_count,
                        "volume": r.volume.value,
                        "volume_invalid_count": r.volume_invalid_count,
                        "gap_closure": r.gap_closure_count,
                        "gap_unexpected": r.gap_unexpected_count,
                    },
                    "load_error": r.load_error,
                }
                for r in report.results
            ],
        }

    def _format_known_anomaly(self, report: CorpusAuditReport) -> list[str]:
        """
        Explicitly report the KNOWN HDFCBANK/15m June-2024 provider
        anomaly and confront it with the ACTUAL persisted-data state.
        """

        matched: list[DatasetAuditResult] = [
            r
            for r in report.results
            if r.instrument == "HDFCBANK" and r.timeframe == "15m"
        ]
        anomaly_lines = [
            "HDFCBANK / 15m / June 2024",
            "  Provider anomaly previously observed : YES",
            (
                "  Message                              : 'Open price "
                "must lie between low and high.'"
            ),
        ]
        if not matched:
            anomaly_lines.append(
                "  Corpus coverage                      : "
                "HDFCBANK/15m not audited (outside the audit window).",
            )
            anomaly_lines.append(
                "  Invalid persisted OHLC rows          : not checked.",
            )
            return anomaly_lines

        result = matched[0]
        ohlc_text = _first(
            (
                str(result.ohlc_invalid_count)
                if result.ohlc_invalid_count
                else ""
            ),
        )
        if result.ohlc is AuditCheckStatus.PASS:
            ohlc_text = "0"
        if result.ohlc is AuditCheckStatus.N_A:
            ohlc_text = "not applicable (no stored candles)"
        anomaly_lines.append(
            f"  Invalid persisted OHLC rows          : {ohlc_text}",
        )
        if result.ohlc_issue_timestamps:
            anomaly_lines.append(
                "  Offending timestamps                : "
                + ", ".join(result.ohlc_issue_timestamps),
            )
        anomaly_lines.append(
            f"  Corpus coverage                      : {result.status.value}",
        )
        if result.coverage is not None:
            anomaly_lines.append(
                f"  Stored candles                       : "
                f"{result.candle_count} "
                f"(range {_first(result.coverage.stored_first)} .. "
                f"{_first(result.coverage.stored_last)})",
            )
        anomaly_lines.append(
            "  Assessment                           : " + self._anomaly_note(result),
        )
        return anomaly_lines

    @staticmethod
    def _anomaly_note(result: DatasetAuditResult) -> str:
        if result.load_error:
            return (
                "the persisted dataset could not be read; the stored file "
                "must be inspected separately (no repair is performed by "
                "this audit)."
            )
        if result.ohlc_invalid_count == 0:
            if result.candle_count > 0:
                return (
                    f"the persisted dataset currently contains "
                    f"{result.candle_count} candles and NO invalid OHLC "
                    "rows; the earlier provider anomaly left no corrupt "
                    "row in the stored corpus (the failed ingestion "
                    "attempt was not persisted)."
                )
            return (
                "no stored candles to inspect; the earlier ingestion "
                "attempt persisted no rows."
            )
        return (
            f"the persisted dataset currently contains "
            f"{result.ohlc_invalid_count} invalid OHLC row(s); review is "
            "required (rows are reported, never repaired by this audit)."
        )

    def _format_final(self, report: CorpusAuditReport) -> list[str]:
        lines = [
            f"Datasets audited  : {report.dataset_count}",
            f"Complete          : {report.complete_count}",
            f"Partial           : {report.partial_count}",
            f"Missing           : {report.missing_count}",
            f"Empty             : {report.empty_count}",
            f"Unavailable       : {report.unavailable_count}",
            f"Integrity failures: {report.integrity_failures}",
            "",
            f"Corpus audit: {report.verdict}",
            "",
        ]
        if report.is_pass:
            lines.append(
                "All datasets are COMPLETE for the planned window and every "
                "persisted candle passed every data-quality check.",
            )
        else:
            if not report.complete_count == report.dataset_count:
                lines.append(
                    "Completeness: at least one dataset does not fully cover "
                    "the planned window (see DATASET SUMMARY).",
                )
            if report.integrity_failures:
                lines.append(
                    "Data quality : at least one persisted dataset carries "
                    "an invalid candle / ordering / timezone / duplicate "
                    "problem (see INTEGRITY CHECKS). Rows are reported, "
                    "never repaired.",
                )
            if not report.is_pass:
                lines.append(
                    "A dataset can be COMPLETE in coverage while still "
                    "requiring review because of an invalid persisted "
                    "candle — completeness and data-quality anomalies are "
                    "reported independently.",
                )
        lines.extend(
            [
                "",
                "This audit is READ-ONLY: it inspected the persisted "
                "corpus only. It makes no prediction, issues no trading "
                "recommendation and never modified any stored data.",
            ],
        )
        return lines


# ------------------------------------------------------------
# DATASET SUMMARY TABLE (fixed-width helpers)
# ------------------------------------------------------------


def _fmt_dataset_header() -> str:
    return (
        f"{'Instrument':<12} {'TF':<6} {'Status':<10} "
        f"{'Candles':>8}   {'First Candle':<27} {'Last Candle':<27}"
    )


def _fmt_dataset_row(result: DatasetAuditResult) -> str:
    status = result.coverage_label
    first = _first(
        result.first_timestamp.isoformat()
        if result.first_timestamp
        else None,
    )
    last = _first(
        result.last_timestamp.isoformat()
        if result.last_timestamp
        else None,
    )
    return (
        f"{result.instrument:<12} {result.timeframe:<6} {status:<10} "
        f"{result.candle_count:>8}   {first:<27} {last:<27}"
    )


def _fmt_dataset_separator() -> str:
    return "-" * 90


__all__ = ["CorpusAuditFormatter"]