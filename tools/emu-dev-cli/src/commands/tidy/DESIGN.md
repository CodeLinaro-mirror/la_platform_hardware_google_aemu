# Design Document: Clang-Tidy Autonomous Suite & Git-Native Automated Refactoring

- **Status:** Approved
- **Author:** AI Pair Programmer / Erwin Jansen
- **Date:** 2026-08-07 (Updated: 2026-08-13)
- **Target Module:** `emu-dev-cli` (`hardware/google/aemu/tools/emu-dev-cli/`)

---

## 1. Executive Summary & Core Objective

The `tidy` subcommand suite in `emu-dev-cli` provides an end-to-end,
compiler-verified platform for analyzing, remediating, and codifying
`clang-tidy` standards across the Android Emulator codebase.

The suite is structured around three canonical verbs:

1. **`tidy check` (Inspection & Querying):** Discovers, parses, and formats
   compiler diagnostics for targets, specific source files, or modified files in
   active git diffs (`--diff`).
2. **`tidy fix` (Remediation & Refactoring):** An autonomous, closed-loop
   semantic refactoring engine capable of safely resolving **Levels 1, 2, 3, and
   4** diagnostics (`readability-identifier-naming`, modernizations, types,
   functions, and public APIs).
3. **`tidy add` (Rule Codification):** Automatically inspects `BUILD.bazel`
   packages and injects verified `clang_tidy_test` declarations once code is
   clean.

Rather than relying on fragile AST re-parsing or brittle in-memory coordinate
tracking, the refactoring engine employs a **Compiler-Guided Oracle Loop** with
**Git-Native Transactions**:

- Uses the C++ compiler (`bazel build --keep_going`) as the ground-truth oracle
  to uncover broken call sites across the build graph.
- Uses **Git Commit Trailers** as an interruptible, zero-overhead transaction
  log living directly within Git history.
- Provides a robust **Escape Hatch** that allows seamless escalation between
  fast models, deep reasoning models, and human engineers via `--continue`,
  `--status`, and `--abort`.
- Scopes compilation validation to 1-hop reverse dependencies (`rdeps`) to keep
  verification fast.
- Compresses verified refactoring histories into atomic, semantic commits
  satisfying the _Four Pillars of Reviewability_.

---

## 2. Multi-Level Blast-Radius Taxonomy

Diagnostics and fixes are categorized into four progressive levels based on
blast radius:

| Level       | Aliases       | Diagnostic Category                                                                                                                    | Remediation Strategy                                                                                                                                      | Execution Mode       |
| :---------- | :------------ | :------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------- |
| **Level 1** | `1`, `safe`   | Pure modernizations & bugprone fixes (`modernize-use-nullptr`, `google-readability-casting`, explicit constructors, implicit widening) | **Native YAML AST Diffing:** Exact textual byte replacements applied deterministically via `apply_yaml_fixes()` with fastbuild and AI fixup verification. | Instant / In-Process |
| **Level 2** | `2`, `local`  | `readability-identifier-naming` (Local variables, private struct/class members)                                                        | **Compiler-Guided Call-Site Patching:** Seed declaration rename + `fastbuild -k` + Agent patcher + Fastbuild verification.                                | Git-Transaction Loop |
| **Level 3** | `3`, `types`  | `readability-identifier-naming` (Struct, class, enum, typedef types)                                                                   | **Header Propagation Loop:** Type declaration rename + Cross-module call-site updates scoped to package closure and direct consumers.                     | Git-Transaction Loop |
| **Level 4** | `4`, `public` | `readability-identifier-naming` (Public functions, methods, global constants)                                                          | **Public API & Multi-Consumer Refactor:** Declaration update + Consumer/Test patcher + Unit test regression verification.                                 | Git-Transaction Loop |

---

## 3. The 3 Subcommands in Detail

### 3.1 `emu-dev-cli tidy check`

Scans Bazel library targets, report targets, or active workspace diffs and
renders diagnostic summaries:

- **Scope Filtering:** Supports positional targets (e.g.
  `@goldfish//emulator/libs/async:tidy`), `--file <path>` for single-file
  scoping, or `--diff` to restrict checks strictly to modified lines in the
  current git diff.
- **Level Normalization:** Supports both numeric (`--level 1`) and semantic
  names (`--level safe`, `local`, `types`, `public`, `all`).
- **Structured JSON Output:** Passing global `--json` yields structured
  machine-readable JSON for integration into agents, CI scripts, or IDE tooling.

### 3.2 `emu-dev-cli tidy fix`

Runs progressive automated remediation through the multi-tier refactoring
engine:

- **Sequential Level Traversal:** Invocations at higher levels (e.g.
  `--level local` or `--level 2`) automatically execute Level 1 first before
  progressing to Level 2.
- **Transaction Controls:**
  - `--status`: Inspects active Git trailers to report in-flight refactoring
    state.
  - `--continue`: Resumes a paused transaction after manual adjustments or human
    inspection.
  - `--abort`: Instantly rolls back the working tree to the baseline anchor
    commit (`git reset --hard <anchor>`).
- **Regression Verification:** `--test <target>` runs unit test suites after
  each symbol commit to ensure functional invariants are preserved.
- **Model Tiers:** `--model=auto` (default) escalates from `flash` to `pro` upon
  encountering complex compiler diagnostics; `--model=pro` forces deep
  reasoning.

### 3.3 `emu-dev-cli tidy add`

Scans package directories or `BUILD.bazel` files:

- Inspects existing `cc_library` / `cc_test` targets in the package.
- Injects a formatted `clang_tidy_test` rule with proper target dependencies,
  licenses, and visibility.

---

## 4. Git-Native Transaction Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Transaction Anchor                                       │
│    - Record clean baseline commit hash:                     │
│      $A = git rev-parse HEAD                                │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Iterative Step Loop (Per Target Symbol)                  │
│    For each identifier diagnostic in target:                │
│    a. Apply seed declaration rename via byte-offsets        │
│    b. Run `fastbuild --keep_going` to expose broken callers │
│    c. Agent refactors exposed call sites                    │
│    d. Verification Gate:                                    │
│       - Build passes?                                       │
│       - Unit tests pass?                                    │
│       - If YES -> git commit with Tidy-Transaction trailers │
│       - If NO  -> Escalate (Flash -> Pro -> Human) or       │
│                   git reset --hard HEAD                     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Target Level Clean Gate                                  │
│    - Query `tidy check --level N`                           │
│    - Target is 100% clean (0 diagnostics for Level N)       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. History Compression (Squash into 1 Atomic Commit)        │
│    - `git reset --soft $A`                                  │
│    - Format semantic commit:                                │
│        style(<package>): Modernize naming conventions (L<N>)│
│        Bug: 494605718                                       │
│        Test: bazel test <package>:all                       │
│    - `git commit -a -m "..."`                               │
│    - (Optional) `repo upload . --cbr -y`                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Git Commit Trailer Protocol

Transaction state lives directly inside Git commit metadata. No loose lockfiles,
temporary JSON states, or working tree pollutions are generated.

### Step Commit Structure

```git
refactor(sockets): Rename socketSetOption to SocketSetOption

Tidy-Transaction: @goldfish//emulator/libs/sockets:tidy
Tidy-Anchor: 2e5359be8d6c201004b1cdc8c8f31d2a22ce38e3
Tidy-Level: 4
Tidy-Symbol-Old: socketSetOption
Tidy-Symbol-New: SocketSetOption
Tidy-Status: IN_PROGRESS
```

### Git-Native Introspection Commands

- **Check active transaction target**:
  `git log -1 --format="%(trailers:key=Tidy-Transaction,valueonly)"`
- **Retrieve anchor hash**:
  `git log -1 --format="%(trailers:key=Tidy-Anchor,valueonly)"`
- **View all steps in flight**: `git log --oneline <anchor>..HEAD`

---

## 6. The Compiler-Guided Oracle & Prompting Protocol

### Step 1: Seed Declaration Rename

The engine applies the precise byte-level rename at the specific declaration
offset using the clang-tidy `fixes.yaml` replacements. This avoids the severe
fragilities inherent to regex line-based replacements (which fail on inline
comments or substring shadowing).

### Step 2: Ground-Truth Compiler Exposure

The engine runs `BazelRunner.build(compilation_targets, flags=["--keep_going"])`
to capture all compiler errors across the workspace.

### Step 3: Linker & Header Resolution

`CompilerOracle` parses compiler output and maps missing `.o`/`.obj` object
paths back to their originating physical `.cc`/`.cpp` files (normalizing out
Bazel sandbox prefixes).

### Step 4: Structured Agent Prompt

The prompt provided to the agent includes the exact compiler diagnostics:

````markdown
You are performing an automated C++ semantic refactoring in Google Goldfish
Emulator.

### Objective

Rename symbol: `{old_symbol}` -> `{new_symbol}` Reason: Clang-tidy rule
`{rule_name}` (Level {level} Identifier Naming).

### Current State

The declaration has been renamed in `{decl_file}:{decl_line}`. Running
`mise fastbuild --keep_going` identified the following broken call sites across
the codebase:

### Compiler Diagnostics

```text
{compiler_errors}
```

### Instructions

1. Update each broken call site in the listed files from `{old_symbol}` to
   `{new_symbol}`.
2. Preserve all existing formatting, docstrings, and comments.
3. Do not modify unrelated code or change any functional behavior.
4. Verify your edits cover all files mentioned in the compiler errors above.
````

---

## 7. Scoped Dependency Resolution (`1-Hop rdeps`)

Rebuilding the entire repository (`//...` or `@goldfish//...`) for every single
symbol rename is prohibitively slow.

[`target_resolver.py`](../../lib/target_resolver.py)
resolves a minimal, sufficient verification set:

- **Levels 1 & 2:** Scoped strictly to the package closure (`f"{pkg}:all"`).
- **Levels 3 & 4:** Queries Bazel `cquery` for direct 1-hop reverse dependencies
  (`rdeps(@goldfish//..., <target>, 1)`), capturing all immediate consumers
  without traversing the entire transitive closure.

---

## 8. Hermetic Sandbox Support & Native YAML Parsing

In hermetic Bazel sandbox environments, external dependencies like `PyYAML` may
not be present in the Python runtime.

[`lib/tidy_runner.py`](../../lib/tidy_runner.py)
implements a self-contained fallback parser (`_parse_fixes_yaml_native`). If
`import yaml` fails with `ModuleNotFoundError`, it safely parses the Clang-Tidy
YAML export format natively, ensuring that `emu-dev-cli` tests and commands
execute hermetically inside Bazel test sandboxes.

---

## 9. Dependency Graph Planning & Batched Engine

For large libraries with hundreds of diagnostics, sequential $O(N)$ symbol
iteration can be accelerated through dependency graph analysis:

- **`DependencyPlanner`
  ([`lib/planner.py`](../../lib/planner.py)):**
  Ingests all diagnostics and partitions them into disjoint, independent
  clusters based on file locality and AST scopes.
- **`BatchedRefactoringEngine`
  ([`lib/batched_engine.py`](../../lib/batched_engine.py)):**
  Applies batches of non-interfering seed renames simultaneously, drastically
  reducing the number of required compilation cycles while preserving
  transactional safety.

---

## 10. Escalation Ladder & Human Escape Hatch

```
┌────────────────────────────────────────────────────────┐
│ Tier 1: Fast Execution (Flash / Local Subagent)        │
│ - Seed rename + call-site update + fastbuild           │
└───────────────────────────┬────────────────────────────┘
                            │ (Fails after 2 iterations)
                            ▼
┌────────────────────────────────────────────────────────┐
│ Tier 2: Model Escalation (Pro / Deep Reasoning Agent)  │
│ - Automatically escalates to Gemini Pro                │
│ - Analyzes macro collisions and template instantiations│
└───────────────────────────┬────────────────────────────┘
                            │ (Still fails or test breaks)
                            ▼
┌────────────────────────────────────────────────────────┐
│ Tier 3: Human Escape Hatch (Pause & Prompt)            │
│ - Leaves repository cleanly committed at last good step│
│ - Prints exact symbol & broken file list               │
│ - User runs: emu-dev-cli tidy fix --continue           │
└────────────────────────────────────────────────────────┘
```

---

## 11. Component Breakdown in `emu-dev-cli`

```
hardware/google/aemu/tools/emu-dev-cli/src/
├── commands/
│   └── tidy/
│       ├── DESIGN.md           # This document
│       ├── __init__.py         # CLI argument registration & help epilogs
│       ├── check_cmd.py        # tidy check (diagnostic scanner & JSON formatter)
│       ├── fix_cmd.py          # Thin CLI coordinator, argument mapping, and status inspection
│       └── add_cmd.py          # Injects clang_tidy_test into BUILD.bazel
└── lib/
    ├── agent_dispatcher.py     # Dispatches subagents with structured prompts & model selection
    ├── batched_engine.py       # BatchedRefactoringEngine executing parallelized clusters
    ├── bazel.py                # BazelRunner with fastbuild and keep_going support
    ├── compiler_oracle.py      # Parses compiler error streams into structured diagnostics
    ├── git_transaction.py      # Manages Anchor, Step Commits, Trailers, and Squashing
    ├── level1_engine.py        # Level1RefactoringEngine executing AST replacements & verification
    ├── patch_verifier.py       # Fast build and test verification gates
    ├── planner.py              # DependencyPlanner for diagnostic clustering and batching
    ├── target_resolver.py      # Scoped target discovery & 1-hop reverse dependencies (rdeps)
    ├── tidy_engine.py          # GitNativeRefactoringEngine executing transactional LLM loops
    └── tidy_runner.py          # Diagnostic classification (L1..L4) & hermetic YAML parsing
```
