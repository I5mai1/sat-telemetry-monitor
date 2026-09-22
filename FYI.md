# Project Standards

## Language & Runtime
- Python 3.11+ only. Use modern syntax (`match` statements, `X | Y` union types, `tomllib`, etc.).

## Structure
- Modular package layout — no monolithic scripts. Group code by responsibility (e.g. `ingest/`, `telemetry/`, `ui/`, `common/`).
- One clear responsibility per module; keep files small and cohesive.
- Shared logic belongs in a common/util module, not duplicated across features.

## Type Hinting
- All function signatures (parameters and return types) must be type-hinted.
- Use `typing`/built-in generics (`list[int]`, `dict[str, Any]`, `X | None`) consistently.
- Prefer `dataclasses` or `pydantic` models over untyped dicts for structured data.

## Telemetry Standards
- Telemetry handling follows **ECSS** (European Cooperative for Space Standardization) and **CCSDS** (Consultative Committee for Space Data Systems) conventions.
- Packet structures, field naming, and units should map traceably to the relevant ECSS-E-ST-70-41 (telemetry/telecommand packet utilization) and CCSDS packet standards.
- Preserve raw telemetry field names/IDs from source standards in code/comments where useful for traceability; do not silently rename standard fields.

## General
- No unnecessary abstractions — implement what's needed, not speculative future cases.
- Tests live alongside modules and use `pytest`.
