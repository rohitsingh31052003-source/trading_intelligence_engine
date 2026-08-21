"""
Configuration for historical setup research (Product Phase 6D).

Frozen + slots, validated at construction, MINIMAL — NO strategy /
optimization / scoring parameters. The research configuration carries
ONLY research-infrastructure settings: which reused Sprint 11Q
classifications count as a historical setup occurrence, which
dimensions the descriptive evidence is grouped by, and the documented
evidence-strength sample gates (mirroring the Sprint 11Y semantics —
sample size is a HARD GATE). The existing decision / candidate /
outcome semantics are intentionally NOT part of this configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from engine.models.setup_confluence import SetupClassification


@dataclass(frozen=True, slots=True)
class SetupResearchConfig:
    """
    Historical setup research configuration.

    Attributes:

    occurrence_classifications
        The reused Sprint 11Q
        :class:`~engine.models.setup_confluence.SetupClassification`
        member names that count as a historical setup occurrence.
        Default ``("POTENTIAL_SETUP",)`` — a WATCH is a weaker
        detection and is NOT an occurrence by default (configurable).
        The existing setup semantics are never redefined here.

    group_dimensions
        The descriptive evidence grouping dimensions. Supported
        values: ``SETUP_TYPE`` / ``DIRECTION`` / ``TREND`` /
        ``RANGE_STATE`` / ``MTF_ALIGNMENT``. Grouping is descriptive
        only; it never filters, ranks or re-scores occurrences.

    min_sample_total / min_resolved / min_valid_r
        Evidence-strength sample gates (mirror the Sprint 11Y
        semantics). A group whose total sample is below
        ``min_sample_total`` is ALWAYS INSUFFICIENT, regardless of how
        favourable the observed result is. Otherwise a group below
        ``min_resolved`` resolved outcomes or ``min_valid_r`` valid-R
        observations is WEAK.

    strong_min_sample / strong_min_resolved / strong_min_valid_r
        The STRONG gates (each ``>=`` the corresponding minimum gate).
        Even STRONG evidence is DESCRIPTIVE and does NOT guarantee
        future performance.

    label / metadata
        Descriptive run identity.
    """

    occurrence_classifications: tuple[str, ...] = ("POTENTIAL_SETUP",)
    group_dimensions: tuple[str, ...] = (
        "SETUP_TYPE",
        "DIRECTION",
        "TREND",
        "MTF_ALIGNMENT",
    )
    min_sample_total: int = 5
    min_resolved: int = 3
    min_valid_r: int = 3
    strong_min_sample: int = 20
    strong_min_resolved: int = 10
    strong_min_valid_r: int = 10
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    #: Supported descriptive grouping dimensions.
    GROUP_DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "SETUP_TYPE",
        "DIRECTION",
        "TREND",
        "RANGE_STATE",
        "MTF_ALIGNMENT",
    )

    def __post_init__(self) -> None:
        valid_classifications = {member.name for member in SetupClassification}
        classifications = tuple(str(c).strip().upper() for c in self.occurrence_classifications)
        if not classifications:
            raise ValueError("occurrence_classifications must be non-empty.")
        for name in classifications:
            if name not in valid_classifications:
                raise ValueError(
                    f"occurrence classification {name!r} is not a reused "
                    "SetupClassification member.",
                )
        object.__setattr__(self, "occurrence_classifications", classifications)

        dimensions = tuple(str(d).strip().upper() for d in self.group_dimensions)
        for name in dimensions:
            if name not in self.GROUP_DIMENSIONS:
                raise ValueError(
                    f"group dimension {name!r} is not supported "
                    f"(supported: {self.GROUP_DIMENSIONS!r}).",
                )
        object.__setattr__(self, "group_dimensions", dimensions)

        for name in (
            "min_sample_total",
            "min_resolved",
            "min_valid_r",
            "strong_min_sample",
            "strong_min_resolved",
            "strong_min_valid_r",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if self.min_sample_total < 1:
            raise ValueError("min_sample_total must be >= 1.")
        if self.strong_min_sample < self.min_sample_total:
            raise ValueError("strong_min_sample must be >= min_sample_total.")
        if self.strong_min_resolved < self.min_resolved:
            raise ValueError("strong_min_resolved must be >= min_resolved.")
        if self.strong_min_valid_r < self.min_valid_r:
            raise ValueError("strong_min_valid_r must be >= min_valid_r.")

        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Sorted, auditable configuration snapshot for reporting."""

        return tuple(
            sorted(
                (
                    (
                        "occurrence_classifications",
                        ",".join(self.occurrence_classifications),
                    ),
                    ("group_dimensions", ",".join(self.group_dimensions)),
                    ("min_sample_total", str(self.min_sample_total)),
                    ("min_resolved", str(self.min_resolved)),
                    ("min_valid_r", str(self.min_valid_r)),
                    ("strong_min_sample", str(self.strong_min_sample)),
                    ("strong_min_resolved", str(self.strong_min_resolved)),
                    ("strong_min_valid_r", str(self.strong_min_valid_r)),
                    ("label", self.label),
                ),
            ),
        )


__all__ = ["SetupResearchConfig"]
