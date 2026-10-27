---
description: "Android Emulator repository conventions, library reuse, cross-platform Unicode file I/O, Semantic Commit rules, and Gerrit guardrails."
trigger: always_on
---

# Android Emulator Custom Agent Harness Rules (`aemu-harness`)

## 1. Repository & Build System Map
- **`emu-main-next` (`platform/hardware/generic/goldfish`)**: Modern `qemu-next` codebase built with **Bazel** (`bazel build //emulator/...`, `bazel test //emulator/...`).
  - Executable C++ binaries MUST use `cc_binary_with_manifest` (`load("//emulator/tools:cc_binary_with_manifest.bzl", "cc_binary_with_manifest")`) so the Windows UTF-8 `activeCodePage` manifest is embedded (`b/572839247`).
- **`emu-main-dev` (`platform/external/qemu`)**: Classic QEMU2 codebase built with **CMake/Ninja** (`android/rebuild.sh`).
- **`studio-main` (`platform/hardware/google/aemu`, `tools/vendor/google/cli`)**: Shared `aemu` base libraries, `emu-dev-cli` (`tools/emu-dev-cli`), and Android Device Hub (`//tools/vendor/google/cli/android-device-hub/...`).

## 2. Mandatory Library Reuse (`//emulator/libs/*`)
Before writing any new utility, helper, test server, parser, or socket wrapper in `goldfish`, check `//emulator/libs/*`:
- **File I/O & UTF-8 Paths**: Use `android::base::file` (`//emulator/libs/file`, `goldfish/file/file.h`) in `goldfish`, or `android/utils/file_io.h` (`android_fopen`, `android_stat`, `PathUtils`) in `external/qemu`. Never pass raw `std::string` paths directly to `fopen`/`std::ifstream`/`std::ofstream` on Windows.
- **INI Parsing**: Use `android::goldfish::IniFile` (`//emulator/libs/ini_file`, `android/goldfish/ini_file.h`).
- **Environment & System Queries**: Use `android::base::System::Get()->EnvGet(...)` (`//emulator/libs/system`, `android/base/system.h`) and `android::base::TestSystem` (`//emulator/libs/system:test-headers`) instead of `std::getenv`.
- **HTTP Servers & Clients**: Use `goldfish::http::WebServer` (`//emulator/libs/http`, `goldfish/http/web_server.h`) and `goldfish::network::HttpClient` (`//emulator/libs/network:http_client`, `goldfish/network/http_client.h`) in both production and unit tests.
- **Async Event Loops & Sockets**: Use `goldfish::async` (`LibuvEventLoop`, `ThreadedEventLoop`, `AsyncSocket`, `UvErrToAbslStatus`) and `goldfish::network::ResolveEndpoints`.

## 3. Code Quality, `absl::StatusOr` (TotW 181), & Error Messages (`go/error-messages`)
- **TotW 181 (`absl::StatusOr`)**: Never use Hungarian `_or` / `maybe_` suffixes (e.g., use `auto config = LoadConfig();` rather than `config_or`). Access via `.ok()` and `*config` or `config->...`.
- **Actionable Error Messages (TotT 607)**: Every `absl::*Error` or `LOG(ERROR)` must state what operation failed, why (underlying OS/library error), the offending value (without leaking secrets), and an actionable next step, using specific canonical `absl::StatusCode` values.
- **Code Complexity Discipline**: Prefer guard clauses (`< 3` nesting levels), small single-purpose functions, named constants/enums instead of magic literals, and unit tests that include non-ASCII Unicode path/string coverage (`"测试_🚀_é"`).

## 4. Semantic Commit Messages & Gerrit Notification Safety
- **Commit Message Format**: All commits must follow `emu-dev-cli workflow create-commit` / `amend-commit` Semantic Commit rules:
  - Subject (`<= 50` chars): `<type>[optional scope]: <Description>` where `<type>` is one of `feat`, `fix`, `build`, `docs`, `chore`, `test`, `refactor`, `perf`, `style`, `<Description>` starts with a capital letter in imperative mood, and has no trailing period.
  - Blank line followed by a mandatory explanatory body (`<= 72` chars per line) explaining *what* and *why* (not just *how*).
  - Required footers: `Bug: <id|N/A>`, `Test: <command>`, and `Change-Id: I...` (plus `BREAKING CHANGE: <description>` when applicable). Never include `Tag=` or `CONV=` tags.
- **Gerrit Notification Guardrail (`b/567559614`)**: Every Gerrit comment, review, or vote mutation must scope notifications to `OWNER` (`--notify=OWNER` or `"notify": "OWNER"`). Prefer `emu-dev-cli gerrit` subcommands.
