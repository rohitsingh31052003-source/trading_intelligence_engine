"""
Execution authorization persistence — exceptions (Checkpoint 15.5).

Typed exception hierarchy for the authorization persistence layer.
No error is silently swallowed.
"""

from __future__ import annotations


class AuthorizationStoreError(Exception):
    """Base error for authorization persistence."""


class AuthorizationNotFoundError(AuthorizationStoreError):
    """A requested authorization was not found in the store."""


class AuthorizationIntegrityError(AuthorizationStoreError):
    """A persisted authorization failed an integrity check."""


class UnsupportedAuthorizationSchemaVersionError(AuthorizationStoreError):
    """A persisted authorization uses an unsupported schema version."""


__all__ = [
    "AuthorizationIntegrityError",
    "AuthorizationNotFoundError",
    "AuthorizationStoreError",
    "UnsupportedAuthorizationSchemaVersionError",
]
