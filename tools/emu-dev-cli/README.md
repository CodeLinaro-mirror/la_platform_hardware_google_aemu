# Android Emulator Developer CLI (`emu-dev-cli`)

`emu-dev-cli` is a command-line helper tool designed for Android Emulator developers and AI engineering agents to automate fetching prebuilt emulator binaries, setting up virtual device configurations (AVDs), dynamic environment linking, and executing automated test suites (such as CTS and CTS-Verifier).

---

## 🚀 Getting Started

### 1. Build and install global launcher and agent roles/skills

```bash
python3 hardware/google/aemu/agents/setup_agents.py
```

`emu-dev-cli` is installed to user-local executable directories:
* **Linux / macOS (default):** `~/.android/bin/emu-dev-cli` *(Fallback: `~/.local/bin/emu-dev-cli`)*
* **Windows:** `%LOCALAPPDATA%\Google\EmuDevCLI\emu-dev-cli.exe` *(Fallback: `%USERPROFILE%\bin\emu-dev-cli.exe`)*

### 2. Run initial workspace source directory registry setup

```bash
emu-dev-cli init
```

`init` installs the agent skill specification (`SKILL.md`) to:
* `~/.gemini/config/skills/emu_dev_cli/SKILL.md`
* `~/.gemini/skills/emu_dev_cli/SKILL.md`

and prompts for local source code repository paths on your workstation.

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
