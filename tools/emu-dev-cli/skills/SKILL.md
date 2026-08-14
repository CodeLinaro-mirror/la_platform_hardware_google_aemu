---
name: emu_dev_cli
description: CLI tool for fetching prebuilt emulator binaries and system images from Android Build (go/ab), creating AVDs, launching emulator instances, executing automated CTS-Verifier tests, investigating crashes with LLDB and autonomous AI fixes, querying Android Test Hub (ATH) flakiness, inspecting test execution history, reproducing flaky Bazel tests under ASAN/TSAN/Windows/macOS, triaging Buganizer issues with AI patches, and fixing flaky tests.
---

# Android Emulator Developer CLI (`emu-dev-cli`)

`emu-dev-cli` is an agent-first CLI for emulator developers and AI engineering assistants to construct, manage, and verify Android Emulator environments, investigate and reproduce crashes, as well as monitor, triage, reproduce, and fix flaky emulator tests.


Executable command:

```bash
emu-dev-cli <command>
```

---

## 🛠️ Command Reference

### 1. Fetch Artifacts from Android Build (`fetch-build`)

Pulls and extracts prebuilt emulator host binaries or system image archives from
`go/ab` into `/tmp/`.

#### A. Fetch Host Emulator (`fetch-build emulator`)

- **Fetch latest build on `emu-main-next` (Default):**
  ```bash
  emu-dev-cli fetch-build emulator --latest
  ```
- **Fetch latest build on `emu-main-dev`:**
  ```bash
  emu-dev-cli fetch-build emulator --latest --branch emu-main-dev
  ```
- **Fetch specific build by ID:**
  ```bash
  emu-dev-cli fetch-build emulator --build-id 15943073
  ```

#### B. Fetch System Image (`fetch-build system-image`)

Auto-detects host CPU architecture (`x86_64` vs `arm64`).

- **Fetch latest system image on `trunk-release` (Default):**
  ```bash
  emu-dev-cli fetch-build system-image --latest
  ```
- **Fetch latest system image on release branch:**
  ```bash
  emu-dev-cli fetch-build system-image --latest --branch 26Q2-emu-release
  ```
- **Fetch specific system image build ID:**
  ```bash
  emu-dev-cli fetch-build system-image --build-id 15900270
  ```

---

### 2. Source Directory Registry (`source-directory`)

Manage mappings between branch names (`emu-main-dev`, `emu-main-next`,
`git_main`) and their local source code checkout paths on disk. Stored in
`~/.android/emu-dev-cli.json`.

- **Get local repository path for branch:**
  ```bash
  emu-dev-cli source-directory get emu-main-dev
  # Output: /work/emu-main-dev
  ```
- **Set local repository path for branch:**
  ```bash
  emu-dev-cli source-directory set emu-main-dev /work/emu-main-dev
  ```
- **List all configured mappings:**
  ```bash
  emu-dev-cli source-directory list
  ```

---

### 3. Create Android Virtual Devices (`create avd`)

Creates a valid Android Virtual Device (AVD) pointer
(`~/.android/avd/<name>.ini`) and hardware profile
(`~/.android/avd/<name>.avd/config.ini`) bound to a system-image directory.

#### Available Device Profiles (`--list-profiles`)

`small_phone`, `medium_phone` (default), `medium_tablet`, `small_desktop`,
`medium_desktop`, `large_desktop`.

```bash
# List available device profiles
emu-dev-cli create avd --list-profiles

# Create AVD with medium_phone profile
emu-dev-cli create avd \
  --name my-dev-phone \
  --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ \
  --profile medium_phone \
  --force
```

---

### 4. Launch Emulator Instances (`launch emulator`)

Launches a prebuilt `emulator` executable. Auto-configures Linux dynamic shared
library paths (`LD_LIBRARY_PATH` for Qt, Vulkan, and GLES) and verifies X11
`DISPLAY` sockets (`DISPLAY=:20` on CRD).

- **Dry-run verification (no process spawned):**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    --dry-run \
    -- -avd my-dev-phone
  ```
- **Launch interactive foreground emulator:**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    -- -avd my-dev-phone
  ```
- **Launch detached daemon background emulator:**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    --detached \
    -- -avd my-dev-phone -no-window
  ```

---

### 5. Automated CTS-Verifier Runner (`cts run-cts-verifier`)

Downloads and executes automated CTS-Verifier test modules (`tts`,
`battery_saver`, `vibrations`, `tile_service`, `screen_pinning`, `has_vibrator`,
etc.) against an online emulator.

- **Discover available automated test modules (`--list-modules`):** Dynamically
  scans and lists all available CTS-Verifier automation modules (e.g. `tts`,
  `battery_saver`, `vibrations`, `tile_service`, `screen_pinning`,
  `has_vibrator`):
  ```bash
  emu-dev-cli cts run-cts-verifier --list-modules
  ```
- **Run specific CTS-Verifier module on emulator (`--module <name>`):** Runs the
  specified module against an active or launched emulator instance:
  ```bash
  emu-dev-cli cts run-cts-verifier --module tts
  ```
- **Run using specific Android Build (`go/ab`) build ID:**
  ```bash
  emu-dev-cli cts run-cts-verifier --build-id 15900270 --module vibrations
  ```

---

### 6. Onboarding Initialization (`init`)

Installs global agent skills into `~/.gemini/` and launches interactive
workspace source path setup if `~/.android/emu-dev-cli.json` is missing:

```bash
emu-dev-cli init
```

---

### 7. Query Documentation Paths (`docs`)

Accesses documentation files using configured branch source directory paths in
`~/.android/emu-dev-cli.json`:

- **Get path to CTS Verifier automation documentation (`README.md`):**
  ```bash
  emu-dev-cli docs cts-verifier-automation
  # Output: /work/emu-main-next/third_party/adt-infra/goldfish_test/xts/verifier/README.md
  ```

---

### 8. Rebuild & Update Executable (`update`)

Rebuilds `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` via Bazel from
the configured local `emu-main-next` source directory and re-installs the
compiled release package:

```bash
emu-dev-cli update
```

---

### 9. Crash Investigation & Automated Fixing (`crash`)

Integrates CrashAdvisor minidump diagnostics, normalized stack fingerprinting,
Buganizer issue deduplication across open and closed tickets, older repository build revision checking,
and autonomous AI engineer fixing:

- **Find existing / duplicate Buganizer issues (`find-bug`):** Symbolicates
  crash minidump, parses stack fingerprint (top faulting frame & calling
  functions), queries Buganizer Component 29601 across both open and closed issues,
  identifies if the crash was built against an older repository version than current HEAD,
  and reports whether the bug has already been fixed (`[ALREADY FIXED / RESOLVED ✅]` vs `[OPEN]`):
  ```bash
  emu-dev-cli crash find-bug 05d8356e2f800000
  emu-dev-cli crash find-bug https://crash.corp.google.com/05d8356e2f800000
  ```
- **File or update Buganizer issue (`file-bug`):** Runs CrashAdvisor RCA
  analysis for a Crash ID, creates a new issue in Component 29601 or
  updates an existing issue with looper timelines, stack traces, and older build revision context:
  ```bash
  emu-dev-cli crash file-bug 05d8356e2f800000
  ```
- **Attempt autonomous fix (`autofix`):** Parses RCA actionability YAML for a
  Crash ID, maps target files to local repository checkout, checks if the bug has already
  been resolved in Buganizer or recent git commits on an older build version, and dispatches
  `emu_main_next_engineer` with explicit version guardrails instructing the agent to inspect
  `git log` and current source code before making changes:
  ```bash
  emu-dev-cli crash autofix 05d8356e2f800000
  emu-dev-cli crash autofix 05d8356e2f800000 --dry-run
  emu-dev-cli crash autofix 05d8356e2f800000 --force
  ```
- **Local Crash Reproduction (`reproduce`):** Extracts Build ID, platform,
  and commandline flags from crash metadata, downloads the matching emulator
  binary, and boots under the LLDB interactive debugger:
  ```bash
  emu-dev-cli crash reproduce 05d8356e2f800000 --lldb
  emu-dev-cli crash reproduce 05d8356e2f800000 --dry-run
  ```
- **Unified Crash Analysis (`analyze`):** Runs symbolication, stack
  fingerprinting, older repository build evaluation, and interactive or automated RCA investigation:
  ```bash
  emu-dev-cli crash analyze 05d8356e2f800000 --file-bug --autofix
  ```



---

### 10. Automated Flakiness Suite (`flakiness`)

The `flakiness` command suite is a closed-loop system for AI agents and developers to monitor, investigate, reproduce, triage, and fix flaky emulator tests across all CI pipelines and target platforms.

#### Target Platform Matrix (`--target`)
| Target Name | Description | Local Bazel Flags |
| :--- | :--- | :--- |
| `emulator_linux_x64` (default) | Standard Linux x86_64 host build | `bazel test <test>` |
| `emulator_linux_x64_asan` | AddressSanitizer memory safety build | `bazel test <test> --config=asan` |
| `emulator_linux_x64_tsan` | ThreadSanitizer data-race build | `bazel test <test> --config=tsan` |
| `emulator_windows_x64` | Windows x86_64 host build | `bazel test <test> --config=rbe-win-x64` (on Linux via RBE) or `--config=windows` (on Windows) |
| `emulator_mac_aarch64` | Apple Silicon ARM64 host build | `bazel test <test> --config=macos_arm64` |
| `all` | Aggregates runs across all 5 platforms | *(Queries all CI targets)* |

#### Query Modes (`--mode`)
* `all` (default): Examines both pre-submit Gerrit tryjobs and post-submit merged CI runs.
* `presubmit`: Restricts queries to pre-submit tryjob builds (`P...`).
* `postsubmit`: Restricts queries to merged post-submit CI builds.

#### Authentication Protocol & macOS Workstation Handling (`--token`)
Android Test Hub and Android Build internal endpoints require an OAuth2 token with `https://www.googleapis.com/auth/androidbuild.internal`.

On **macOS workstations**, standard `oauth2l fetch` grants public cloud-platform credentials which return `HTTP 403 Forbidden` (`ACCESS_TOKEN_SCOPE_INSUFFICIENT`).

**Agent Execution Protocol when running on macOS (`sys.platform == 'darwin'`):**
1. **Environment Check**: Check if `ANDROID_BUILD_TOKEN` environment variable or `--token <token>` argument is set.
2. **Automated Remote SSH Fetch**: If an SSH connection to a GLinux machine (`$GLINUX_HOST` or `$USER.c.googlers.com`) is available, `OAuthTokenManager` automatically fetches the token via SSH.
3. **Interactive Escalation**: If an auth failure (`HTTP 403`) occurs, the agent MUST interactively ask the user to run:
   ```bash
   oauth2l fetch --sso $USER@google.com androidbuild.internal
   ```
   and either set `export ANDROID_BUILD_TOKEN="<token>"` or supply `--token "<token>"`.

---


#### 10.1 List Flaky Tests (`flakiness list`)
Scans Android Test Hub (ATH) / AnTS invocations over a time window and aggregates test failure rates into an aligned Markdown table (or JSON with `--json`).

```bash
# List flaky tests on TSAN with failure rate >= 10% across the last 7 days
emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

# List all flaky tests across all 5 target platforms combined
emu-dev-cli flakiness list --target all --mode all --min-flake-rate 5.0

# Machine-readable output for programmatic agent parsing
emu-dev-cli --json flakiness list --target emulator_linux_x64_tsan
```

---

#### 10.2 Inspect Individual Test History (`flakiness history`)
Retrieves chronological execution records for a specific test target, showing pass/fail status, timestamps, build IDs, invocation IDs, target platforms, and direct clickable Fusion2 links.

```bash
# Query execution history for hal_plug_adapter_unittests on TSAN
emu-dev-cli flakiness history \
  --test @@goldfish+//emulator/plugin/hal/plug:hal_plug_adapter_unittests \
  --target emulator_linux_x64_tsan \
  --days 7 \
  --limit 25

# Query history across all platforms to compare Linux vs Windows vs Mac
emu-dev-cli flakiness history \
  --test @@goldfish+//emulator/libs/async:loop_handoff_test \
  --target all \
  --days 3
```

---

#### 10.3 Fetch Diagnostic Artifacts (`flakiness fetch-logs`)
Downloads logcat files, host stdout/stderr logs, thread dumps, and Perfetto traces for any invocation ID (`I...`).

```bash
# Fetch host log for an invocation
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type HOST_LOG

# Fetch all logs and traces to a custom directory
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type ALL --out-dir /tmp/test_logs/
```

---

#### 10.4 Reproduce Flakes Locally (`flakiness reproduce`)
Executes the target's Bazel test runner with targeted sanitizers/configs and `--runs_per_test=N` to stress-test and locally reproduce non-deterministic failures.

##### Windows Test Flakiness on Linux (`--config=rbe-win-x64`)
Windows unit tests can be compiled, stress-tested, and reproduced directly on Linux host workstations via Remote Build Execution (RBE) using `--config=rbe-win-x64`. `emu-dev-cli` automatically maps `emulator_windows_x64` to `--config=rbe-win-x64` when executing on Linux hosts.

```bash
# Stress-test TSAN data races (runs test 30 times in loop)
emu-dev-cli flakiness reproduce \
  --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests \
  --target emulator_linux_x64_tsan \
  --iterations 30

# Stress-test memory safety under ASAN
emu-dev-cli flakiness reproduce \
  --test @goldfish//emulator/videobridge:in_process_media_provider_test \
  --target emulator_linux_x64_asan \
  --iterations 50

# Stress-test Windows host test target 30x directly on Linux workstation via RBE
emu-dev-cli flakiness reproduce \
  --test @goldfish//emulator/libs/async:loop_handoff_test \
  --target emulator_windows_x64 \
  --iterations 30
```

---

#### 10.5 Automated Bug Triage & AI Patch Proposal (`flakiness triage`)
Cross-references detected flaky tests against existing open Buganizer issues in component `1016880`. For newly discovered flakes, generates an AI root-cause hypothesis and proposed unified diff patch.

```bash
# Preview triage results and AI patch proposals without creating bugs
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --dry-run

# Automatically file Buganizer issues with AI patches for unfiled flakes
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --auto-file
```

---

#### 10.6 One-Command Fix & Verify (`flakiness fix`)
End-to-end remediation workflow: fetches the AI patch and stack trace from Buganizer, applies the code change to the local workspace, verifies with a 50x stress-test reproduction run, and optionally uploads a Gerrit CL.

```bash
# Dry run verification of bug fix
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --dry-run

# Apply fix, stress-test 50x, and upload Gerrit CL
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --iterations 50 --upload
```

---

### 11. Common Natural Language Requests & Agent Action Mapping

| User Request | Target Command |
| :--- | :--- |
| *"Please do an analysis of all the flaky tests of today on the linux tsan target and file bugs for failing tests for which no bug exists"* | `emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --days 1 --auto-file` |
| *"Show me what bugs would be filed for today's TSAN flaky tests without modifying Buganizer"* | `emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --days 1 --dry-run` |
| *"List all flaky tests across all platforms with flake rate > 5% over the past 7 days"* | `emu-dev-cli flakiness list --target all --days 7 --min-flake-rate 5.0` |
| *"Show me the run history and failure status for loop_handoff_test on TSAN"* | `emu-dev-cli flakiness history --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_linux_x64_tsan --days 7` |
| *"Download the failure logs for invocation I99100010599127182"* | `emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type ALL` |
| *"Reproduce hal_plug_adapter_unittests locally under TSAN"* | `emu-dev-cli flakiness reproduce --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --iterations 30` |
| *"Reproduce and stress-test Windows flaky test on Linux"* | `emu-dev-cli flakiness reproduce --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_windows_x64 --iterations 30` *(uses RBE `--config=rbe-win-x64` automatically on Linux)* |
| *"Fix flaky test bug b/123456789 and upload the CL"* | `emu-dev-cli flakiness fix --bug 123456789 --iterations 50 --upload` |


---

## 🤖 Agent Autonomous Remediation Protocol

When an AI pair programmer or autonomous agent is tasked with fixing a flaky test, follow this standard loop:

1. **Audit & Identify:** Run `emu-dev-cli flakiness list --target <target>` to find the worst offenders, or `emu-dev-cli flakiness history --test <target>` to inspect failure patterns.
2. **Reproduce:** Execute `emu-dev-cli flakiness reproduce --test <target> --target <target> --iterations 30` to confirm local reproducibility.
3. **Diagnose:** Fetch logs using `emu-dev-cli flakiness fetch-logs --invocation-id <id>` and inspect stack traces (e.g. TSAN data races in `EventLoop` or future/promise lifecycle).
4. **Implement Fix:** Edit source files to eliminate race conditions, add proper mutex guards, or synchronize teardown lifetimes.
5. **Verify:** Re-run `emu-dev-cli flakiness reproduce --test <target> --target <target> --iterations 50` until **0% flake rate (PASSED)** is achieved.
6. **Submit:** Upload the fix to Gerrit with semantic commit tags (`BUG=b/... TAG=agy CONV=...`).

