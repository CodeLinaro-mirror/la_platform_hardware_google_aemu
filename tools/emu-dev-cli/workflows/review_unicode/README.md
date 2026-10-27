# Code Review: Unicode Strings & File Paths Workflow (`review-unicode`)

The `review-unicode` workflow is a targeted code review workflow designed to audit commits for proper cross-platform Unicode string and file path handling and to ensure unit tests exercise non-ASCII UTF-8 strings and file paths.

Because Unicode and file path conventions differ between `emu-main-next` (`platform/hardware/generic/goldfish`) and `emu-main-dev` (`platform/external/qemu`), the workflow first checks which repository is being reviewed and applies the appropriate repository-specific rules.

It is part of the modular code review workflow family in `emu-dev-cli`, where individual review workflows focus deeply on specific aspects of code quality (e.g. duplication, modularity, control flow, constants, unicode).

---

## 🎯 Focus Areas

1. **Repository Detection (`emu-main-next` vs `emu-main-dev`):**
   Identifies the target repository and branch so the right Unicode and filesystem conventions are enforced.
2. **`emu-main-next` (`platform/hardware/generic/goldfish`) Conventions:**
   - UTF-8 everywhere (`std::string`, `std::string_view`) with `std::filesystem::path` (`fs::path`) directly, backed by the Windows `<activeCodePage>UTF-8</activeCodePage>` manifest.
   - `android::base::file` (`//emulator/libs/file`, `goldfish/file/file.h`, `goldfish/file/file_atomic.h`) for filesystem operations instead of raw `fopen`/`open`/`stat` or ad-hoc `std::ifstream`/`std::ofstream` boilerplate.
   - `android::base::System::Get()` (`//emulator/libs/system`, `android/base/system.h`) for environment variables and system directories.
   - `cc_binary_with_manifest` (`//emulator/tools:cc_binary_with_manifest.bzl`) for executable binaries in `BUILD.bazel`.
   - `path.c_str()` / `path.wstring()` or `android::base::Win32UnicodeString` (`//emulator/libs/windows`) at `wchar_t`-only Win32 API boundaries, and `Device::EscapeQemuOptionValue` when formatting paths into comma-separated QEMU CLI options.
3. **`emu-main-dev` (`platform/external/qemu`) & `aemu` Conventions:**
   - `android/utils/file_io.h` (`android_fopen`, `android_open`, `android_stat`, `android_mkdir`, `android_unlink`, `android_chmod`, `android_access`) and `aemu/base/files/PathUtils.h` (`PathUtils::asUnicodePath`) instead of raw narrow `fopen`/`open`/`stat` or `std::ifstream`/`std::ofstream`.
   - `android::base::Win32UnicodeString` (`aemu/base/system/Win32UnicodeString.h`) with wide Win32 `*W` / `_w*` APIs on `_WIN32`, and `android_add_executable` in CMake.
4. **Unicode Unit Test Coverage:**
   Verifies that unit tests for file paths, directories, environment variables, INI files, CLI arguments, and process launching include non-ASCII UTF-8 test cases (e.g. `"täst_中文_🚀"`, `"José"`), extending existing unit test files where possible.
5. **Clean Refactoring & Verification:**
   Guides fixing any Unicode/path issues, adding non-ASCII test coverage, verifying tests pass, and amending the commit while preserving its Gerrit `Change-Id`.

---

## 🚀 Usage

### 1. Initialize Workflow Session for HEAD
```bash
emu-dev-cli workflow review-unicode init
```

### 2. Review a Specific Commit or Repository
```bash
emu-dev-cli workflow review-unicode init --commit <sha_or_ref> --repo <repo_path>
```
