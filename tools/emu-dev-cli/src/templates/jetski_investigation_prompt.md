=== Jetski Flaky Test In-Depth RCA Request ===
Test Target: $test_identifier
Target Platform: $target
Flake Rate: $flake_rate_pct% ($failed_runs/$total_runs failed runs)
Latest Build ID: $latest_build_id
Latest Invocation ID: $latest_invocation_id

Local Diagnostic Artifact Sandbox:
file://$sandbox_dir

Extracted Diagnostic Files:
$log_files_str

Matched Source File:
$matched_src_file

Instructions for Autonomous AI Engineer:
1. **Mandatory Local Reproducibility Check (Hard Gate):**
   - Before attempting any code fix, you MUST verify whether the flake reproduces locally on current `HEAD` (e.g. via `emu-dev-cli flakiness reproduce --test $test_identifier --target $target --iterations 20`, `bazel test --runs_per_test=20`, or the target's local test runner).
   - If the test **does not reproduce** locally after stress runs (or if CI history shows an earlier commit already fixed it), **STOP** and do **NOT** attempt a blind code fix. Set `**Reproducible:** No` in `rca_summary.md`, record the exact commands and pass counts in `**Reproduction Results:**`, and omit the `diff` block.
2. **Diagnostic Log Analysis & Codebase Inspection:**
   - Inspect the downloaded diagnostic logcats, host logs, stacktraces, and thread dumps in file://$sandbox_dir.
   - Locate the source code and BUILD.bazel target for $test_identifier around $module_name.
   - Analyze thread safety, asynchronous futures/promises, condition variables, lifecycle teardown, and race conditions.
3. **Deterministic TDD & Verification (Red -> Green -> Refactor) — Only when Reproducible:**
   - **Step 1 (Red - Construct Deterministic Test Case):** Only after confirming the flake reproduces locally, write or update a unit test case that **deterministically exposes the underlying race condition/failure** (e.g. via explicit callback synchronization, condition variables, or deterministic thread ordering) so it fails 100% reliably.
   - **Step 2 (Green - Validate Fix):** Apply your proposed synchronization fix and verify that your new deterministic test and stress runs pass 100% cleanly on your local platform.
   - **Step 3 (Refactor):** Ensure clean, production-grade synchronization logic **without using artificial `sleep_for` delays**.
4. **Formulate RCA & Diff:**
   - If `**Reproducible:** Yes`, design a robust synchronization fix (e.g. mutex, atomic, condition variable, or thread join) and include the deterministic test case in your proposed diff.
   - If `**Reproducible:** No`, explain why the flake did not reproduce (or which prior commit fixed it) and do not include a code patch.
5. **Output Contract:**
   Save your final findings to file://$sandbox_dir/rca_summary.md adhering STRICTLY to the following Markdown schema so that the automated parser can extract your findings:

### 🔬 Jetski RCA Analysis: $test_identifier

**Reproducible:** <Yes or No>

**Reproduction Results:** <Exact reproduction command(s) executed and pass/fail counts>

**Root Cause Summary:** <1-2 sentence concise summary of the root cause, or why it no longer reproduces>

**Detailed Explanation:**
<Comprehensive breakdown of reproduction check, logs, and fix rationale (if reproducible)>

**Proposed Code Patch (Diff):**
```diff
<insert proposed code diff patch here ONLY when Reproducible is Yes; omit this section when Reproducible is No>
```

