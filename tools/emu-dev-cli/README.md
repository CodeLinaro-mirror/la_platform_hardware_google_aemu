# Android Emulator Developer CLI (`emu-dev-cli`)

`emu-dev-cli` is a command-line helper tool designed for Android Emulator
developers and AI engineering agents to automate fetching prebuilt emulator
binaries, setting up virtual device configurations (AVDs), dynamic environment
linking, executing automated test suites (such as CTS and CTS-Verifier),
querying framework documentation, and self-updating built binaries.

---

## 🚀 Getting Started

### 1. Build and install global launcher and agent roles/skills

```bash
python3 hardware/google/aemu/agents/setup_agents.py
```

`emu-dev-cli` is installed to user-local executable directories:

- **Linux / macOS (default):** `~/.android/bin/emu-dev-cli` _(Fallback:
  `~/.local/bin/emu-dev-cli`)_
- **Windows:** `%LOCALAPPDATA%\Google\EmuDevCLI\emu-dev-cli.exe` _(Fallback:
  `%USERPROFILE%\bin\emu-dev-cli.exe`)_

### 2. Run initial workspace source directory registry setup

```bash
emu-dev-cli init
```

`init` installs the agent skill specification (`SKILL.md`) to:

- `~/.gemini/config/skills/emu_dev_cli/SKILL.md`
- `~/.gemini/skills/emu_dev_cli/SKILL.md`

and prompts for local source code repository paths on your workstation.

### 3. Rebuild & update from local source repository

```bash
emu-dev-cli update
```

`update` rebuilds `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` via
Bazel from your local `emu-main-next` checkout and re-installs the compiled
release package.

> [!NOTE] **Agent Integration:** Once initialized, `emu-dev-cli` skills are
> automatically registered in `~/.gemini/skills/` and
> `~/.gemini/config/skills/`. This makes all `emu-dev-cli` capabilities natively
> visible to and executable by **Gemini** and **Jetski** AI coding assistants
> during developer sessions.

---

## 🛠️ Usage Quick Reference

### 1. Fetch Prebuilt Artifacts from Android Build (`go/ab`)

```bash
# Fetch latest host emulator release archive for emu-main-next
emu-dev-cli fetch-build emulator --latest

# Fetch latest x86_64 or arm64 system-image archive
emu-dev-cli fetch-build system-image --latest
```

### 2. Configure Source Directory Mappings

```bash
# Register a branch checkout folder
emu-dev-cli source-directory set emu-main-next /work/emu-main-next

# List all configured mappings
emu-dev-cli source-directory list
```

### 3. Create an Android Virtual Device (AVD) or Mesh

```bash
# Create an AVD matching official android-cli profile
emu-dev-cli create avd \
  --name my-phone \
  --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ \
  --profile medium_phone \
  --force

# Batch-create a mesh of N isolated AVD instances (e.g. bt-mesh-1, bt-mesh-2)
emu-dev-cli create mesh \
  --prefix bt-mesh \
  --count 2 \
  --profile medium_phone
```

### 4. Launch Emulator Instance

```bash
# Launch interactive graphical emulator with auto-configured library paths
emu-dev-cli launch emulator \
  --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator/ \
  -- -avd my-phone

# Launch background detached emulator daemon
emu-dev-cli launch emulator \
  --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator/ \
  --detached \
  -- -avd my-phone -no-window

# Launch a mesh of N isolated emulator instances with non-overlapping ports and Netsim packet streamer
emu-dev-cli launch mesh \
  --prefix bt-mesh \
  --count 2 \
  --packet-streamer default \
  --no-window
```

### 5. Automated CTS-Verifier Runner

```bash
# Run TTS module using public CDN release (default when --build-id omitted)
emu-dev-cli cts run-cts-verifier --module tts

# Run vibrations module using specific Android Build build ID
emu-dev-cli cts run-cts-verifier --build-id 15900270 --module vibrations

# List all available automated modules
emu-dev-cli cts run-cts-verifier --list-modules
```

### 6. Query Framework Documentation Paths (`docs`)

```bash
# Get full path to CTS Verifier automation guide
emu-dev-cli docs cts-verifier-automation
# Output: /work/emu-main-next/third_party/adt-infra/goldfish_test/xts/verifier/README.md

# Machine-readable JSON output
emu-dev-cli --json docs cts-verifier-automation
```

### 7. Crash Investigation & Automated Fixing (`crash`)

> [!NOTE]
>
> - All `emu-dev-cli crash` subcommands expect a **Crash ID** (e.g.
>   `05d8356e2f800000`) or `go/crash` URL, **not** a Buganizer bug number
>   (`b/...`).
> - **OAuth2 Token Handling:** `emu-dev-cli` automatically manages token
>   acquisition via `oauth2l`. On Linux (GLinux), it uses SSO integration
>   (`oauth2l fetch --sso`). On macOS and Windows, run
>   `oauth2l fetch https://www.googleapis.com/auth/buganizer https://www.googleapis.com/auth/androidbuild.internal`
>   once to initiate browser login, or pass `--token <TOKEN>` / set
>   `$env:BUGANIZER_TOKEN`.

```bash
# Search Buganizer for duplicate/existing bugs matching crash ID stack fingerprint
emu-dev-cli crash find-bug 05d8356e2f800000

# Run RCA and file/update Buganizer issue for a Crash ID
emu-dev-cli crash file-bug 05d8356e2f800000

# Run RCA and dispatch autonomous engineer to fix local repo
emu-dev-cli crash autofix 05d8356e2f800000

# Local reproduction with LLDB attached to qemu-system process
emu-dev-cli crash reproduce 05d8356e2f800000 --lldb

# Run unified crash analysis
emu-dev-cli crash analyze 05d8356e2f800000 --file-bug --autofix
```

---

### 8. Automated Flakiness Suite (`flakiness`)

The `flakiness` command family provides a closed-loop system for developers and
AI agents to discover, investigate, reproduce, triage, and fix flaky emulator
tests across all CI platforms.

#### Target Platform Matrix (`--target`)

| Target Name                    | Description                            | Local Bazel Flags                                           |
| :----------------------------- | :------------------------------------- | :---------------------------------------------------------- |
| `emulator_linux_x64` (default) | Standard Linux x86_64 host build       | `bazel test <test>`                                         |
| `emulator_linux_x64_asan`      | AddressSanitizer memory safety build   | `bazel test <test> --config=asan`                           |
| `emulator_linux_x64_tsan`      | ThreadSanitizer data-race build        | `bazel test <test> --config=tsan`                           |
| `emulator_windows_x64`         | Windows x86_64 host build              | `bazel test <test> --config=rbe-win-x64` (on Linux via RBE) |
| `emulator_mac_aarch64`         | Apple Silicon ARM64 host build         | `bazel test <test> --config=macos_arm64`                    |
| `all`                          | Aggregates runs across all 5 platforms | _(Queries all CI targets)_                                  |

#### Authentication Protocol & macOS Workstation Handling (`--token`)

Android Test Hub and Android Build internal endpoints require an OAuth2 token
with `https://www.googleapis.com/auth/androidbuild.internal`.

On **macOS workstations**, standard `oauth2l fetch` grants public cloud-platform
credentials which return `HTTP 403 Forbidden`
(`ACCESS_TOKEN_SCOPE_INSUFFICIENT`).

**Agent Execution Protocol when running on macOS (`sys.platform == 'darwin'`):**

1. **Environment Check**: Check if `ANDROID_BUILD_TOKEN` environment variable or
   `--token <token>` argument is set.
2. **Automated Remote SSH Fetch**: If an SSH connection to a GLinux machine
   (`$GLINUX_HOST` or `$USER.c.googlers.com`) is available, `OAuthTokenManager`
   automatically fetches the token via SSH.
3. **Interactive Escalation**: If an auth failure (`HTTP 403`) occurs, the agent
   interactively prompts the user to run
   `oauth2l fetch --sso $USER@google.com androidbuild.internal` and pass it via
   `--token` or `export ANDROID_BUILD_TOKEN="..."`.

```bash
# 1. List flaky test targets from Android Test Hub (across Linux, ASAN, TSAN, Windows, Mac)
emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

# 2. Inspect individual test chronological execution history and pass/fail rates
emu-dev-cli flakiness history --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan

# 3. Inspect ResultStore / Sponge / Fusion2 test actions, failure exit codes, timings, and logs
emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26
emu-dev-cli flakiness sponge --test @@goldfish+//emulator/launcher:can_boot_with_minigbm --target emulator_mac_aarch64 --latest-failures 3

# 4. Fetch diagnostic logs, ResultStore action summaries, and thread dumps for an invocation
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type HOST_LOG

# 5. Locally reproduce and stress-test flakes under Bazel (supports RBE for Windows on Linux)
emu-dev-cli flakiness reproduce --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --iterations 30

# 6. Triage against Buganizer and generate AI root-cause patches
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --dry-run

# 7. One-command remediation: apply fix, stress-test 50x, and prepare Gerrit CL
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --iterations 50 --upload
```

---

### 9. Clang-Tidy Progressive Remediation (`tidy`)

`emu-dev-cli tidy` automates progressive Clang-Tidy diagnostic analysis,
transactional refactoring, target discovery, and compliance auditing across
Android Emulator Bazel packages.

#### 9.1 Diagnostic Profiling (`tidy check`)

Runs the Bazel Clang-Tidy aspect (`:tidy_report`) and categorizes diagnostics
across 4 Progressive Remediation Levels:

```bash
# Check all diagnostics up to Level 4 (default)
emu-dev-cli tidy check @goldfish//emulator/plugin/display:tidy

# Check strictly Level 1 (Safe AST fixes)
emu-dev-cli tidy check @goldfish//emulator/libs/sockets:tidy --level 1

# Output structured JSON report
emu-dev-cli tidy check @goldfish//emulator/launcher:tidy --json
```

#### 9.2 Autonomous Transactional Refactoring (`tidy fix`)

Executes the progressive transactional remediation loop with automatic step
isolation:

- **Level 1 (AST Safe)**: Applies Clang-Tidy native byte-offset replacements
  directly.
- **Level 2 (Local Scopes)**: Refactors local variables, private members, and
  internal static constants (`k_snake_case` / `g_snake_case`).
- **Level 3 (Translational Call-Sites)**: Refactors internal function signatures
  and parameter declarations, capturing broken call-sites with `CompilerOracle`.
- **Level 4 (Public API / Global Signatures)**: Refactors exported APIs, scoping
  package closures (`f"{pkg}:all"`) and 1-hop reverse dependencies
  (`cquery rdeps`) to prevent full-repository rebuild latency.
- **Rollback Invariant**: If verification or test gates fail,
  `emu-dev-cli tidy fix` automatically invokes `tx.rollback_step()` so the
  working tree is never left dirty.

```bash
# Run Level 1 AST fixes
emu-dev-cli tidy fix @goldfish//emulator/plugin/display:tidy --level safe

# Run Level 2 local identifier refactoring
emu-dev-cli tidy fix @goldfish//emulator/libs/sockets:tidy --level local

# Continue a paused transaction after manual adjustments
emu-dev-cli tidy fix --continue

# Inspect active transaction status
emu-dev-cli tidy fix --status

# Abort an active transaction and restore anchor commit
emu-dev-cli tidy fix --abort
```

#### 9.3 Package Rule Configuration (`tidy add`)

Scans Bazel packages and configures `clang_tidy_test` rules with proper licenses
and target lists:

```bash
# Add clang_tidy_test rule to a BUILD.bazel package directory
emu-dev-cli tidy add emulator/libs/sockets --target sockets
```

---

## 🤖 Agent Autonomous Remediation & Policy Protocol

When an AI pair programmer or autonomous agent is tasked with fixing Clang-Tidy
warnings or modernizing C++ code:

1. **Prohibition of Manual Source Edits**: Do not perform manual
   search-and-replace, ad-hoc source editing, or whole-file regex substitutions
   across targets.
2. **Profile Diagnostics**: Run `emu-dev-cli tidy check <target> --level <N>` to
   inspect diagnostic counts.
3. **Execute Transactional Fix**: Run
   `emu-dev-cli tidy fix --target <target> --level <N>` to let the tool manage
   AST mutations, `CompilerOracle` call-site discovery, test gate verification,
   and clean step commits.
4. **Rebase Hygiene**: When performing interactive rebases, run `clang-format`
   or `git clang-format` per commit across the stack to maintain style hygiene.
