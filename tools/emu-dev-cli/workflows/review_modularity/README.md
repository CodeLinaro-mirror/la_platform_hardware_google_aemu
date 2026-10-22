# Code Review: Modularity & Decomposition Workflow (`review-modularity`)

The `review-modularity` workflow is a targeted code review workflow designed to audit commits for monolithic files and functions, guiding the agent to decompose them into cohesive, modular, single-responsibility components.

It is part of the modular code review workflow family in `emu-dev-cli`, where individual review workflows focus deeply on specific aspects of code quality (e.g. duplication, modularity, security, readability).

---

## 🎯 Focus Areas

1. **"God" Functions & Routines:**
   Identifies functions that violate the Single Responsibility Principle by attempting to handle multiple disjoint tasks (e.g., input parsing, I/O, business logic, and output formatting) in a single sprawling block.
2. **Cyclomatic & Cognitive Complexity:**
   Flags routines with excessive nesting levels, complex branching trees, or too many mutable local variables that make the code difficult to follow, reason about, or unit test.
3. **Monolithic Files & Mixed Layers:**
   Detects files that lump together unrelated concerns or mix high-level architectural policies with low-level implementation details without clear separation boundaries.
4. **Clean Decomposition & Testing:**
   Guides decomposing monolithic code into focused helper functions or modular sub-packages, verifying that tests pass, and amending the commit while preserving its Gerrit `Change-Id`.

---

## 🚀 Usage

### 1. Initialize Workflow Session for HEAD
```bash
emu-dev-cli workflow review-modularity init
```

### 2. Review a Specific Commit or Repository
```bash
emu-dev-cli workflow review-modularity init --commit <sha_or_ref> --repo <repo_path>
```
