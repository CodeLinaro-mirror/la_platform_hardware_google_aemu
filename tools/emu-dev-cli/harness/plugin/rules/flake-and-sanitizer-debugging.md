---
trigger: model_decision
description: "Consult when investigating, reproducing, or fixing flaky unit/E2E tests, ThreadSanitizer (TSAN) races, AddressSanitizer (ASAN) errors, or Windows RBE test failures."
---

# Flake Reproduction & Sanitizer Debugging Playbook

## 1. Mandatory Reproducibility-First Gate
Never propose a blind or speculative code patch for a flaky test without either:
1. Reproducing the failure locally (or on RBE), OR
2. Proving a deterministic root cause directly from a captured TSAN data-race report or ASAN/SEGV stack trace in Sponge.

## 2. Reproduction Commands by Failure Class
- **General C++ Unit Test Flake (`goldfish`)**:
  ```bash
  bazel test //emulator/path:target_test --runs_per_test=50 --test_output=errors
  ```
- **Concurrency / Data Race (`TSAN`)**:
  ```bash
  bazel test //emulator/path:target_test --config=tsan --runs_per_test=50 --test_output=errors
  ```
- **Memory Corruption / Use-After-Free (`ASAN`)**:
  ```bash
  bazel test //emulator/path:target_test --config=asan --runs_per_test=20 --test_output=errors
  ```
- **Windows-Only Failure (`remote-windows`)**:
  ```bash
  bazel test //emulator/path:target_test --config=remote-windows --runs_per_test=20 --test_output=errors
  ```
- **`emu-dev-cli flakiness` Suite**:
  ```bash
  emu-dev-cli flakiness history <target>
  emu-dev-cli flakiness sponge <invocation_id>
  emu-dev-cli flakiness reproduce <target> --runs 50
  ```

## 3. Common Root Causes & Fixes
- **Sleep-based synchronization (`std::this_thread::sleep_for`)**: Replace with deterministic latches (`absl::Notification`, `std::future`, or `absl::Mutex::Await`).
- **Event loop teardown races**: Ensure `ThreadedEventLoop` tasks complete or are canceled before destroying referenced state.
- **Hardcoded ports or temp paths**: Use OS-assigned ephemeral ports (`port = 0`) and isolated `TempDir` fixtures with non-ASCII Unicode coverage (`"测试_🚀_é"`).
