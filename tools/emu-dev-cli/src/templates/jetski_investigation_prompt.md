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
1. **Diagnostic Log Analysis & Codebase Inspection:**
   - Inspect the downloaded diagnostic logcats, host logs, stacktraces, and thread dumps in file://$sandbox_dir.
   - Locate the source code and BUILD.bazel target for $test_identifier around $module_name.
   - Analyze thread safety, asynchronous futures/promises, condition variables, lifecycle teardown, and race conditions.
2. **Deterministic TDD & Verification (Red -> Green -> Refactor):**
   - **Step 1 (Red - Construct Deterministic Test Case):** If running on a locally supported platform, attempt to write or update a unit test case that **deterministically exposes the underlying race condition/failure** (e.g. via explicit callback synchronization, condition variables, or deterministic thread ordering) so it fails 100% reliably.
   - **Step 2 (Green - Validate Fix):** Apply your proposed synchronization fix. If the test target can be built and executed on your local platform, verify that your new deterministic test passes 100% cleanly. If the target is for an unsupported platform (e.g. macOS/Windows), perform a static concurrency and lifetime audit to verify the fix mathematically.
   - **Step 3 (Refactor):** Ensure clean, production-grade synchronization logic **without using artificial `sleep_for` delays**.
3. **Formulate RCA & Diff:**
   - Design a robust synchronization fix (e.g. mutex, atomic, condition variable, or thread join) and include the deterministic test case in your proposed diff.
4. **Output Contract:**
   Save your final findings to file://$sandbox_dir/rca_summary.md adhering STRICTLY to the following Markdown schema so that the automated parser can extract your findings:

### 🔬 Jetski RCA Analysis: $test_identifier

**Root Cause Summary:** <1-2 sentence concise summary of the root cause / race condition>

**Detailed Explanation:**
<Comprehensive breakdown of the timing anomaly, stacktrace, and fix rationale>

**Proposed Code Patch (Diff):**
```diff
<insert proposed code diff patch here>
```

