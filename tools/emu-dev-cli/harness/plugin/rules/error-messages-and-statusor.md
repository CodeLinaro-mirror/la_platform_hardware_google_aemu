---
trigger: model_decision
description: "Consult when authoring or reviewing absl::Status, absl::StatusOr, LOG(ERROR), or user/CLI error messages to comply with go/error-messages (TotT 607) and TotW 181."
---

# Error Messages (`go/error-messages`) & `absl::StatusOr` (TotW 181) Guide

## 1. Four-Part Actionable Error Message Structure (TotT 607)
Every `absl::*Error(...)`, `Status(...)`, and `LOG(ERROR)` / `LOG(WARNING)` site must include:
1. **What operation failed**: e.g., `"Failed to download skin layout from ..."` or `"Failed to parse AVD config ..."`
2. **Why it failed**: Include the underlying OS, `errno`, `libuv` (`UvErrToAbslStatus`), `curl`, or HTTP status detail.
3. **Offending value & boundary**: Echo the invalid path, port, or parameter value and expected range **without** leaking secrets (never log `Authorization` headers, OAuth tokens, URL query strings with credentials, or raw response bodies).
4. **Actionable next step**: State how the caller or user can fix the problem when a concrete action exists.

## 2. Canonical `absl::StatusCode` Selection
Never collapse distinct failure classes into a single generic code:
- **`INVALID_ARGUMENT`**: Caller supplied malformed local input or invalid flags. Never use `INVALID_ARGUMENT` for malformed data returned by a remote server.
- **`FAILED_PRECONDITION`**: System is not in the required state for the operation (e.g., emulator not booted, missing initialized directory).
- **`NOT_FOUND`**: Requested local file, AVD, or specific resource does not exist.
- **`UNAVAILABLE`**: Transient network error, connection refused, DNS failure, or HTTP `502`/`503`/`504`.
- **`DEADLINE_EXCEEDED`**: Operation or network request timed out.
- **`PERMISSION_DENIED` / `UNAUTHENTICATED`**: HTTP `403` / `401` or OS file permission error.
- **`INTERNAL`**: Unexpected invariant violation or corrupt remote response format.

## 3. `absl::StatusOr` Naming & Access (TotW 181)
- **No Hungarian `_or` / `maybe_` suffixes**:
  - Bad: `auto config_or = LoadConfig(path);`
  - Good: `auto config = LoadConfig(path);`
- **Single variable & pointer-like access**:
  - Check `if (!config.ok()) { return config.status(); }` and then access via `*config` or `config->field` (or transfer ownership via `*std::move(config)`).
