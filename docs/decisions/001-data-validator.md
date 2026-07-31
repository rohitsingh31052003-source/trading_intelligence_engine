# EDR-001: Centralized Data Validation

## Status
Accepted

## Context

Market data can originate from multiple providers:
- Yahoo Finance
- Angel One
- Shoonya
- CSV imports

Each provider may return malformed or inconsistent data.

## Decision

Validation will be implemented in a dedicated DataValidator class rather than inside individual providers.

## Consequences

Advantages:
- Single source of truth
- Reusable validation
- Easier testing
- Easier maintenance

Trade-offs:
- One additional processing step before data enters the system