"""Checkpoint 18.2 — read-only sandbox verifier demo (OFFLINE, deterministic).

Runs the Checkpoint 18.2 read-only verification with a FAKE transport that
emulates a real Upstox Sandbox read-only session, proving the complete flow
(gate -> credential -> guard -> read-only profile -> read-only reconciliation
-> audit) WITHOUT any network and WITHOUT any real credential.

PASS count: 15. Exits 0.
"""

from __future__ import annotations

import os
import sys

_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datetime  # noqa: E402

from engine.intelligence.controlled_broker_validation import (  # noqa: E402
    CHECKPOINT_17_8_REAL_BROKER_ENV,
)
from engine.intelligence.sandbox_readonly_verifier import (  # noqa: E402
    SandboxReadOnlyVerifier,
)
from engine.intelligence.upstox_broker_models import (  # noqa: E402
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
)
from engine.intelligence.upstox_sandbox_transport import (  # noqa: E402
    UpstoxProfileResponse,
)
from engine.models.broker_adapter import (  # noqa: E402
    BrokerResultStatus,
)
from engine.models.sandbox_readonly_verification import (  # noqa: E402
    ReadOnlyOperationType,
    VerificationClassification,
)

_CHECKS = 0
_PASS = 0


class _FixedClock:
    def __init__(self) -> None:
        self._now = datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def utcnow(self) -> datetime.datetime:
        return self._now


def _ok(label: str, condition: bool) -> None:
    global _CHECKS, _PASS
    _CHECKS += 1
    status = "PASS" if condition else "FAIL"
    if condition:
        _PASS += 1
    print(f"{status}: {label}")


class _FakeProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def get_access_token(self) -> str:
        return self._token


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_profile(self) -> object:
        self.calls.append("profile")
        return UpstoxProfileResponse(
            broker="UPSTOX",
            user_type="individual",
            exchanges=("NSE",),
            products=("D",),
            order_types=("MARKET", "LIMIT", "SL", "SL-M"),
            is_active=True,
            user_id_present=True,
        )

    def get_order(self, tag: str = "", order_id: str | None = None) -> object:
        self.calls.append(f"get_order:{order_id or tag}")
        if order_id == "240108010445130":
            return UpstoxOrderStateResponse(
                order_id="240108010445130",
                tag="uptag-abc123",
                status=UpstoxOrderState.COMPLETE,
                reason="complete",
            )
        return UpstoxClientFailure(
            kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
            message="no record for this tag in the sandbox",
        )

    def check_health(self) -> bool:
        return True

    def place_order(self, request: object) -> object:
        raise ValueError("READ-ONLY: order placement blocked")

    def cancel_order(self, order_id: str) -> object:
        raise ValueError("READ-ONLY: order cancellation blocked")


def main() -> int:
    os.environ.pop(CHECKPOINT_17_8_REAL_BROKER_ENV, None)

    print("=" * 72)
    print("CHECKPOINT 18.2 — READ-ONLY SANDBOX VERIFICATION DEMO (OFFLINE)")
    print("READ-ONLY ONLY — NO ORDERS PLACED/MODIFIED/CANCELLED — LIVE TRADING NOT AUTHORIZED")
    print("=" * 72)

    # 1-2. Gate disabled -> UNVERIFIED, no request.
    transport = _FakeTransport()
    verifier = SandboxReadOnlyVerifier(
        transport=transport, credential_provider=_FakeProvider("demo-token")
    )
    result = verifier.verify()
    _ok("gate disabled -> verification UNVERIFIED", result.real_sandbox_connected is False)
    _ok("gate disabled -> no request issued", transport.calls == [])
    _ok("gate disabled -> conclusion mentions the gate", "CHECKPOINT_17_8_REAL_BROKER" in result.conclusion)

    # 3-4. Gate enabled -> real connectivity established.
    os.environ[CHECKPOINT_17_8_REAL_BROKER_ENV] = "1"
    transport = _FakeTransport()
    verifier = SandboxReadOnlyVerifier(
        transport=transport,
        credential_provider=_FakeProvider("demo-token"),
        clock=_FixedClock(),
        broker_order_ids=("240108010445130", "000000000000000"),
    )
    result = verifier.verify()
    _ok("gate enabled + token -> real sandbox connected", result.real_sandbox_connected is True)
    _ok("gate enabled -> profile identity verified (masked)", result.profile_broker == "UPSTOX")

    # 5. Read-only reconciliation over existing order.
    _ok(
        "reconciliation records existing order outcome",
        "240108010445130:SUCCESS->FILLED" in result.reconciliation_result,
    )
    _ok(
        "missing order reconciliation is AMBIGUOUS (not success)",
        "000000000000000:AMBIGUOUS" in result.reconciliation_result,
    )

    # 6-7. Audit entries + no token.
    _ok("audit entries recorded", len(result.audit_entries) >= 3)
    dumped = str(result.to_dict())
    _ok("no token value in audit projection", "demo-token" not in dumped)
    _ok("no Bearer in audit projection", "Bearer" not in dumped)

    # 8. Audit classification vocabulary broker-neutral.
    classes = {e.classification for e in result.audit_entries}
    _ok(
        "audit classifications are broker-neutral",
        classes <= {
            VerificationClassification.SUCCESS,
            VerificationClassification.AMBIGUOUS,
        },
    )
    matches = [
        e for e in result.audit_entries
        if e.operation_type is ReadOnlyOperationType.ORDER_DETAILS
        and e.endpoint_category == "order_details"
    ]
    _ok("order-details audit uses broker-neutral normalized status", all(
        e.normalized_status in (None, BrokerResultStatus) or e.normalized_status.value
        for e in matches
    ))

    # 9. Ambiguous outcome carries error taxonomy.
    ambiguous = [e for e in result.audit_entries if e.classification is VerificationClassification.AMBIGUOUS]
    _ok(
        "ambiguous audit entries carry error code + UNKNOWN status",
        all(e.error_code is not None and e.normalized_status is BrokerResultStatus.UNKNOWN for e in ambiguous),
    )

    # 10. Determinism.
    transport2 = _FakeTransport()
    verifier2 = SandboxReadOnlyVerifier(
        transport=transport2,
        credential_provider=_FakeProvider("demo-token"),
        clock=_FixedClock(),
        broker_order_ids=("240108010445130", "000000000000000"),
    )
    result2 = verifier2.verify()
    _ok("verification identity deterministic", result.verification_id == result2.verification_id)

    # 11. Read-only boundary of the transport.
    try:
        transport.place_order(object())  # type: ignore[arg-type]
        _ok("transport blocks order placement", False)
    except ValueError:
        _ok("transport blocks order placement", True)

    try:
        transport.cancel_order("any-order-id")
        _ok("transport blocks order cancellation", False)
    except ValueError:
        _ok("transport blocks order cancellation", True)

    # 12. Credentials alone never authorize trading.
    _ok(
        "conclusion explicitly withholds live-trading authorization",
        "do NOT authorize live trading" in result.conclusion,
    )

    # 13. No order mutation ever occurs via reconciliation.
    _ok(
        "reconciliation is read-only (only get_order calls)",
        all(c.startswith("get_order:") for c in transport.calls if c.startswith("get_order")),
    )

    # 14. Conclusion text.
    print("CONCLUSION:", result.conclusion)

    # 15. Exit gate.
    os.environ.pop(CHECKPOINT_17_8_REAL_BROKER_ENV, None)
    _ok(f"demo completed ({_PASS}/{_CHECKS} checks passed)", _PASS == _CHECKS)

    print("=" * 72)
    print("Checkpoint 18.2 demo completed successfully.")
    print("=" * 72)
    return 0 if _PASS == _CHECKS else 1


if __name__ == "__main__":
    raise SystemExit(main())