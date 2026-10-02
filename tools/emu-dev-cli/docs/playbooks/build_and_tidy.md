# Build, Clang-Tidy, and Sanitizer Playbook

**Role:** Operational Playbook  
**Location:** `hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/build_and_tidy.md`

## 1. Clang-Tidy Verification (`mise tidy` / `clang_tidy.py`)

When verifying a stack of commits in `hardware/generic/goldfish` or `hardware/google/aemu`:

- **Single Commit Audit:** Run `clang_tidy.py` against a specific commit SHA from the workspace
  root:
  ```bash
  python3 hardware/generic/goldfish/tools/clang_tidy.py --commit <sha>
  ```
- **Top-Down Stack Audit:** When asked to tidy a commit stack from top (`HEAD`) to bottom:
  1. Audit commits with `clang_tidy.py --commit <sha>` starting at `HEAD` down to the base commit to
     identify which commits have findings.
  2. When applying fixes to earlier commits in the stack, use interactive rebase (`git rebase -i`)
     or fold fixups into the target commits, and re-verify that no rebase conflicts or regressions
     are introduced in descendant commits.
- **Header Filter Rules:** `clang_tidy.py` automatically sets `-header-filter` to files modified in
  the commit. Ensure new headers are self-contained (`#pragma once`, explicit `#include`s for all
  referenced types).

---

## 2. Windows Cross-Compilation via RBE

You do not need a Windows workstation to verify Windows build compatibility (`_WIN32` headers,
MSVC/clang-cl warnings, Win32 handle types):

```bash
bazel build --config=rbe-win-x64 @goldfish//emulator/...
```

- **Common Windows Portability Pitfalls:**
  - Avoid POSIX-only headers (`<unistd.h>`, `<sys/socket.h>`, `<dlfcn.h>`) without `#ifdef _WIN32`
    guards or platform abstractions (`emulator/libs/base`, `emulator/libs/process`).
  - Watch for `ERROR` macro collisions from `<windows.h>` (`#undef ERROR` if needed before logging
    headers).
  - Use `std::filesystem::path` with `.string()` when passing paths to APIs expecting `const char*`
    (on Windows `std::filesystem::path::value_type` is `wchar_t`).

---

## 3. ThreadSanitizer (TSAN) Rules & Suppressions

```bash
bazel test --config=tsan @goldfish//emulator/...
```

- **Startup Options Contract:** TSAN runtime options (`__tsan_default_options`) and suppressions
  (`__tsan_default_suppressions`) **must** be compiled directly into the test binary or loaded at
  process startup before threads spawn. Setting `TSAN_OPTIONS` via `setenv()` inside `main()` after
  the TSAN runtime has already initialized is ignored by the sanitizer runtime.
- **Third-Party Library Suppressions:** Known benign races inside prebuilt third-party libraries
  (e.g., `libvpx`, graphics driver blobs) belong in the centralized TSAN suppression configuration
  rather than ad-hoc `setenv` calls in individual unit tests.

---

## 4. Asynchronous Media & Pipeline Unit Tests

- **Never Use Fixed `sleep_for` Alone to Assert Encoded Output:** Under TSAN or heavy RBE load,
  asynchronous worker threads (such as `VideoRecorder`'s encoding loop or `WebMEncoder`) can take
  significantly longer than wall-clock sleeps (`50ms`–`100ms`) to dequeue and encode frames.
- **Deterministic Synchronization Pattern:**
  1. Push frames through the `DisplaySource` / producer.
  2. Poll or wait on a condition/frame counter (with a generous timeout, e.g., 5 seconds) until the
     consumer/encoder has processed the expected frame count **before** calling `stop()` or
     inspecting the output container.
  3. Ensure `stop()` drains or flushes queued work deterministically before joining the worker
     thread.
