# Checkpoint 16.4 — Execution Command Persistence & Lifecycle Boundary Audit

## 1. Overall Classification

**PASS WITH LIMITATIONS**

ExecutionCommand requires persistence, but the implementation can be deferred until the first execution consumer appears. The lifecycle boundary is clean (NOT_CREATED → CREATED). Persistence belongs in a new `ExecutionCommandStore` in `src/engine/persistence/`, following the established atomic JSON pattern. Introduction does not weaken any frozen authorization/execution boundaries.

## 2. Current State Audit

### 2.1 ExecutionCommand Model Status (Checkpoint 16.2)

- **Model**: `src/engine/models/execution_command.py` — frozen+slots dataclass, 555 lines.
- **Identity**: `command_id = "cmd-" + sha256[:16]` derived from canonical binding + economic content.
- **Factory**: `create_execution_command()` — pure, deterministic, fail-closed (AUTHORIZED-only).
- **Consumers**: ZERO production consumers. Only tests + documentation reference it.
- **Persistence**: NONE. ExecutionCommand exists only in memory.
- **Dashboard integration**: NONE.
- **Broker integration**: NONE.
- **Execution path**: NONE.

### 2.2 Upstream Dependencies

| Artifact | Status | Persistence |
|----------|--------|-------------|
| OperationalTradeIntent (14.2) | Frozen model, zero production consumers | NONE |
| ExecutionAuthorization (15.2) | Frozen model, engine (15.3) | YES — `ExecutionAuthorizationStore` (15.5) |
| TradePlan (Product Phase 4) | Frozen model | NONE |
| PaperTrade (Product Phase 5) | Frozen model | YES — `PaperTradeStore` |

### 2.3 Established Persistence Pattern

The repository has a consistent filesystem JSON persistence pattern:

1. **Atomic writes**: `tempfile.mkstemp` in same directory → write → flush → fsync (best-effort) → `os.replace`.
2. **Safe-id validation**: Regex `^[A-Za-z0-9._-]+$` prevents path traversal.
3. **Schema versioning**: Version checked before model reconstruction; future versions rejected.
4. **Typed exceptions**: Base error + specific errors (NotFound, IntegrityError, UnsupportedSchemaVersion).
5. **Default directory**: Relative to cwd (e.g., `./authorizations`, `./paper_trades`).
6. **No pickle/eval/exec**: Only JSON + Decimal + datetime encoding.
7. **Integrity checks**: Stored id must match reconstructed id; mismatches raise errors.

Implementations:
- `src/engine/persistence/execution_authorization_store.py` (Checkpoint 15.5)
- `src/dashboard/paper_trade_store.py` (Product Phase 5)
- `src/engine/registry/persistence.py` (Sprint 11K)

## 3. Persistence Requirement Analysis

### 3.1 Does ExecutionCommand Require Persistence?

**YES — but implementation can be deferred.**

Arguments FOR persistence:
- ExecutionCommand is the durable artifact bridging persisted ExecutionAuthorization and future execution adapters.
- Without persistence, the command is lost on process restart, making recovery impossible for any future execution consumer.
- The authorization IS persisted (15.5); the command is the natural downstream artifact that should also survive restarts.
- The pattern is well-established, additive, and does not weaken boundaries.
- Idempotency is structurally guaranteed by deterministic `command_id`.

Arguments for DEFERRAL:
- ExecutionCommand has ZERO production consumers today.
- No execution adapter, broker integration, or dashboard endpoint constructs or consumes commands.
- Adding persistence without a consumer is speculative infrastructure.
- The command can be re-derived from persisted authorization + intent if needed before a consumer exists.

**Decision**: Persistence is architecturally required but implementation-safe to defer until the first execution consumer appears. The design contract is established now so future implementation is boundary-correct.

### 3.2 Lifecycle Before Broker Submission

**Boundary: NOT_CREATED → CREATED only.**

Pre-submission lifecycle states:
- `NOT_CREATED` — the command does not yet exist (initial state).
- `CREATED` — the command has been constructed from an AUTHORIZED authorization and exists (in memory or persisted).

No additional states are needed before broker submission:
- No `SUBMITTED` state (belongs to BrokerOrder, downstream).
- No `ACKNOWLEDGED` state (belongs to BrokerOrder).
- No `FILLED` state (belongs to ExecutionResult, downstream).
- No `CANCELLED` state on the command itself (cancellation is a downstream concern).

The command is an immutable snapshot. Once created, it does not change. Any "cancellation" or "modification" requires creating a new command (via re-authorization).

### 3.3 Where Persistence Belongs

**New `ExecutionCommandStore` in `src/engine/persistence/`.**

Rationale:
- The command is a domain artifact of the execution layer, not the dashboard.
- The dashboard is presentation-only (Product Phase 1-3 principle).
- The engine layer (`src/engine/intelligence/`) is stateless computation — no persistence.
- The existing `src/engine/persistence/` package is the correct home (Checkpoint 15.5 established this boundary for ExecutionAuthorizationStore).
- Following the established pattern ensures consistency and reduces cognitive load.

Proposed location: `src/engine/persistence/execution_command_store.py`

Proposed API (mirrors ExecutionAuthorizationStore):
```python
class ExecutionCommandStore:
    def save(command, overwrite=False) -> Path
    def load(command_id) -> ExecutionCommand
    def exists(command_id) -> bool
    def list_commands() -> list[str]
    def delete(command_id) -> None
    def path_for(command_id) -> Path
```

Default directory: `./commands` (relative to cwd).

## 4. Boundary Safety Analysis

### 4.1 Can Persistence Be Introduced Without Weakening Frozen Boundaries?

**YES.**

Evidence:
- ExecutionCommand model is already frozen+slots, immutable, with deterministic identity.
- The model has ZERO current consumers — adding persistence does not change any existing behavior.
- The proposed store location (`src/engine/persistence/`) is additive — no existing files modified.
- The store follows the exact established pattern (ExecutionAuthorizationStore).
- No backward dependencies: the store imports the model; the model does not import the store.
- No circular dependencies possible.
- Frozen checkpoints (10.8, 11.8, 12.6, 13.6, 14.6, 15.6) remain unmodified.

### 4.2 Frozen File Verification

| Frozen Checkpoint | File | Modified? |
|-------------------|------|-----------|
| 10.8 | Various historical research files | NO |
| 11.8 | Pipeline/reporting files | NO |
| 12.6 | Decision intelligence files | NO |
| 13.6 | Execution architecture docs | NO |
| 14.6 | Operational Trade Intent files | NO |
| 15.6 | Execution Authorization files | NO |
| 16.2 | ExecutionCommand model | NO (model unchanged) |

### 4.3 Consumer Impact

Adding persistence to ExecutionCommand:
- Does NOT modify the model.
- Does NOT modify the factory.
- Does NOT modify any existing consumer (there are none).
- Does NOT modify dashboard, paper trading, planning, or authorization layers.
- Is purely additive: new store class + new serialization module.

## 5. Serialization Requirements

### 5.1 Existing Serialization Pattern

ExecutionAuthorization uses:
- `src/engine/persistence/execution_authorization_serialization.py`
- Deterministic sorted-key JSON
- Type tags for dataclasses, enums, datetime, Decimal, tuple
- Schema version constant (`AUTHORIZATION_SCHEMA_VERSION = 1`)
- `serialize_authorization()` / `deserialize_authorization()` / `parse_header()` / `canonical_json()`

### 5.2 ExecutionCommand Serialization Requirements

A new `execution_command_serialization.py` module should:
- Use the same deterministic sorted-key JSON pattern.
- Encode `Decimal` values as strings (preserving monetary precision).
- Encode `datetime` as ISO format.
- Encode `ExecutionMode` enum by name.
- Encode tuples as tagged lists.
- Include `COMMAND_SCHEMA_VERSION = 1`.
- Validate schema version before reconstruction.
- Be lossless for all fields (command_id, authorization_id, intent_id, content_fingerprint, instrument, direction, entry, stop, target, quantity, planned_risk, maximum_risk, execution_mode, created_at, valid_from, valid_until, label, metadata, version).

## 6. Integrity and Security Requirements

### 6.1 Identity Integrity

- `command_id` must match the file name (mirrors PaperTradeStore + ExecutionAuthorizationStore pattern).
- Mismatch → `CommandIntegrityError`.
- Deterministic id ensures idempotent saves (identical content → identical file).

### 6.2 Safe-ID Validation

- `command_id` must match `^[A-Za-z0-9._-]+$`.
- Rejects path traversal (`../`, absolute paths, reserved names).
- Validated before any filesystem operation.

### 6.3 Atomic Writes

- Same-dir temp file → flush → fsync → `os.replace`.
- Temp file cleaned up on failure.
- Never leaves partial content at target path.

### 6.4 Corruption Handling

- Malformed JSON → `CommandStoreError` (never silently returns a partial command).
- Missing schema version → `CommandStoreError`.
- Future schema version → `UnsupportedCommandSchemaVersionError`.
- Missing file → `CommandNotFoundError`.

## 7. Lifecycle Boundary Specification

### 7.1 Pre-Submission Lifecycle

```
NOT_CREATED
    |
    |  create_execution_command() (factory call)
    v
CREATED
    |
    |  [future] BrokerAdapter.submit(command)
    v
SUBMITTED  ← downstream (BrokerOrder, not ExecutionCommand)
```

### 7.2 State Transitions

| Transition | Trigger | Owner |
|------------|---------|-------|
| NOT_CREATED → CREATED | Explicit factory call with AUTHORIZED authorization | Application layer |
| CREATED → (persisted) | Explicit store.save() call | Application layer |
| CREATED → (loaded) | Explicit store.load() call | Application layer |

### 7.3 What Does NOT Mutate the Command

- Future candles do NOT change the command (immutable by construction).
- Market data changes do NOT affect the command.
- Paper-trade results do NOT modify the command.
- Authorization expiration/revocation does NOT modify the command (the command is a point-in-time snapshot).
- Dashboard rendering does NOT create or modify commands.

## 8. Relationship to Other Persistence Layers

| Layer | Location | Purpose | Command Store? |
|-------|----------|---------|----------------|
| ExecutionAuthorizationStore | `src/engine/persistence/` | Persist authorizations | N/A |
| PaperTradeStore | `src/dashboard/` | Persist paper trades | NO (different domain) |
| ExperimentPersistence | `src/engine/registry/` | Persist experiment results | NO (different domain) |
| SetupResearchStore | `src/engine/data/` | Persist setup research | NO (different domain) |
| LiveValidationStore | `src/dashboard/` | Persist live validation observations | NO (different domain) |
| **ExecutionCommandStore** | **`src/engine/persistence/`** | **Persist execution commands** | **YES (proposed)** |

## 9. Dashboard Integration Decision

**NO dashboard integration in this checkpoint.**

Rationale:
- The dashboard is presentation-only (Product Phase 1-3 principle).
- There is no execution UI requirement yet (no broker adapter, no order placement).
- Dashboard endpoints that surface execution commands would create an implicit execution pathway, violating the fail-closed principle.
- Any future dashboard integration must be explicitly scoped and must NOT construct or modify commands.

## 10. Security Considerations

### 10.1 Credential Safety

- ExecutionCommand contains NO credentials, NO broker keys, NO API tokens.
- The command is broker-neutral; all broker-specific secrets belong to the future BrokerAdapter.
- Persistence does not introduce new credential exposure.

### 10.2 Path Safety

- Safe-id regex prevents directory traversal.
- All paths constructed via `pathlib` (no string concatenation).
- Default directory relative to cwd (no hard-coded absolute paths).

### 10.3 Tamper Evidence

- Deterministic `command_id` is content-addressed.
- Any modification to a persisted command changes its `command_id`, causing a mismatch on load.
- Schema versioning prevents downgrade attacks.

## 11. Determinism and Immutability

### 11.1 Determinism

- `command_id` is deterministic (SHA-256 of canonical payload).
- Identical authorization + intent + parameters → identical command_id.
- No wall-clock dependency (timestamps are caller-supplied).
- No random values.
- No UUID generation.
- No mutable global state.

### 11.2 Immutability

- `frozen=True, slots=True` dataclass.
- All fields are immutable types (str, Decimal, datetime, tuple, ExecutionMode enum).
- Factory returns a new instance; no mutation of inputs.
- Persistence stores the exact serialized form; loaded record is a new instance.

## 12. Test Requirements

If/when ExecutionCommandStore is implemented, tests must cover:

1. **Basic persistence**: save, load, exists, missing command.
2. **Round-trip**: exact identity/fingerprint/Decimal/datetime/enum/metadata preservation.
3. **Restart**: fresh store instance loads persisted command.
4. **Idempotency**: identical save returns same path without error.
5. **Integrity**: corrupted JSON, missing fields, invalid schema, identity mismatch.
6. **Security**: unsafe command IDs, path traversal attempts.
7. **Immutability**: save does not mutate original, load returns independent artifact.
8. **Atomic writes**: no partial files, temp cleanup on failure.
9. **Boundary isolation**: store does not import execution/broker functionality.
10. **Determinism**: repeated saves produce identical bytes.

## 13. Architectural Invariants Preserved

- **models ← intelligence ← dashboard** direction unchanged.
- **No circular dependencies**: store imports model; model does not import store.
- **No execution semantics** in model or store (broker-neutral).
- **No paper-trading dependency** in execution command layer.
- **No dashboard dependency** in execution command store.
- **No market data access** in execution command store.
- **Frozen checkpoints unmodified**: 10.8, 11.8, 12.6, 13.6, 14.6, 15.6.
- **No existing engine/model modified**: only additive new files.

## 14. Limitations

1. **No persistence implementation** — designed and audited, not implemented (deferred to first execution consumer).
2. **No post-CREATED lifecycle states** — EXPIRED/REVOKED/SUPERSEDED on the command itself are intentionally absent (those belong to the authorization layer or downstream broker order).
3. **No dashboard integration** — intentionally deferred (presentation-only principle).
4. **No broker adapter** — intentionally deferred (Checkpoint 13 boundary).
5. **No execution pathway** — intentionally absent (the command is a snapshot, not an executor).

## 15. Recommended Next Steps

1. **Freeze Checkpoint 16.4** — the audit is complete; no implementation required.
2. **Defer ExecutionCommandStore implementation** until the first execution consumer appears (e.g., a BrokerAdapter or execution orchestration layer).
3. **When implementing**, follow the exact pattern from `ExecutionAuthorizationStore`:
   - Same atomic write discipline.
   - Same safe-id validation.
   - Same schema versioning.
   - Same typed exception hierarchy.
   - Same default directory convention (`./commands`).
4. **Do NOT** integrate ExecutionCommand with dashboard, paper trading, or any existing consumer until a genuine execution consumer requires it.

## 16. Files Inspected

### Source
- `src/engine/models/execution_command.py`
- `src/engine/models/execution_authorization.py`
- `src/engine/models/operational_trade_intent.py`
- `src/engine/models/trade_plan.py`
- `src/engine/models/paper_trade.py`
- `src/engine/intelligence/execution_authorization.py`
- `src/engine/intelligence/operational_trade_intent.py`
- `src/engine/intelligence/operational_trade_intent_application.py`
- `src/engine/persistence/execution_authorization_serialization.py`
- `src/engine/persistence/execution_authorization_store.py`
- `src/engine/persistence/exceptions.py`
- `src/dashboard/paper_trade_store.py`
- `src/dashboard/services.py`
- `src/dashboard/app.py`
- `src/engine/registry/persistence.py`

### Tests
- `tests/test_execution_command.py`
- `tests/test_execution_authorization.py`
- `tests/test_execution_authorization_engine.py`
- `tests/test_execution_authorization_store.py`
- `tests/test_operational_trade_intent.py`
- `tests/test_operational_trade_intent_engine.py`
- `tests/test_operational_trade_intent_application.py`

### Documentation
- `docs/checkpoint_16_1_authorized_intent_to_execution_command_boundary_audit.md`
- `docs/checkpoint_16_2_execution_command_model_and_identity_implementation.md`
- `docs/checkpoint_16_3_execution_command_factory_and_authorization_integration_boundary_audit.md`
- `docs/checkpoint_15_4_execution_authorization_persistence_and_lifecycle_boundary_audit.md`
- `docs/checkpoint_15_5_execution_authorization_persistence_implementation.md`
- `docs/checkpoint_15_6_final_execution_authorization_integration_and_freeze_audit.md`

## 17. Final Recommendation

**ExecutionCommand persistence is architecturally required but implementation-safe to defer.**

The command is the natural downstream artifact of a persisted authorization. It must survive process restarts for any future execution adapter to function. The persistence pattern is well-established, additive, and does not weaken any frozen boundary. However, with ZERO current consumers, implementing the store now would be speculative. The design contract is established: a future `ExecutionCommandStore` in `src/engine/persistence/` following the exact atomic-JSON pattern of `ExecutionAuthorizationStore`.

**Freeze Checkpoint 16.4. No implementation changes required.**
