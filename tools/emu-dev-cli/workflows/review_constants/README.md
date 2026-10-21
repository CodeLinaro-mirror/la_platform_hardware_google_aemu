# Code Review: Named Constants & Magic Value Reduction Workflow (`review-constants`)

The `review-constants` workflow is a targeted code review workflow designed to audit commits for unexplained magic numbers, magic strings, and raw literals, guiding the agent to replace them with self-documenting named constants and domain enums.

It is part of the modular code review workflow family in `emu-dev-cli`, where individual review workflows focus deeply on specific aspects of code quality (e.g. duplication, modularity, control flow, constants).

---

## 🎯 Focus Areas

1. **Magic Numbers & Unexplained Literals:**
   Identifies hardcoded numeric literals (e.g., timeouts, retry limits, status codes, bitmasks, thresholds, buffer sizes, coordinates) passed into logic or assertions without explanatory names.
2. **Missing or Bypassed Enums:**
   Detects raw integers or strings used where an enum, enum class, or union type exists or should be declared to represent distinct domain states (e.g., network types, signal strengths, lifecycle states).
3. **Repeated Magic Strings:**
   Flags repeated string literals used as event names, IPC channels, or protocol messages that should be centralized into shared constants.
4. **Consistency in Tests:**
   Ensures unit test assertions and fixtures reference the same named constants and enum members rather than duplicating raw numeric values.
5. **Clean Refactoring & Verification:**
   Guides replacing literals with descriptive constants or enums, verifying that tests pass, and amending the commit while preserving its Gerrit `Change-Id`.

---

## 🚀 Usage

### 1. Initialize Workflow Session for HEAD
```bash
emu-dev-cli workflow review-constants init
```

### 2. Review a Specific Commit or Repository
```bash
emu-dev-cli workflow review-constants init --commit <sha_or_ref> --repo <repo_path>
```
