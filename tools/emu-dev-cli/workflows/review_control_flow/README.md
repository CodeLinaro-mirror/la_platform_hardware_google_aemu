# Code Review: Control Flow & Nesting Reduction Workflow (`review-control-flow`)

The `review-control-flow` workflow is a targeted code review workflow designed to audit commits for unnecessary control flow complexity, deep nesting, and tangled branching, guiding the agent to flatten logic and introduce guard clauses.

It is part of the modular code review workflow family in `emu-dev-cli`, where individual review workflows focus deeply on specific aspects of code quality (e.g. duplication, modularity, control flow, readability).

---

## 🎯 Focus Areas

1. **The "Pyramid of Doom" (Deep Nesting):**
   Identifies code indented 3+ levels deep where the happy path is buried inside multiple layers of conditionals, loops, and exception blocks.
2. **Missing Guard Clauses & Early Returns:**
   Flags functions that wrap large blocks of logic in positive preconditions rather than returning early or failing fast (`if not valid: return`).
3. **Compound Boolean Complexity:**
   Simplifies convoluted multi-clause conditions and double-negatives into descriptive boolean helper functions or well-named local predicates.
4. **Repetitive Branching Ladders:**
   Converts long chains of `if/elif/else` matching constant keys into clean dictionary or map lookups.

---

## 🚀 Usage

### 1. Initialize Workflow Session for HEAD
```bash
emu-dev-cli workflow review-control-flow init
```

### 2. Review a Specific Commit or Repository
```bash
emu-dev-cli workflow review-control-flow init --commit <sha_or_ref> --repo <repo_path>
```

