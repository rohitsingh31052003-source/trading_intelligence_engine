"""Upstox credential provider boundary (Checkpoint 17.7).

This module defines the broker-specific credential provider abstraction that
the adapter-owned :class:`~engine.intelligence.upstox_broker_client.UpstoxBrokerClient`
uses to obtain the current access token for each request.

CRITICAL SAFETY RULES (Checkpoint 17.7):

* The adapter itself NEVER reads the token. The token is obtained ONLY by the
  broker client from an injected credential provider at the network boundary
  (and, in 17.7, the client is a MOCK -- no network, no token ever leaves the
  process).
* The provider MUST NOT contain hard-coded credentials. The concrete
  :class:`EnvironmentUpstoxCredentialProvider` reads a single environment
  variable lazily and returns an empty string when absent (fail closed).
* Tests use fake / dummy credential providers; NO real credential material is
  ever placed in fixtures, exceptions, results, persistence, or logs.
* If credentials are absent the adapter/client FAIL CLOSED
  (``AUTHENTICATION_FAILURE`` before any order-affecting call).
* This module imports ONLY stdlib (``os``). It never imports core domain
  models, never imports network libraries, and never imports a broker SDK.
* The environment variable read here is the SEPARATE execution access-token
  credential (``UPSTOX_EXECUTION_ACCESS_TOKEN``) -- NOT the historical-data
  ``UPSTOX_ANALYTICS_TOKEN``. The execution credential does not exist in this
  checkpoint; the provider simply yields an empty string when unset.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

#: Environment variable name for the (future) execution access token. This is
#: intentionally NOT the historical-data ``UPSTOX_ANALYTICS_TOKEN``: the
#: historical token is a candle-data credential used only by the read-only
#: historical provider, never by execution code.
UPSTOX_EXECUTION_ACCESS_TOKEN_ENV = "UPSTOX_EXECUTION_ACCESS_TOKEN"

#: Token env-var names the client redaction rule scrubs from any error text.
SENSITIVE_TOKEN_ENV_NAMES: tuple[str, ...] = (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
    "UPSTOX_ANALYTICS_TOKEN",
    "UPSTOX_ACCESS_TOKEN",
)


@runtime_checkable
class UpstoxCredentialProvider(Protocol):
    """Broker-specific credential provider protocol.

    Implementations yield the current access token as a plain string. An
    empty string means "no credential available" and the client fails closed.
    """

    def get_access_token(self) -> str:
        """Return the current access token (empty string when unavailable)."""
        ...


class EnvironmentUpstoxCredentialProvider:
    """Concrete provider that reads the execution access token from the env.

    The provider reads the token lazily on every call (so rotation at the
    environment is picked up) and NEVER stores it. When the variable is
    unset/empty the provider returns an empty string and the client fails
    closed. This provider is NOT used by any 17.7 test (tests inject fake
    providers); it exists so the boundary has a concrete default that is
    safe and fail-closed.
    """

    def __init__(self, env_name: str = UPSTOX_EXECUTION_ACCESS_TOKEN_ENV) -> None:
        self._env_name = env_name

    def get_access_token(self) -> str:
        value = os.environ.get(self._env_name, "")
        return value if isinstance(value, str) else ""


class StaticUpstoxCredentialProvider:
    """Deterministic provider holding a caller-supplied token value.

    Used ONLY by tests with deliberately fake token values to prove the
    credential boundary (the token never propagates into results, errors,
    persistence, or logs). It is NOT a production credential source.
    """

    def __init__(self, token: str = "") -> None:
        self._token = token if isinstance(token, str) else ""

    def get_access_token(self) -> str:
        return self._token


#: A provider that always yields no token (fail-closed test double).
class EmptyUpstoxCredentialProvider:
    """Provider that always yields an empty token (fail-closed test double)."""

    def get_access_token(self) -> str:
        return ""


__all__ = [
    "UPSTOX_EXECUTION_ACCESS_TOKEN_ENV",
    "SENSITIVE_TOKEN_ENV_NAMES",
    "UpstoxCredentialProvider",
    "EnvironmentUpstoxCredentialProvider",
    "StaticUpstoxCredentialProvider",
    "EmptyUpstoxCredentialProvider",
]
