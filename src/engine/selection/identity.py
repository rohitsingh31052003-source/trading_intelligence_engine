"""
Deterministic selection identity (Sprint 11N).

A selection decision carries a deterministic identifier derived from a
canonical representation of the selection identity (selection type +
criteria + label + metadata). The same identity always produces the
same selection id; changing any meaningful component changes the id.
Nondeterministic values (current timestamps, etc.) are NEVER part of
the identity.

The canonicalization reuses the Sprint 11J ``_canonicalize`` helper so
the criteria dataclass, enums and primitives are handled identically to
experiment / suite identity. No canonicalization logic is duplicated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from engine.experiment.config import _canonicalize
from engine.models.selection import SelectionCriteria, SelectionType


@dataclass(frozen=True, slots=True)
class SelectionIdentity:
    """
    The deterministic identity inputs of a selection decision.

    Field semantics:

    selection_type
        Whether the selection is among experiments or suites.

    criteria
        The explicit :class:`SelectionCriteria`.

    label
        Human-readable selection label.

    metadata
        Arbitrary deterministic string metadata.
    """

    selection_type: SelectionType
    criteria: SelectionCriteria
    label: str
    metadata: Mapping[str, str]

    @property
    def canonical_representation(self) -> str:
        """
        Stable, sorted-key string representation of the selection
        identity.

        Used to derive the selection id. Deterministic: identical
        identities always produce an identical string.
        """

        payload = _canonicalize(
            {
                "selection_type": self.selection_type,
                "criteria": self.criteria,
                "label": self.label,
                "metadata": dict(self.metadata),
            }
        )
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    @property
    def configuration_hash(self) -> str:
        """SHA-256 (hex) of the canonical representation."""

        return hashlib.sha256(
            self.canonical_representation.encode("utf-8"),
        ).hexdigest()

    @property
    def selection_id(self) -> str:
        """
        Deterministic selection identifier.

        A stable prefix plus the first 16 hex characters of the SHA-256
        of the canonical representation. The same identity always
        yields the same id; changing any meaningful component changes
        the id.
        """

        return f"sel-{self.configuration_hash[:16]}"


__all__ = ["SelectionIdentity"]
