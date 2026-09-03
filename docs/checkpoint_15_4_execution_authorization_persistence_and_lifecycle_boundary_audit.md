# Checkpoint 15.4 — Execution Authorization Persistence & Lifecycle Boundary Audit & Design

## 1. Scope

This checkpoint performs an architecture-first audit and design review of the Execution Authorization persistence and lifecycle boundary. No implementation changes are made unless a genuine downstream integration defect is discovered and cannot be solved downstream.

**Frozen boundaries (verified intact):**
- Checkpoint 10.8 — Historical Research → FROZEN
- Checkpoint 11.8 — Final Current-Market Analytical Output → FROZEN
- Checkpoint 12.6 — Trade Planning & Paper Trading Simulation → FROZEN
- Checkpoint 13.6 — Execution Architecture Boundary → FROZEN
- Checkpoint 14.6 — Operational Trade Intent → FROZEN
- Checkpoint 15.1 — Execution Authorization Boundary → PASS WITH LIMITATIONS
- Checkpoint 15.2 — Execution Authorization Model & Deterministic Identity → COMPLETE
- Checkpoint 15.3 — Execution Authorization Engine & Workflow → COMPLETE

## 2. Previous Checkpoint Context

Checkpoints 15.1–15.3 established:
- The conceptual authorization boundary (OperationalTradeIntent ≠ ExecutionAuthorization ≠ ExecutionCommand ≠ Broker Order ≠ Position)
- The immutable `ExecutionAuthorization` model with deterministic `authorization_id` ("auth-" + sha256[:16])
- The `ExecutionAuthorizationEngine` with eligibility evaluation and explicit authorization workflow
- All six lifecycle states: UNAUTHORIZED, ELIGIBLE, AUTHORIZED, EXPIRED, REVOKED, SUPERSEDED

**What was NOT established:** persistence, durability across process restarts, lifecycle transition mechanics, or a repository/store layer.

## 3. Exact Files Inspected

### Authorization layer
- `src/engine/models/execution_authorization.py` — model + factory
- `src/engine/intelligence/execution_authorization.py` — engine + result types
- `tests/test_execution_authorization.py` — 97 model tests
- `tests/test_execution_authorization_engine.py` — 84 engine tests

### Operational intent layer
- `src/engine/models/operational_trade_intent.py` — model + factory
- `src/engine/intelligence/operational_trade_intent.py` — engine
- `src/engine/intelligence/operational_trade_intent_application.py` — application service
- `tests/test_operational_trade_intent.py` — 125 model tests
- `tests/test_operational_trade_intent_engine.py` — 69 engine tests
- `tests/test_operational_trade_intent_application.py` — 58 application tests

### Planning/paper-trading boundaries
- `src/engine/models/trade_plan.py` — TradePlan, RiskPlanStatus
- `src/engine/models/paper_trade.py` — PaperTrade, PaperTradeStatus
- `src/engine/intelligence/trade_planning.py` — TradePlanningEngine
- `src/engine/intelligence/paper_trading.py` — PaperTradingEngine

### Dashboard layer
- `src/dashboard/services.py` — DashboardAnalysisService
- `src/dashboard/views.py` — presentation models, ActionabilityState
- `src/dashboard/app.py` — FastAPI routes
- `src/dashboard/paper_trade_store.py` — existing filesystem persistence

### Existing persistence infrastructure
- `src/engine/registry/persistence.py` — ExperimentPersistence (Sprint 11K pattern)
- `src/engine/selection/persistence.py` — SelectionPersistence
- `src/dashboard/paper_trade_store.py` — PaperTradeStore
- `src/engine/data/historical_store.py` — HistoricalDataStore
- `src/engine/data/research_corpus_store.py` — ResearchCorpusStore
- `src/engine/data/setup_research_store.py` — SetupResearchStore

### Prior audit documents
- `docs/checkpoint_15_1_execution_authorization_boundary_audit.md`
- `docs/checkpoint_15_2_execution_authorization_model_and_identity_implementation.md`
- `docs/checkpoint_15_3_execution_authorization_engine_and_workflow_implementation.md`

## 4. Existing Authorization Architecture

### Model layer (`src/engine/models/execution_authorization.py`)

`ExecutionAuthorization` is a `frozen=True, slots=True` dataclass with 15 fields:

- **Identity**: `authorization_id` ("auth-" + sha256[:16]), `intent_id`, `plan_id`, `content_fingerprint`
- **Status**: `AuthorizationStatus` (UNAUTHORIZED/ELIGIBLE/AUTHORIZED/EXPIRED/REVOKED/SUPERSEDED)
- **Timestamps**: `authorized_at`, `valid_from`, `expires_at` (all timezone-aware, caller-supplied)
- **Provenance**: `issuer`, `authorization_method`, `scope`, `policy_reference`, `safety_check_summary`
- **Metadata**: `label`, `metadata` (sorted tuple of pairs)

**Critical design rules verified in code:**
- `authorization_id` is deterministic: derived from canonical payload via SHA-256, excludes timestamps
- Status IS included in identity payload (different status → different authorization_id)
- The model NEVER calls `datetime.now()` or `datetime.utcnow()`
- `__post_init__` validates: required fields, timestamp ordering (`valid_from >= authorized_at`, `expires_at > valid_from`), timezone awareness, non-empty provenance fields
- `is_authorized` property returns `True` ONLY for `AuthorizationStatus.AUTHORIZED`
- No broker fields, no execution fields, no paper-trade fields

### Engine layer (`src/engine/intelligence/execution_authorization.py`)

`ExecutionAuthorizationEngine` is stateless (no `_cache`, `_registry`, `_state` attributes).

Two public methods:
1. `evaluate_eligibility(intent, evaluation_timestamp) -> EligibilityResult` — evaluates 14 policy conditions
2. `authorize(intent, evaluation_timestamp, ...) -> AuthorizationDecision` — explicit authorization workflow

**Critical design rules verified in code:**
- Engine delegates ALL authorization record construction to `create_authorization()` factory
- Eligibility and authorization are separate concepts (eligibility alone does NOT create AUTHORIZED)
- No `datetime.now()` — caller supplies `evaluation_timestamp`
- Fail-closed: any unknown/missing/contradictory condition returns `authorized=False`
- Catches `TypeError`/`ValueError` from factory gracefully

### Application service layer

`OperationalTradeIntentApplicationService` (Checkpoint 14.5) wraps the intent engine. It has NO authorization methods. Authorization is NOT a side effect of intent creation, trade planning, paper trading, or dashboard rendering.

## 5. Existing Persistence Architecture

### Current authorization persistence: NONE

**Finding: ExecutionAuthorization has NO persistence mechanism.**

The model file (`execution_authorization.py`) contains NO `save()`, `load()`, `serialize()`, or `persist()` methods. The engine file contains NO persistence code. There is:
- No `AuthorizationStore` or `AuthorizationRepository`
- No `AuthorizationSerializer`
- No JSON file output for authorization records
- No database connection
- No filesystem interaction of any kind

The only `json.dumps` call in the authorization module is for canonical identity computation (SHA-256 hashing), NOT for serialization to storage.

### Established persistence patterns in the codebase

The repository has a well-established filesystem-JSON persistence discipline, used consistently across 6+ stores:

| Store | Location | Pattern |
|-------|----------|---------|
| `PaperTradeStore` | `dashboard/paper_trade_store.py` | One JSON file per record, atomic write (mkstemp + os.replace), safe-id regex, schema-version check, Decimal/datetime encoding |
| `ExperimentPersistence` | `engine/registry/persistence.py` | Same pattern, `.json` suffix |
| `SelectionPersistence` | `engine/selection/persistence.py` | Same pattern, `.selection` suffix |
| `HistoricalDataStore` | `engine/data/historical_store.py` | One dir per (instrument,timeframe), atomic write |
| `ResearchCorpusStore` | `engine/data/research_corpus_store.py` | Manifest-only, atomic write |
| `SetupResearchStore` | `engine/data/setup_research_store.py` | One JSON file per result, atomic write |
| `LiveValidationStore` | `dashboard/live_validation_store.py` | `.validation` suffix, atomic write |

**Common pattern (Sprint 11K discipline):**
- One file per record (`<directory>/<id>.json` or custom suffix)
- Atomic write: `tempfile.mkstemp` in SAME directory → write → flush → fsync (best-effort) → `os.replace`
- Safe-id validation (`_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")`)
- Schema version checked BEFORE model reconstruction
- `PaperTradeStoreError` / `*IntegrityError` / `*NotFoundError` exception hierarchy
- Default directory relative to cwd (no hard-coded absolute paths)
- No `pickle`/`eval`/`exec`

## 6. Actual Authorization Data Flow

### Current flow (in-memory only)

```
OperationalTradeIntent
        ↓
ExecutionAuthorizationEngine.evaluate_eligibility()
        ↓
EligibilityResult (in-memory)
        ↓
ExecutionAuthorizationEngine.authorize()
        ↓
create_authorization() factory
        ↓
ExecutionAuthorization (in-memory, immutable)
        ↓
[NO PERSISTENCE — artifact is lost when process terminates]
```

### Who creates authorization
The `ExecutionAuthorizationEngine.authorize()` method creates authorization. It is called explicitly by the application layer (not automatically). No production caller currently exists — the engine has zero production consumers.

### Who owns authorization state
Currently: **nobody owns state**. The `ExecutionAuthorization` object is created and returned. The caller is responsible for retaining it. There is no registry, no store, no cache.

### Where authorization currently exists
In-memory only — as a return value from `engine.authorize()`. Once the Python object is garbage-collected or the process terminates, the authorization is lost.

### Whether it is persisted
**NO.** There is no persistence code for `ExecutionAuthorization` anywhere in the repository.

### Whether it survives process restart
**NO.** Since there is no persistence, an authorization created by Process A is completely lost when Process A terminates. Process B starts with zero authorization state.

### Whether multiple authorization records can coexist
Not currently testable — there is no store to hold multiple records. The model supports multiple distinct authorizations (different `authorization_id` for different statuses/content), but they can only coexist in memory during a single process lifetime.

### Whether old authorization records remain distinguishable
The deterministic `authorization_id` design supports this — different statuses produce different IDs, so multiple authorization records for the same intent would be distinguishable. But this is untested because no persistence exists.

### Whether lifecycle state can be reconstructed deterministically
**NO.** Without persistence, lifecycle state cannot be reconstructed after restart. The model is immutable (frozen), so lifecycle transitions would require creating new records — but without a store, there is nowhere to reconstruct from.

### Whether authorization can be queried independently of intent creation
**NO.** There is no query mechanism. Authorization can only be obtained by calling `engine.authorize()` with a fresh intent and explicit inputs.

## 7. Lifecycle Audit

### States currently implemented

All six states exist in the `AuthorizationStatus` enum:

```python
class AuthorizationStatus(Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    ELIGIBLE = "ELIGIBLE"
    AUTHORIZED = "AUTHORIZED"
    EXPIRED = "EXPIREDED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
```

### Transitions implemented vs. conceptual

| Transition | Status | Implementation |
|------------|--------|----------------|
| None → UNAUTHORIZED | Conceptual | Implicit — every intent starts ununauthorized |
| UNAUTHORIZED → ELIGIBLE | Engine | `evaluate_eligibility()` returns `EligibilityResult(eligible=True)` |
| ELIGIBLE → AUTHORIZED | Engine | `authorize()` with explicit inputs → `AuthorizationDecision(authorized=True)` |
| AUTHORIZED → EXPIRED | NOT implemented | No transition mechanism exists |
| AUTHORIZED → REVOKED | NOT implemented | No transition mechanism exists |
| AUTHORIZED → SUPERSEDED | NOT implemented | No transition mechanism exists |
| Any → UNAUTHORIZED | NOT implemented | No reset mechanism exists |

### Who is allowed to cause each transition
Currently only the engine can cause transitions (UNAUTHORIZED → ELIGIBLE → AUTHORIZED). The post-AUTHORIZED transitions (EXPIRED/REVOKED/SUPERSEDED) have NO implementation — no code path can produce them.

### Whether lifecycle transitions mutate immutable artifacts
The `ExecutionAuthorization` model is `frozen=True`. It CANNOT be mutated in place. Any lifecycle transition MUST create a new `ExecutionAuthorization` record with the new status.

**This is the correct design.** The immutable artifact rule is already enforced by the model. The missing piece is the mechanism to create and persist the new record.

### Whether a transition creates a new authorization record or mutates existing state
The model enforces creation of new records (frozen dataclass). But there is NO engine method to perform lifecycle transitions, and NO store to hold multiple records.

### Whether historical authorization state is preserved
**NO.** Since there is no persistence and no transition mechanism, historical authorization state is not preserved. When the process restarts, ALL authorization state is lost.

### Special attention: AUTHORIZED → EXPIRED/REVOKED/SUPERSEDED

These transitions must NOT silently become "AUTHORIZED → still authorized" after restart. Currently, after restart, there is NO authorization state at all — which is actually fail-closed (no authorization = not authorized). However, this is an accidental property of having no persistence, not a deliberate design choice.

**Risk:** If persistence is added later without explicit lifecycle handling, a naïve reload that only checks for the presence of an `authorization_id` could accidentally treat any found record as currently AUTHORIZED, regardless of its actual status.

## 8. Persistence Boundary

### Current state: No persistence boundary exists

There is no authorization repository, store, serializer, or any persistence mechanism.

### Where persistence should live

Based on the established codebase patterns, the recommended design is:

```
ExecutionAuthorization
        ↓
Authorization Repository / Store  (NEW)
        ↓
Durable Storage (filesystem JSON)
```

**Recommended location:** `src/engine/intelligence/execution_authorization_serialization.py` (serializer) + `src/dashboard/authorization_store.py` or `src/engine/intelligence/authorization_repository.py` (store).

The store should follow the established Sprint 11K / PaperTradeStore pattern:
- One JSON file per authorization record
- Atomic writes (mkstemp + os.replace)
- Safe-id validation
- Schema-versioned
- `AuthorizationStoreError` / `AuthorizationNotFoundError` / `AuthorizationIntegrityError` exception hierarchy
- Default directory: `./data/authorizations` (relative to cwd)

### Why this location
- The model lives in `engine/models/execution_authorization.py`
- The engine lives in `engine/intelligence/execution_authorization.py`
- Serialization should be in the engine package (matching the pattern of `paper_trading_serialization.py`, `trade_planning_serialization.py`, etc.)
- The store should be accessible from both the engine layer and the dashboard layer (matching `PaperTradeStore` in `dashboard/`)

### What the persistence layer MUST NOT do

Per the architectural rules:
- MUST NOT create authorization permission
- MUST NOT evaluate trading eligibility
- MUST NOT alter authorization semantics
- MUST NOT create execution commands
- MUST NOT contact brokers
- MUST NOT modify TradePlan
- MUST NOT modify OperationalTradeIntent
- MUST NOT modify PaperTrade
- MUST NOT perform market analysis

## 9. Immutable Artifact Rule

### Current state: Rule is enforced by the model

`ExecutionAuthorization` is `frozen=True, slots=True`. It cannot be mutated in place. The `__post_init__` validates all fields on construction.

### Lifecycle changes must not mutate the original

Since the model is frozen, lifecycle changes ALREADY require creating a new `ExecutionAuthorization` record. This is correct.

### Recommended representation for lifecycle transitions

**Option A — Create a new immutable authorization record** is the correct choice, already enforced by the model. When a lifecycle transition occurs (e.g., AUTHORIZED → EXPIRED), a new `ExecutionAuthorization` is created with the new status and a new deterministic `authorization_id` (because status is included in the identity payload).

This means:
- The original AUTHORIZED record is preserved unchanged
- The new EXPIRED record coexists with the original
- Both are distinguishable by their `authorization_id`
- The current state of an intent is determined by querying the store for the latest record

Option B (external lifecycle record) is unnecessary because the model already supports creating new immutable records for each state change. Option C is not needed.

### Reasoning
The existing model design already supports Option A naturally. The identity payload includes `status`, so a status change automatically produces a new `authorization_id`. No additional architecture is needed.

## 10. Identity and Deduplication Audit

### authorization_id determinism

Verified in code (`src/engine/models/execution_authorization.py`):
- Format: `"auth-" + sha256[:16]` of canonical payload
- Payload includes: `intent_id`, `plan_id`, `content_fingerprint`, `status`, `issuer`, `authorization_method`, `scope`, `policy_reference`, `safety_check_summary`, `label`, `metadata` (sorted)
- Timestamps are EXPLICITLY excluded from identity payload
- No UUID, no wall-clock dependency, no memory address

### intent_id and content_fingerprint

Verified in code (`src/engine/models/operational_trade_intent.py`):
- `intent_id`: `"intent-" + sha256[:16]` of canonical identity payload (includes instance discriminator: `created_at`, `evaluation_timestamp`, `label`, `metadata`)
- `content_fingerprint`: `"fp-" + sha256[:16]` of canonical fingerprint payload (economic content ONLY — excludes timestamps, labels, metadata)
- Both deterministic, no UUID, no wall-clock

### Persistence does not regenerate IDs

**N/A — no persistence exists.** But the design rule is clear: IDs are computed by the factory, not by the persistence layer. Any future store must preserve the `authorization_id` exactly as produced by the factory.

### Serialization/deserialization preserves identity

**N/A — no serializer exists.** But any future serializer must preserve `authorization_id`, `intent_id`, `content_fingerprint` verbatim.

### Duplicate writes are deterministic

Since `authorization_id` is deterministic, calling `create_authorization()` twice with identical inputs produces identical IDs. With persistence, this means duplicate writes would produce identical filenames, enabling idempotent save behavior.

### Reloading does not produce different identity

If a persisted authorization is deserialized and its `authorization_id` is preserved exactly, reloading produces the same identity. This depends on the serializer preserving the field verbatim.

### Two distinct authorization events can coexist

The design supports this: different statuses → different `authorization_id`s → different filenames. Two authorizations for the same intent (e.g., one AUTHORIZED, one later EXPIRED) can coexist in the store.

### An authorization for one intent cannot be retrieved as another intent's

`intent_id` is bound at creation and validated in `__post_init__`. The store should key by `authorization_id` but also validate `intent_id` on retrieval.

### Repository needs

| Need | Required? | Rationale |
|------|-----------|-----------|
| Primary-key semantics | YES | `authorization_id` is the primary key |
| Uniqueness constraints | YES | Each `authorization_id` must be unique |
| Idempotent save behavior | YES | Same authorization → same file; overwrite should be explicit |
| Duplicate rejection | OPTIONAL | Could silently overwrite with identical content (idempotent) |
| Overwrite prevention | YES | Default should prevent accidental overwrite; explicit flag needed |

## 11. Fingerprint Integrity

### What must be preserved

Three fields must survive persistence unchanged:
1. `intent_id` — must match `authorization.intent_id`
2. `content_fingerprint` — must match `authorization.content_fingerprint`
3. `authorization_id` — must match the reconstructed deterministic ID

### Verification mechanism

The model's `__post_init__` already validates:
- `authorization_id` starts with `AUTHORIZATION_ID_PREFIX` ("auth-")
- `content_fingerprint` starts with `FINGERPRINT_PREFIX` ("fp-")
- `intent_id` is non-empty
- `content_fingerprint` is non-empty

A future store should add integrity validation on load:
```python
# Pseudocode — not implemented
def _verify_integrity(self, auth: ExecutionAuthorization) -> None:
    expected_id = _recompute_authorization_id(auth)
    if expected_id != auth.authorization_id:
        raise AuthorizationIntegrityError("authorization_id mismatch")
```

### Where integrity validation belongs

**The model layer** (`__post_init__`) already validates format. The **repository layer** should add cross-field integrity checks on load (recomputing the authorization_id from the canonical payload and comparing to the stored value). This is the narrowest correct boundary — the model validates its own structure; the repository validates that persistence hasn't corrupted the identity.

## 12. Timestamp and Validity Audit

### Current timestamp handling

All timestamps in `ExecutionAuthorization` are:
- **Timezone-aware**: `__post_init__` enforces `tzinfo is not None` for all three timestamps
- **Caller-supplied**: The model NEVER generates timestamps
- **Ordered**: `valid_from >= authorized_at`, `expires_at > valid_from`
- **Intent-bounded**: When `intent.valid_until` is present, `expires_at <= intent.valid_until`

### Timestamp relationships verified

```python
valid_from >= authorized_at  ✓ (enforced in __post_init__)
expires_at > valid_from      ✓ (enforced in __post_init__)
expires_at <= intent.valid_until  ✓ (enforced in factory, when intent.valid_until is not None)
authorization lifetime <= intent lifetime  ✓ (derived from the above)
```

### Naive/aware datetime handling

The model raises `ValueError` if any timestamp is naive. The engine also checks `evaluation_timestamp.tzinfo is not None`. This is fail-closed.

### Persistence preserves timezone information

**N/A — no persistence exists.** But any future serializer must preserve timezone-aware datetimes in ISO format (matching the established pattern: `datetime.isoformat()` on serialization, `datetime.fromisoformat()` on deserialization).

### Expiration is deterministic

Since `expires_at` is caller-supplied and preserved exactly, expiration is fully deterministic. No `datetime.now()` is ever called.

### No persistence code uses datetime.now()

Verified: there is no persistence code for authorization.

## 13. Restart Safety

### Current behavior: NOT restart-safe

**Finding: Authorization does NOT survive process restart.**

Since there is no persistence:
1. Process A creates AUTHORIZED authorization
2. Process A terminates
3. Process B starts
4. Process B has ZERO authorization state
5. Any authorization query returns UNAUTHORIZED / not-authorized

This is fail-closed (no authorization = not authorized), which is safe but means the system "forgets" all authorization state on restart.

### What restart safety requires

A persistence layer that:
1. Saves `ExecutionAuthorization` records to durable storage
2. Loads records on startup
3. Validates integrity on load (recomputes `authorization_id`)
4. Handles corrupted/missing records fail-closed

### Whether expired authorization remains expired

If a persisted record has `status=EXPIRED`, it remains EXPIRED after reload. The status is part of the immutable record. No time-based recomputation is needed.

### Whether revoked authorization remains revoked

Same as above — `status=REVOKED` is preserved in the immutable record.

### Whether superseded authorization remains superseded

Same — `status=SUPERSEDED` is preserved.

### Whether an unknown authorization fails closed

An `AuthorizationStatus` with an unknown value cannot be constructed (Enum validation). Any persisted record with an invalid status should be rejected by the integrity check.

### Whether corrupted records fail closed

The model's `__post_init__` raises `ValueError` on structural corruption. A future store should catch this during deserialization and raise a typed `AuthorizationIntegrityError`.

## 14. Corruption and Fail-Closed Behavior

### Current corruption handling

The model's `__post_init__` validates:
- Non-empty `authorization_id`, `intent_id`, `plan_id`, `content_fingerprint`
- `authorization_id` starts with "auth-"
- `content_fingerprint` starts with "fp-"
- All timestamps are timezone-aware
- `valid_from >= authorized_at`
- `expires_at > valid_from`
- Non-empty provenance fields

Any violation raises `ValueError` — the record cannot be constructed.

### How malformed records should behave

Since no persistence exists, this is prospective design. The recommended behavior:

| Corruption type | Recommended behavior |
|-----------------|---------------------|
| Missing `authorization_id` | Reject — `ValueError` in `__post_init__` |
| Missing `intent_id` | Reject — `ValueError` in `__post_init__` |
| Missing `content_fingerprint` | Reject — `ValueError` in `__post_init__` |
| Invalid status | Reject — `ValueError` in `__post_init__` (Enum validation) |
| Invalid timestamp (naive) | Reject — `ValueError` in `__post_init__` |
| Invalid enum | Reject — `ValueError` in `__post_init__` |
| Invalid Decimal | Reject — `ValueError` in `__post_init__` |
| Modified economic fields | Detect via integrity check (recompute `authorization_id`) |
| Inconsistent identity | Detect via integrity check |
| Truncated record | Reject — JSON parse error → `AuthorizationIntegrityError` |
| Duplicate record | Idempotent — same `authorization_id` → same file |

**Safest behavior: Reject with explicit error.** A corrupted or unverifiable authorization record MUST NEVER become AUTHORIZED. The store should raise `AuthorizationIntegrityError` and the engine should treat this as "not authorized" (fail-closed).

## 15. Schema and Serialization Audit

### Current serialization: NONE

There is no `execution_authorization_serialization.py` file. The model has no `to_json()` / `from_json()` methods.

### Required serializer boundary

A future serializer should:
- Follow the established pattern (see `paper_trading_serialization.py`, `trade_planning_serialization.py`)
- Use `__enum__` / `__dataclass__` / `__datetime__` / `__decimal__` / `__tuple__` type tags
- Use `AUTHORIZATION_SCHEMA_VERSION = 1` module constant
- Provide `serialize_authorization` / `deserialize_authorization` / `canonical_authorization_json` / `parse_authorization_header`
- Preserve `authorization_id`, `intent_id`, `content_fingerprint` exactly
- Check schema version BEFORE model reconstruction
- Reject future schema versions with explicit error

### The persisted representation must not become a second domain model

The serializer is a transport mechanism only. The domain model (`ExecutionAuthorization`) remains the single source of truth. The serializer converts to/from JSON; it does not add fields, modify semantics, or introduce a parallel model.

## 16. Concurrency / Multiple Authorization Records

### Current concurrency: Single-threaded, single-process

The engine is stateless and has no concurrency controls. There is no store, so there is no concurrent access to worry about.

### Minimum concurrency guarantees required

For the current project stage (personal research workstation):
- **Within a single process**: No special guarantees needed — Python's GIL protects the in-memory objects
- **Across processes**: The atomic-write pattern (mkstemp + os.replace) provides crash-safe writes
- **Duplicate creation**: The deterministic `authorization_id` means duplicate `create_authorization()` calls with the same inputs produce the same ID — enabling idempotent save behavior
- **Multiple authorization records for the same intent**: Supported by design (different statuses → different IDs)

### What is deferred

- No locking/versioning mechanism needed at this stage
- No optimistic concurrency control needed
- No distributed coordination needed (single workstation)
- No authorization revocation queue needed

## 17. Authorization vs Persistence Responsibility Matrix

| Responsibility | Owner | Status |
|----------------|-------|--------|
| Intent creation | `OperationalTradeIntentApplicationService` | ✓ Implemented (Checkpoint 14.5) |
| Eligibility evaluation | `ExecutionAuthorizationEngine` | ✓ Implemented (Checkpoint 15.3) |
| Authorization creation | `ExecutionAuthorizationEngine.authorize()` → `create_authorization()` factory | ✓ Implemented (Checkpoint 15.3) |
| Authorization identity | `ExecutionAuthorization` model (`_sha256_prefix`) | ✓ Implemented (Checkpoint 15.2) |
| Authorization lifecycle | **NOT IMPLEMENTED** | ✗ No transition mechanism exists |
| Durable storage | **NOT IMPLEMENTED** | ✗ No store exists |
| Serialization | **NOT IMPLEMENTED** | ✗ No serializer exists |
| Integrity verification | Model `__post_init__` + **future store** | ✓ Partial (model only) |
| Execution command creation | Future Execution Command layer | ✗ Not started |
| Broker communication | Future Broker Adapter | ✗ Not started |
| Position management | Future Portfolio/Position layer | ✗ Not started |

**Key finding: The authorization boundary is clean and complete up to the point of creating the immutable record. What is missing is the persistence layer that makes the record durable.**

## 18. Frozen-Boundary Verification

### Verified: No frozen boundaries were modified

All files from frozen checkpoints remain unchanged:
- `src/engine/models/trade_plan.py` — Unchanged (TradePlan, RiskPlanStatus)
- `src/engine/models/paper_trade.py` — Unchanged (PaperTrade, PaperTradeStatus)
- `src/engine/intelligence/trade_planning.py` — Unchanged (TradePlanningEngine)
- `src/engine/intelligence/paper_trading.py` — Unchanged (PaperTradingEngine)
- `src/engine/models/operational_trade_intent.py` — Unchanged (Checkpoint 14.2)
- `src/engine/intelligence/operational_trade_intent.py` — Unchanged (Checkpoint 14.4)
- `src/engine/intelligence/operational_trade_intent_application.py` — Unchanged (Checkpoint 14.5)

### No genuine downstream integration defects found

No defects were discovered that require reopening any frozen boundary. All identified gaps are in the authorization layer itself (missing persistence), not in downstream consumers.

## 19. Execution Isolation

### Verified: No execution implementation exists

The architecture correctly stops at:
```
OperationalTradeIntent
        ↓
ExecutionAuthorization
        ↓
Persistence  [MISSING — this checkpoint]
        ↓
Future ExecutionCommand  [NOT STARTED]
        ↓
Future BrokerAdapter  [NOT STARTED]
```

There is:
- No `ExecutionCommand` implementation
- No `BrokerAdapter` implementation
- No broker SDK
- No order submission code
- No live trading code
- No position management code
- No portfolio management code
- No account execution code
- No broker credentials in the authorization layer
- No order IDs, fills, slippage, fees, or routing

The authorization model has been verified to contain NO broker-related fields (tested: no `order_id`, `fill_price`, `position_id`, `broker_id` attributes).

## 20. Tests and Results

### Authorization-specific tests

```
tests/test_execution_authorization.py       — 97 tests  ✓ ALL PASSED
tests/test_execution_authorization_engine.py — 84 tests  ✓ ALL PASSED
tests/test_operational_trade_intent.py       — 125 tests ✓ ALL PASSED
tests/test_operational_trade_intent_engine.py — 69 tests ✓ ALL PASSED
tests/test_operational_trade_intent_application.py — 58 tests ✓ ALL PASSED
```

**Combined: 433 authorization/intent tests, all passed.**

### Full test suite

```
python -m pytest tests/ -q
```

**Result: 5282 passed, 2 failed, 3 skipped, 1 warning**

The 2 failures are pre-existing yfinance-related failures:
- `tests/test_live_data_integration.py::TestProviderFailure::test_yahoo_not_ready_when_no_backend`
- `tests/test_live_data_integration.py::TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`

These failures existed before this checkpoint and are unrelated to authorization.

### What persistence behavior is already covered

**None.** There are no tests for authorization persistence because no persistence exists.

### What lifecycle behavior is covered

- All 6 status values can be constructed ✓
- `is_authorized` property returns True ONLY for AUTHORIZED ✓
- `UNAUTHORIZED`, `ELIGIBLE`, `EXPIRED`, `REVOKED`, `SUPERSEDED` are all NOT authorized ✓
- Fail-closed: malformed records cannot become AUTHORIZED ✓
- Engine returns `authorized=False` for ineligible intents ✓

### What is missing

- No persistence save/load tests
- No lifecycle transition tests (EXPIRED, REVOKED, SUPERSEDED transitions)
- No restart-safety tests
- No corruption-recovery tests
- No integrity-validation tests
- No concurrent-access tests

### What tests should be added in a future implementation checkpoint

When persistence is implemented (next checkpoint):
- Save/load round-trip tests
- Restart-safety tests (save → new process → load → verify state)
- Lifecycle transition tests (AUTHORIZED → EXPIRED, etc.)
- Corruption handling tests (truncated file, invalid JSON, schema version mismatch)
- Integrity validation tests (tampered authorization_id, modified fields)
- Idempotent save tests (same authorization_id → same file)
- Duplicate rejection tests
- Deterministic reload tests (load → serialize → load → same identity)

## 21. Implementation Decision

### Option B — Implement persistence in the next checkpoint

**This is the correct decision.**

The authorization boundary is clean and correct:
- The model is immutable and fail-closed ✓
- The engine is stateless and deterministic ✓
- Identity is deterministic and collision-resistant ✓
- Timestamps are timezone-aware and caller-supplied ✓
- Frozen boundaries are intact ✓
- No execution semantics leak into the model ✓

**But persistence does not exist.** Authorization is an in-memory-only artifact that is lost on process restart. For a system that is intended to have a durable authorization state, this is a critical gap.

### Why not Option A

Option A (no implementation required) would mean accepting that authorization does not survive restart. This contradicts the checkpoint's requirement for "durable, correctly bounded Execution Authorization persistence/lifecycle boundary."

### Why not Option C

No genuine defect was found in the existing code. The model and engine are correct. The gap is missing functionality (persistence), not a bug in existing functionality.

### What the next checkpoint should implement

1. **Authorization serializer** (`execution_authorization_serialization.py`) — follows established pattern
2. **Authorization store** (`authorization_store.py`) — follows PaperTradeStore pattern
3. **Lifecycle transition methods** — create_new_status() that produces a new immutable record with updated status
4. **Integrity validation** on load — recompute `authorization_id` and compare
5. **Restart-safe query** — load all records for an intent, return current state

## 22. Limitations

1. **No persistence exists** — authorization is lost on process restart
2. **No lifecycle transitions** — EXPIRED/REVOKED/SUPERSEDED states cannot be reached
3. **No query mechanism** — cannot look up authorization by intent_id
4. **No restart recovery** — process restart clears all authorization state
5. **No integrity validation on load** — no deserialization code exists to validate
6. **No corruption handling** — no persisted records to corrupt (but this will need design when persistence is added)
7. **No clock abstraction** — timestamps are caller-supplied; no injectable clock for testing time-based transitions
8. **No dashboard integration** — authorization is not exposed in the dashboard
9. **No serialization** — no way to serialize/deserialize `ExecutionAuthorization`

All limitations are by design (deferred to future checkpoints) rather than defects.

## 23. Final Verdict

**PASS WITH LIMITATIONS**

The Execution Authorization model and engine are architecturally correct:
- Immutable, fail-closed, deterministic identity
- Clean separation from intent, planning, paper-trading, and execution boundaries
- All six lifecycle states defined and validated
- No execution semantics, no broker fields, no wall-clock dependency

**The critical gap is the absence of persistence.** Authorization does not survive process restart. This is the primary work item for the next checkpoint.

**Recommended next checkpoint: Checkpoint 15.5 — Execution Authorization Persistence Implementation**

Should implement:
1. Authorization serializer (follows established pattern)
2. Authorization store (filesystem JSON, atomic writes, safe-id, schema-versioned)
3. Lifecycle transition methods (create new immutable records for EXPIRED/REVOKED/SUPERSEDED)
4. Integrity validation on load
5. Query by intent_id
6. Restart-safe behavior

**Do NOT implement:**
- ExecutionCommand
- BrokerAdapter
- Order submission
- Live trading
- Position management
- Portfolio management

The architecture must remain:
```
OperationalTradeIntent
        ↓
ExecutionAuthorization
        ↓
[Persistence — NEXT CHECKPOINT]
        ↓
Future ExecutionCommand
        ↓
Future BrokerAdapter
```
