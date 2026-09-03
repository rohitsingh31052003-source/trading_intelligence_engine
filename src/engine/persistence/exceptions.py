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


# ============================================================
# EXECUTION COMMAND PERSISTENCE EXCEPTIONS (Checkpoint 16.5)
# ============================================================


class CommandStoreError(Exception):
    """Base error for execution command persistence."""


class CommandNotFoundError(CommandStoreError):
    """A requested command was not found in the store."""


class CommandIntegrityError(CommandStoreError):
    """A persisted command failed an integrity check."""


class UnsupportedCommandSchemaVersionError(CommandStoreError):
    """A persisted command uses an unsupported schema version."""


__all__ = [
    "AuthorizationIntegrityError",
    "AuthorizationNotFoundError",
    "AuthorizationStoreError",
    "CommandIntegrityError",
    "CommandNotFoundError",
    "CommandStoreError",
    "UnsupportedAuthorizationSchemaVersionError",
    "UnsupportedCommandSchemaVersionError",
]
