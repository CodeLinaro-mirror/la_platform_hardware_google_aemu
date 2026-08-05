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

### 3. Create an Android Virtual Device (AVD)

```bash
# Create an AVD matching official android-cli profile
emu-dev-cli create avd \
  --name my-phone \
  --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ \
  --profile medium_phone \
  --force
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

#### Authentication Protocol & macOS Workstation Handling (`--token`)
Android Test Hub and Android Build internal endpoints require an OAuth2 token with `https://www.googleapis.com/auth/androidbuild.internal`.

On **macOS workstations**, standard `oauth2l fetch` grants public cloud-platform credentials which return `HTTP 403 Forbidden` (`ACCESS_TOKEN_SCOPE_INSUFFICIENT`).

**Agent Execution Protocol when running on macOS (`sys.platform == 'darwin'`):**
1. **Environment Check**: Check if `ANDROID_BUILD_TOKEN` environment variable or `--token <token>` argument is set.
2. **Automated Remote SSH Fetch**: If an SSH connection to a GLinux machine (`$GLINUX_HOST` or `$USER.c.googlers.com`) is available, `OAuthTokenManager` automatically fetches the token via SSH.
3. **Interactive Escalation**: If an auth failure (`HTTP 403`) occurs, the agent interactively prompts the user to run `oauth2l fetch --sso $USER@google.com androidbuild.internal` and pass it via `--token` or `export ANDROID_BUILD_TOKEN="..."`.

```bash
# List flaky test targets from Android Test Hub (across Linux, ASAN, TSAN, Windows, Mac)
emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

# Inspect individual test chronological execution history and pass/fail rates
emu-dev-cli flakiness history --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan

# Fetch diagnostic logs and thread dumps for an invocation
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type HOST_LOG

# Locally reproduce and stress-test flakes under Bazel
emu-dev-cli flakiness reproduce --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --iterations 30

# Triage against Buganizer and generate AI root-cause patches
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --dry-run

# One-command remediation: apply fix, stress-test 50x, and prepare Gerrit CL
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --iterations 50 --upload
```

