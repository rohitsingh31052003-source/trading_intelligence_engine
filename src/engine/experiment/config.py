"""
Experiment configuration (Sprint 11J).

The ``ExperimentConfig`` captures every parameter necessary to
reproduce a complete research experiment. It is immutable and
carries a deterministic experiment identifier derived from a
canonical representation of the configuration.

Design rules:

* Immutable frozen+slots dataclass.

* Deterministic identity.
  The same configuration always produces the same experiment
  ID. Changing a meaningful parameter changes the experiment
  ID. Nondeterministic values (current timestamps, etc.) are
  NEVER part of the identity.

* Canonical representation.
  A stable, sorted-key string representation is produced from
  the full configuration tree (pipeline config, research
  config, evaluation config, strategy parameters, dataset
  identity, metadata, seed). Both the experiment ID and the
  configuration hash are derived from this representation.

* No engine logic.
  This module only models and serializes configuration. It does
  not run pipelines or research.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping

from engine.models.experiment import DatasetSpec
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.research.research import ResearchConfig


# ============================================================
# EVALUATION CONFIGURATION
# ============================================================


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """
    Immutable configuration describing which research analyses an
    experiment should run.

    This does NOT duplicate the research sub-engine configs
    (those live on ``ResearchConfig``). It only toggles which
    optional, potentially expensive analyses the experiment
    runner should perform and supplies the parameter-sweep
    surface used by sensitivity / robustness / walk-forward.

    Field semantics:

    run_out_of_sample
        Whether the runner should run the chronological OOS
        evaluation.

    run_walk_forward
        Whether the runner should run the walk-forward parameter
        selection. Requires ``parameter_name`` and
        ``parameter_values``.

    run_sensitivity
        Whether the runner should run the parameter sensitivity
        sweep. Requires ``parameter_name`` and
        ``parameter_values``.

    parameter_name
        Name of the strategy parameter being swept (e.g.
        ``"swing_lookback"``). Mirrors
        ``ResearchConfig.sensitivity_parameter_name``.

    parameter_values
        Tuple of parameter values to sweep. Mirrors
        ``ResearchConfig.sensitivity_parameter_values``.
    """

    run_out_of_sample: bool = True
    run_walk_forward: bool = True
    run_sensitivity: bool = True

    parameter_name: str | None = None
    parameter_values: tuple[Any, ...] = field(default_factory=tuple)


# ============================================================
# EXPERIMENT CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """
    Immutable configuration for a reproducible research
    experiment.

    Field semantics:

    label
        Human-readable experiment name.

    dataset
        Deterministic dataset identity (``DatasetSpec``).

    pipeline
        ``PipelineConfig`` passed straight through to the
        existing historical evaluation pipeline.

    research
        ``ResearchConfig`` passed straight through to the
        existing research engine.

    evaluation
        ``EvaluationConfig`` describing which optional analyses
        the experiment runner should perform.

    strategy_parameters
        Arbitrary deterministic string key/value mapping
        capturing user-facing strategy parameters for
        inspection and identity. These are metadata only; the
        authoritative engine parameters live on ``pipeline``
        and ``research``.

    seed
        Optional deterministic random seed. Recorded in
        reproducibility metadata. ``None`` when not applicable.

    metadata
        Arbitrary deterministic string metadata.
    """

    label: str

    dataset: DatasetSpec

    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    research: ResearchConfig = field(default_factory=ResearchConfig)

    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)

    strategy_parameters: Mapping[str, str] = field(default_factory=dict)

    seed: int | None = None

    metadata: Mapping[str, str] = field(default_factory=dict)

    # -----------------------------------------------------
    # DETERMINISTIC IDENTITY
    # -----------------------------------------------------

    @property
    def canonical_representation(self) -> str:
        """
        Stable, sorted-key string representation of the full
        configuration.

        Used to derive both the experiment ID and the
        configuration hash. Deterministic: identical
        configurations always produce an identical string.
        """

        payload = _canonicalize(self)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    @property
    def configuration_hash(self) -> str:
        """
        SHA-256 (hex) of the canonical representation.
        """

        return hashlib.sha256(
            self.canonical_representation.encode("utf-8"),
        ).hexdigest()

    @property
    def experiment_id(self) -> str:
        """
        Deterministic experiment identifier.

        A stable prefix plus the first 16 hex characters of the
        SHA-256 of the canonical representation. The same
        configuration always yields the same ID; changing any
        meaningful parameter changes the ID.
        """

        digest = self.configuration_hash
        return f"exp-{digest[:16]}"


# ============================================================
# CANONICAL SERIALIZATION
# ============================================================


def _canonicalize(value: Any) -> Any:
    """
    Recursively convert a value into a JSON-serializable,
    deterministically orderable structure.

    Handles:

    * dataclasses (frozen or mutable) -> dict keyed by field
      name.
    * Enums -> ``"Name"`` (the stable member-name identity).
    * tuples / lists / sets -> list of canonicalized items
      (sets are sorted for determinism).
    * mappings -> dict of canonicalized keys/values.
    * primitives (str/int/float/bool/None) -> as-is.

    Callable objects (evaluators, engines) are NEVER part of a
    config and are not handled; if encountered they raise so a
    nondeterministic value cannot silently enter the ID.
    """

    if value is None:
        return None

    if isinstance(value, Enum):
        return value.name

    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _canonicalize(getattr(value, f.name))
            for f in fields(value)
        }

    if isinstance(value, Mapping):
        return {
            str(_canonicalize_key(k)): _canonicalize(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]

    if isinstance(value, (set, frozenset)):
        return [
            _canonicalize(item)
            for item in sorted(value, key=_sort_key)
        ]

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")

    # Engine / dependency instances embedded in sub-configs (e.g.
    # ``SegmentationConfig.regime_engine``) are not scalar
    # parameters. They are represented by their stable
    # fully-qualified class name so swapping the engine class
    # changes the identity while identical configs stay identical.
    # Bare functions / lambdas (evaluators) are NEVER part of a
    # config and are rejected to keep the identity honest.
    cls = type(value)
    module = getattr(cls, "__module__", "")
    class_qualname = getattr(cls, "__qualname__", cls.__name__)

    # Functions / lambdas carry their OWN __qualname__ attribute
    # (``<lambda>`` / ``<locals>``). Detect those and reject them.
    own_qualname = getattr(value, "__qualname__", None)
    if callable(value) and own_qualname is not None:
        if "<lambda>" in own_qualname or "<locals>" in own_qualname:
            raise TypeError(
                "Callable configuration values (evaluators / "
                "lambdas) are not permitted in experiment identity."
            )
        # Named function: represent by module.qualname.
        own_module = getattr(value, "__module__", module)
        return (
            f"{own_module}.{own_qualname}"
            if own_module
            else own_qualname
        )

    qualname = class_qualname
    if "<lambda>" in qualname or "<locals>" in qualname:
        raise TypeError(
            f"Cannot canonicalize value of type "
            f"{type(value).__name__!r} for experiment identity; "
            f"only deterministic, serializable configuration "
            f"values are permitted."
        )

    return f"{module}.{qualname}" if module else qualname


def _canonicalize_key(key: Any) -> str:
    """
    Canonicalize a mapping key into a stable string.
    """

    if isinstance(key, Enum):
        return key.name

    return str(key)


def _sort_key(value: Any) -> Any:
    """
    Sort key for set canonicalization.

    Falls back to the string representation for non-directly
    comparable types so heterogeneous sets do not raise.
    """

    if isinstance(value, (str, int, float, bool)):
        return (0, value)

    return (1, str(value))
