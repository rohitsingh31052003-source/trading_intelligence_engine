"""
Experiment suite configuration (Sprint 11M).

The ``SuiteConfig`` captures every parameter necessary to reproduce a
complete experiment suite: an ordered collection of member
``ExperimentConfig`` objects plus a label and metadata. It is immutable
and carries a deterministic suite identifier derived from a canonical
representation of the configuration.

Design rules:

* Immutable frozen+slots dataclass.

* Deterministic identity.
  The same suite configuration always produces the same suite ID.
  Changing a meaningful parameter (any member config, member ORDER,
  label or metadata) changes the suite ID. Nondeterministic values
  (current timestamps, etc.) are NEVER part of the identity.

* Ordered members are significant.
  Member order is part of the identity: a re-ordered suite is a
  different suite. This is deliberate and documented so a caller cannot
  accidentally treat two differently-ordered comparison grids as the
  same suite.

* Canonical representation reuse.
  Member ``ExperimentConfig`` objects are canonicalized by reusing the
  Sprint 11J ``_canonicalize`` helper so embedded engine instances and
  stray lambdas are handled identically to experiment identity. No
  canonicalization logic is duplicated.

* No engine logic.
  This module only models and serializes configuration. It does not run
  pipelines, research or suites.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from engine.experiment.config import (
    ExperimentConfig,
    _canonicalize,
    _canonicalize_key,
    _sort_key,
)


# ============================================================
# SUITE CONFIG
# ============================================================


@dataclass(frozen=True)
class SuiteConfig:
    """
    Immutable configuration for a reproducible experiment suite.

    Field semantics:

    label
        Human-readable suite name.

    members
        Ordered tuple of member ``ExperimentConfig`` objects. Order is
        significant and part of the suite identity. A re-ordered suite
        is a different suite.

    metadata
        Arbitrary deterministic string metadata. Part of the suite
        identity.

    seed
        Optional deterministic random seed recorded in reproducibility
        metadata. ``None`` when not applicable. Not part of the member
        experiments (each member carries its own seed); it is a
        suite-level reproducibility marker only.
    """

    label: str

    members: tuple[ExperimentConfig, ...] = field(default_factory=tuple)

    metadata: Mapping[str, str] = field(default_factory=dict)

    seed: int | None = None

    # -----------------------------------------------------
    # DETERMINISTIC IDENTITY
    # -----------------------------------------------------

    @property
    def canonical_representation(self) -> str:
        """
        Stable, sorted-key string representation of the full suite
        configuration.

        Used to derive both the suite ID and the configuration hash.
        Deterministic: identical suite configurations always produce an
        identical string.
        """

        payload = _canonicalize(self)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    @property
    def configuration_hash(self) -> str:
        """SHA-256 (hex) of the canonical representation."""

        return hashlib.sha256(
            self.canonical_representation.encode("utf-8"),
        ).hexdigest()

    @property
    def suite_id(self) -> str:
        """
        Deterministic suite identifier.

        A stable prefix plus the first 16 hex characters of the SHA-256
        of the canonical representation. The same configuration always
        yields the same ID; changing any meaningful parameter (any
        member config, member order, label, metadata, seed) changes
        the ID.
        """

        digest = self.configuration_hash
        return f"suite-{digest[:16]}"

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def member_experiment_ids(self) -> tuple[str, ...]:
        """Ordered member experiment identifiers (from their configs)."""

        return tuple(m.experiment_id for m in self.members)

    @property
    def is_empty(self) -> bool:
        return not self.members


# ============================================================
# RE-EXPORT CANONICALIZATION HELPERS
# ============================================================
#
# ``_canonicalize`` / ``_canonicalize_key`` / ``_sort_key`` are reused
# from the Sprint 11J experiment config module so suite identity
# canonicalizes member configs identically to experiment identity.
# They are imported above; nothing is reimplemented here.

__all__ = [
    "SuiteConfig",
]
