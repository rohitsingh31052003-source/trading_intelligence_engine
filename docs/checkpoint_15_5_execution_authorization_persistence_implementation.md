# Checkpoint 15.5 — Execution Authorization Persistence Implementation

## 1. Scope

Implement the **Execution Authorization Persistence layer** for the
Trading Intelligence Engine. This checkpoint makes
`ExecutionAuthorization` records durable across process restarts while
preserving all existing architectural boundaries.

## 2. Previous Checkpoint Context

Checkpoint 15.4 established:

* `ExecutionAuthorization` is immutable (`frozen=True`, `slots=True`)
* `authorization_id` is deterministic (`"auth-" + sha256[:16]`)
* authorization records existed only in memory
* authorization was lost across process restart
* the repository contains filesystem JSON persistence patterns
  (`PaperTradeStore`, `ExperimentPersistence`)
* atomic writes, safe identifiers, schema versioning, Decimal/datetime
  encoding, and corruption handling patterns already exist
* post-`AUTHORIZED` lifecycle transitions are NOT yet implemented
* no execution/broker/order/position/portfolio implementation exists

Checkpoint 15.4 selected **Option B — Implement persistence in
Checkpoint 15.5.**

## 3. Persistence Architecture Selected

**Filesystem JSON with atomic writes** — the same pattern used by
`ExperimentPersistence` (Sprint 11K) and `PaperTradeStore` (Product
Phase 5).

A dedicated `src/engine/persistence/` package was created with:

* `execution_authorization_serialization.py` — deterministic,
  self-describing JSON serialization
* `execution_authorization_store.py` — atomic filesystem store
* `exceptions.py` — typed exception hierarchy

The store follows the exact atomic-write discipline of the existing
stores:

```
tempfile.mkstemp in SAME directory
    → write text
    → flush
    → fsync (best-effort)
    → os.replace (single-filesystem rename, atomic on Windows + POSIX)
```

## 4. Existing Persistence Pattern Reused

The following patterns were directly reused from `ExperimentPersistence`
and `PaperTradeStore`:

| Pattern | Source | Implementation |
|---------|--------|----------------|
| Atomic writes | `ExperimentPersistence._atomic_write` | `tempfile.mkstemp` → write → flush → fsync → `os.replace` |
| Safe-id validation | `_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")` | Same regex, same `_validate_id` pattern |
| Default directory | `Path.cwd() / "experiments"` / `"paper_trades"` | `Path.cwd() / "authorizations"` |
| Schema versioning | `SCHEMA_VERSION = 1` at top of document | `AUTHORIZATION_SCHEMA_VERSION = 1` |
| Integrity check | filename id == stored id | Same check for `authorization_id` |
| Typed exceptions | `ExperimentPersistenceError` hierarchy | `AuthorizationStoreError` hierarchy |
| File suffix | `.json` | `.json` |
| No silent overwrite | `overwrite=False` default | Same, with content-comparison idempotency |

## 5. Exact Files Inspected

* `src/engine/registry/persistence.py` — ExperimentPersistence
* `src/engine/registry/serialization.py` — Experiment serializer
* `src/engine/registry/exceptions.py` — Experiment exceptions
* `src/dashboard/paper_trade_store.py` — PaperTradeStore
* `src/engine/intelligence/paper_trading_serialization.py` — Paper trade serializer
* `src/engine/models/execution_authorization.py` — ExecutionAuthorization model
* `src/engine/intelligence/execution_authorization.py` — ExecutionAuthorizationEngine
* `src/engine/models/operational_trade_intent.py` — OperationalTradeIntent model
* `tests/test_execution_authorization.py` — Model tests
* `tests/test_execution_authorization_engine.py` — Engine tests
* `tests/test_registry.py` — Registry persistence tests
* `tests/test_paper_trading.py` — Paper trade tests

## 6. Files Created

```
src/engine/persistence/__init__.py
src/engine/persistence/exceptions.py
src/engine/persistence/execution_authorization_serialization.py
src/engine/persistence/execution_authorization_store.py
tests/test_execution_authorization_store.py
docs/checkpoint_15_5_execution_authorization_persistence_implementation.md
```

## 7. Files Modified

No existing files were modified. The implementation is entirely additive.

Frozen components verified untouched:

* `src/engine/models/trade_plan.py`
* `src/engine/intelligence/trade_planning.py`
* `src/engine/models/paper_trade.py`
* `src/engine/intelligence/paper_trading.py`
* `src/engine/models/operational_trade_intent.py`
* `src/engine/intelligence/operational_trade_intent.py`
* `src/engine/intelligence/operational_trade_intent_application.py`

## 8. Store API

```python
class ExecutionAuthorizationStore:

    def __init__(self, directory: Path | str | None = None) -> None

    @property
    def directory(self) -> Path: ...

    def path_for(self, authorization_id: str) -> Path: ...

    def save(self, authorization: ExecutionAuthorization,
             *, overwrite: bool = False) -> Path: ...

    def load(self, authorization_id: str) -> ExecutionAuthorization: ...

    def exists(self, authorization_id: str) -> bool: ...

    def list_authorizations(self) -> list[str]: ...

    def delete(self, authorization_id: str) -> None: ...
```

Helper:

```python
def default_authorization_directory() -> Path:
    return Path.cwd() / "authorizations"
```

## 9. Serialization Design

The serialization module (`execution_authorization_serialization.py`)
provides deterministic, self-describing JSON:

* **Schema version**: `AUTHORIZATION_SCHEMA_VERSION = 1` written at the
  top of every document
* **Type tags**: `__enum__` (AuthorizationStatus by name), `__dataclass__`
  (ExecutionAuthorization), `__decimal__` (Decimal as string),
  `__datetime__` (ISO-8601), `__tuple__` (tuples preserved)
* **Canonical JSON**: `json.dumps(payload, sort_keys=True, ensure_ascii=False)`
* **No pickle/eval/exec**: Only `json` + `Decimal` + `datetime`
* **Lossless round-trip**: All fields preserved exactly

Serialization functions:

```python
def serialize_authorization(authorization: ExecutionAuthorization) -> str
def serialize_authorization_bytes(authorization: ExecutionAuthorization) -> bytes
def deserialize_authorization(payload: str) -> ExecutionAuthorization
def parse_authorization_header(payload: str) -> dict[str, Any]
def canonical_authorization_json(authorization: ExecutionAuthorization) -> str
```

## 10. Schema Version

`AUTHORIZATION_SCHEMA_VERSION = 1`

Written at the top of every persisted document:

```json
{
  "schema_version": 1,
  "authorization": { ... }
}
```

The store's `load()` method validates `schema_version` BEFORE
deserializing the model. An unsupported version raises
`UnsupportedAuthorizationSchemaVersionError`.

## 11. Identity Handling

* `authorization_id` is used as the logical persistence identity
* The file name is `{authorization_id}.json`
* `_validate_id()` enforces the safe-character regex
  `^[A-Za-z0-9._-]+$` before any filesystem operation
* On load, the reconstructed `authorization.authorization_id` is
  compared to the file-name id. A mismatch raises
  `AuthorizationIntegrityError`
* No new random identifier is generated; the deterministic
  `"auth-" + sha256[:16]` identity is preserved

## 12. Duplicate Handling

When `save()` is called with `overwrite=False` (the default):

1. If the file does not exist → write normally
2. If the file exists and the new content is **identical** to the
   existing content → idempotent success (returns the path)
3. If the file exists and the new content is **different** → raises
   `AuthorizationIntegrityError`

This ensures that a deterministic collision (two semantically different
authorizations that somehow share the same id) never silently
overwrites the existing record.

With `overwrite=True`, the existing record is always replaced.

## 13. Corruption Handling

The store fails closed on all malformed/unverifiable records:

| Condition | Behavior |
|-----------|----------|
| Malformed JSON | `AuthorizationStoreError` |
| Missing `authorization` key | `AuthorizationStoreError` |
| Non-dict payload (e.g. `[1,2,3]`) | `AuthorizationStoreError` |
| Missing `schema_version` | `UnsupportedAuthorizationSchemaVersionError` |
| Unknown schema version | `UnsupportedAuthorizationSchemaVersionError` |
| Identity mismatch | `AuthorizationIntegrityError` |
| Missing record | `AuthorizationNotFoundError` |
| Truncated JSON | `AuthorizationStoreError` |

A corrupted or unverifiable authorization record is **never** returned
as a valid authorization.

## 14. Atomic-Write Behavior

Every write follows the same pattern as `ExperimentPersistence` and
`PaperTradeStore`:

```python
fd, tmp_name = tempfile.mkstemp(
    prefix=target.name + ".",
    suffix=".tmp",
    dir=str(directory),
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
except Exception:
    if tmp_path.exists():
        tmp_path.unlink()
    raise AuthorizationStoreError(...)
```

* The temp file is in the SAME directory → `os.replace` is a
  single-filesystem rename (atomic on Windows and POSIX)
* No partially written target file is ever visible
* Temp files are cleaned up on any failure path

## 15. Restart Behavior

A fresh `ExecutionAuthorizationStore` instance reading the same
durable record produces an `ExecutionAuthorization` with:

* `authorization_id` unchanged
* `intent_id` unchanged
* `content_fingerprint` unchanged
* `status` unchanged
* `authorized_at`, `valid_from`, `expires_at` unchanged
* `Decimal` values unchanged
* `metadata` unchanged

The file survives store-instance recreation because it is written to
the filesystem.

## 16. Timestamp Handling

* All timestamps (`authorized_at`, `valid_from`, `expires_at`) are
  serialized as ISO-8601 strings via `datetime.isoformat()`
* Timezone information is preserved in the ISO string
* The store NEVER calls `datetime.now()` or `datetime.utcnow()` for
  persisted timestamps
* Timestamps are deserialized via `datetime.fromisoformat()` which
  preserves timezone info

## 17. Fail-Closed Guarantees

The store fails closed on all error conditions:

* **Missing record** → `AuthorizationNotFoundError` (NOT AUTHORIZED)
* **Corrupt record** → `AuthorizationStoreError` (NOT AUTHORIZED)
* **Unknown schema** → `UnsupportedAuthorizationSchemaVersionError`
  (NOT AUTHORIZED)
* **Invalid status** → caught by model `__post_init__` during
  deserialization (NOT AUTHORIZED)
* **Identity mismatch** → `AuthorizationIntegrityError` (NOT
  AUTHORIZED)

No implicit fallback authorization is ever created.

## 18. Lifecycle Limitations

Per Checkpoint 15.4, the following are **NOT** implemented in this
checkpoint:

* No `revoke()` method
* No `expire()` method
* No `supersede()` method
* No automatic lifecycle mutation
* No background expiration workers
* No post-`AUTHORIZED` state transitions

The store simply persists and retrieves the status that already exists
on the immutable authorization record.

## 19. Execution Isolation

The store module and serialization module contain no references to:

* broker adapters
* order placement
* position management
* portfolio management
* paper trading
* trade planning
* market scanner
* historical providers
* dashboard / FastAPI / uvicorn
* yfinance / upstox

The boundary isolation tests verify this programmatically by scanning
the module source for functional imports of forbidden packages.

## 20. Frozen-Boundary Verification

No frozen component files were modified:

* `src/engine/models/trade_plan.py` — untouched
* `src/engine/intelligence/trade_planning.py` — untouched
* `src/engine/models/paper_trade.py` — untouched
* `src/engine/intelligence/paper_trading.py` — untouched
* `src/engine/models/operational_trade_intent.py` — untouched
* `src/engine/intelligence/operational_trade_intent.py` — untouched
* `src/engine/intelligence/operational_trade_intent_application.py` — untouched

All changes are additive new files in `src/engine/persistence/` and
`tests/test_execution_authorization_store.py`.

## 21. Tests

`tests/test_execution_authorization_store.py` — 57 tests across 12
areas:

| Area | Tests | Coverage |
|------|-------|----------|
| Basic persistence | 6 | save, load, exists, missing, path, default/custom directory |
| Round-trip | 11 | All fields, all 6 statuses, Decimal, datetime, timezone, metadata, optional fields |
| Restart | 3 | Fresh store instance, Decimal unchanged, file survives recreation |
| Duplicate handling | 4 | Idempotent identical save, conflicting content raises, overwrite replaces |
| Corruption | 7 | Malformed JSON, missing key, unsupported schema, identity mismatch, truncated, non-dict, missing schema |
| Security | 4 | Path traversal rejected, safe IDs accepted, load/exists traversal rejected |
| Immutability | 3 | Save does not mutate, load returns independent artifact, original unchanged |
| List/Delete | 6 | Empty list, list after save, sorted, delete existing, delete missing, stray files ignored |
| Schema version | 2 | Version carried in document, parse_header |
| Serialization module | 5 | All statuses round-trip, deterministic bytes, unsupported schema, malformed JSON, sorted keys |
| Atomic write | 3 | No temp left, file created, overwrite replaces |
| Boundary isolation | 2 | No execution/broker imports in store or serialization |

## 22. Test Results

### Execution authorization tests (new + existing)

```
python -m pytest tests/test_execution_authorization.py \
                 tests/test_execution_authorization_engine.py \
                 tests/test_execution_authorization_store.py -q
→ 238 passed
```

### Frozen boundary regression

```
python -m pytest tests/test_operational_trade_intent.py \
                 tests/test_operational_trade_intent_engine.py \
                 tests/test_operational_trade_intent_application.py \
                 tests/test_trade_planning.py \
                 tests/test_paper_trading.py \
                 tests/test_paper_trading_operations.py -q
→ 602 passed, 1 warning (pre-existing StarletteDeprecationWarning)
```

### Full suite

```
python -m pytest tests/ -q
→ 5339 passed, 2 failed, 3 skipped, 1 warning
```

The 2 failures are **pre-existing yfinance failures** (confirmed
unchanged from before this checkpoint):

* `tests/test_live_data_integration.py::TestProviderFailure::test_yahoo_not_ready_when_no_backend`
* `tests/test_live_data_integration.py::TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`

These failures are unrelated to Checkpoint 15.5 (no yfinance,
live data, or dashboard code was touched).

## 23. Limitations

* No post-`AUTHORIZED` lifecycle transitions (EXPIRED, REVOKED,
  SUPERSEDED) — deferred to future checkpoint per Checkpoint 15.4
* No execution command, broker adapter, order placement, or portfolio
  management — correctly excluded per architectural boundary
* No dashboard integration for authorization — deferred
* No clock abstraction — caller supplies all timestamps (consistent
  with the rest of the codebase)
* No concurrent access guarantees — single-process, filesystem-JSON
  design (same as `ExperimentPersistence` and `PaperTradeStore`)

## 24. Implementation Decision

The implementation follows the **Option B** selected in Checkpoint 15.4:
implement persistence in Checkpoint 15.5 using the repository's
existing filesystem-JSON atomic-write pattern.

The store is deliberately narrow-scoped:

* It persists and retrieves `ExecutionAuthorization` records
* It does NOT grant authorization
* It does NOT evaluate eligibility
* It does NOT revoke, expire, or supersede authorizations
* It does NOT execute trades or contact brokers

## 25. Final Verdict

**PASS**

The execution authorization persistence layer is correctly implemented,
tested, isolated, durable, deterministic, and fail-closed. All
existing architectural boundaries are preserved. No frozen components
were modified. The full test suite shows no regressions beyond the
two pre-existing yfinance failures.
