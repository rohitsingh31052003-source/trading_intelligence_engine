#!/usr/bin/env python3
"""Operator CLI: run ONE Checkpoint 18.2 controlled Upstox Sandbox READ-ONLY
verification.

This is a THIN command-line interface over the existing 18.2 read-only
verifier (:class:`~engine.intelligence.sandbox_readonly_verifier.SandboxReadOnlyVerifier`).
It implements NO trading intelligence, NO scoring, NO prediction, NO signal /
geometry / decision / risk / execution logic and NO order-affecting operation.

Usage::

    python scripts/verify_upstox_sandbox_readonly.py
    python scripts/verify_upstox_sandbox_readonly.py \\
        --reconciliation-orders 240108010445130,231019025564798

Environment:

* ``CHECKPOINT_17_8_REAL_BROKER`` must be ``1`` (the repository-wide
  real-broker opt-in gate). Without it the CLI reports UNVERIFIED and exits.
* ``UPSTOX_EXECUTION_ACCESS_TOKEN`` must hold a genuine Upstox SANDBOX access
  token (read lazily by the execution-side provider; never printed).
* ``UPSTOX_ANALYTICS_TOKEN`` is NEVER used by the execution side.
* ``CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS`` (comma-separated) may hold
  PRE-EXISTING sandbox order ids for read-only reconciliation reporting.
  Default: empty -> reconciliation reported NOT VERIFIED.

READ-ONLY ONLY. The sandbox transport blocks place/cancel outright. No order
is created, modified, or cancelled. This CLI does NOT authorize live trading.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    real_broker_integration_enabled,
)
from engine.intelligence.sandbox_readonly_verifier import SandboxReadOnlyVerifier
from engine.intelligence.upstox_credential_provider import (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
)

_RECONCILIATION_ORDER_IDS_ENV = "CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS"

BANNER = (
    "READ-ONLY SANDBOX VERIFICATION ONLY — NO ORDERS ARE PLACED, "
    "MODIFIED, OR CANCELLED — LIVE TRADING IS NOT AUTHORIZED"
)


def _explicit_or_env_order_ids(explicit: str | None) -> tuple[str, ...]:
    if explicit:
        return tuple(part.strip() for part in explicit.split(",") if part.strip())
    raw = os.environ.get(_RECONCILIATION_ORDER_IDS_ENV, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one Checkpoint 18.2 read-only Upstox Sandbox verification."
    )
    parser.add_argument(
        "--reconciliation-orders",
        default=None,
        help="Comma-separated PRE-EXISTING sandbox order ids for read-only "
        "reconciliation (default: $CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per-request timeout in seconds (default 30).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--verifier-module",
        default=None,
        help="Dotted module path of a SandboxReadOnlyVerifier-compatible class "
        "(test seam; OFFICIAL builds must NOT use this).",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be positive.")

    token_present = bool(os.environ.get(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, ""))
    gate = real_broker_integration_enabled()

    if not gate or not token_present:
        missing = []
        if not gate:
            missing.append(f"{CHECKPOINT_17_8_REAL_BROKER_ENV}=1")
        if not token_present:
            missing.append(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV)
        reason = (
            "Read-only verification NOT performed: " + ", ".join(missing) +
            " required (fail closed; no request issued). The historical "
            "UPSTOX_ANALYTICS_TOKEN is NEVER used for execution."
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "UNVERIFIED",
                        "reason": reason,
                        "gate_enabled": bool(gate),
                        "token_available": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(BANNER)
            print(reason)
        return 2

    verifier_class = SandboxReadOnlyVerifier
    if args.verifier_module:
        module_name, _, cls_name = args.verifier_module.rpartition(".")
        module = __import__(module_name, fromlist=[cls_name])
        verifier_class = getattr(module, cls_name)

    verifier = verifier_class(
        credential_provider=__import__(
            "engine.intelligence.upstox_credential_provider",
            fromlist=["EnvironmentUpstoxCredentialProvider"],
        ).EnvironmentUpstoxCredentialProvider(),
        timeout_seconds=args.timeout,
        broker_order_ids=_explicit_or_env_order_ids(args.reconciliation_orders),
    )

    result = verifier.verify()
    view = result.to_dict()

    if args.json:
        print(json.dumps(view, sort_keys=True))
    else:
        print(BANNER)
        print(f"verification_id       : {view['verification_id']}")
        print(f"broker                : {view['broker']}")
        print(f"environment           : {view['environment']}")
        print(f"real_sandbox_connected: {view['real_sandbox_connected']}")
        print(f"gate_passed           : {view['gate_passed']}")
        print(f"token_available       : {view['token_available']}")
        print(f"profile_broker        : {view['profile_broker'] or 'unavailable'}")
        print(f"profile_is_active     : {view['profile_is_active']}")
        print(f"reconciliation        : {view['reconciliation_result']}")
        print(f"audit_entries         : {len(view['audit_entries'])}")
        for entry in view["audit_entries"]:
            print(
                f"  - {entry['operation_type']:<14} "
                f"{entry['endpoint_category']:<16} "
                f"{entry['classification']:<10} "
                f"{entry.get('normalized_status') or '-'}"
            )
        print(f"conclusion            : {view['conclusion']}")
        print(BANNER)

    if result.real_sandbox_connected:
        return 0
    # The verification RAN (gate+token present) but connectivity was not
    # established -> report 1 (an honest non-zero, NOT a silent success).
    return 1


if __name__ == "__main__":
    raise SystemExit(main())