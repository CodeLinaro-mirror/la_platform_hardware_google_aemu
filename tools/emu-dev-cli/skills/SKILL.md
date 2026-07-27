---
name: emu_dev_cli
description: CLI tool for fetching prebuilt Android Emulator binaries and system images from Android Build (go/ab), managing local repository checkout directories, creating device AVDs, and launching background/foreground test emulator instances. Use when setting up test instances, reproducing bugs, or verifying CTS.
---

# Android Emulator Developer CLI (`emu-dev-cli`)

`emu-dev-cli` is an agent-first CLI for emulator developers and AI engineering assistants to construct, manage, and verify Android Emulator environments.

---

## 🛠️ Command Reference

### 1. Fetch Artifacts from Android Build (`fetch-build`)

Pulls and extracts prebuilt emulator host binaries or system image archives from `go/ab` into `/tmp/`.

#### A. Fetch Host Emulator (`fetch-build emulator`)
* **Fetch latest build on `emu-main-next` (Default):**
  ```bash
  emu-dev-cli fetch-build emulator --latest
  ```
* **Fetch latest build on `emu-main-dev`:**
  ```bash
  emu-dev-cli fetch-build emulator --latest --branch emu-main-dev
  ```
* **Fetch specific build by ID:**
  ```bash
  emu-dev-cli fetch-build emulator --build-id 15943073
  ```

#### B. Fetch System Image (`fetch-build system-image`)
Auto-detects host CPU architecture (`x86_64` vs `arm64`).

* **Fetch latest system image on `trunk-release` (Default):**
  ```bash
  emu-dev-cli fetch-build system-image --latest
  ```
* **Fetch latest system image on release branch:**
  ```bash
  emu-dev-cli fetch-build system-image --latest --branch 26Q2-emu-release
  ```
* **Fetch specific system image build ID:**
  ```bash
  emu-dev-cli fetch-build system-image --build-id 15900270
  ```

---

### 2. Source Directory Registry (`source-directory`)

Manage mappings between branch names (`emu-main-dev`, `emu-main-next`, `git_main`) and their local source code checkout paths on disk. Stored in `~/.android/emu-dev-cli.json`.

* **Get local repository path for branch:**
  ```bash
  emu-dev-cli source-directory get emu-main-dev
  # Output: /work/emu-main-dev
  ```
* **Set local repository path for branch:**
  ```bash
  emu-dev-cli source-directory set emu-main-dev /work/emu-main-dev
  ```
* **List all configured mappings:**
  ```bash
  emu-dev-cli source-directory list
  ```

---

### 3. Create Android Virtual Devices (`create avd`)

Creates a valid Android Virtual Device (AVD) pointer (`~/.android/avd/<name>.ini`) and hardware profile (`~/.android/avd/<name>.avd/config.ini`) bound to a system-image directory.

#### Available Device Profiles (`--list-profiles`)
`small_phone`, `medium_phone` (default), `medium_tablet`, `small_desktop`, `medium_desktop`, `large_desktop`.

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

Launches a prebuilt `emulator` executable. Auto-configures Linux dynamic shared library paths (`LD_LIBRARY_PATH` for Qt, Vulkan, and GLES) and verifies X11 `DISPLAY` sockets (`DISPLAY=:20` on CRD).

* **Dry-run verification (no process spawned):**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    --dry-run \
    -- -avd my-dev-phone
  ```
* **Launch interactive foreground emulator:**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    -- -avd my-dev-phone
  ```
* **Launch detached daemon background emulator:**
  ```bash
  emu-dev-cli launch emulator \
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \
    --detached \
    -- -avd my-dev-phone -no-window
  ```

---

### 5. Onboarding Initialization (`init`)

Installs global agent skills into `~/.gemini/` and launches interactive workspace source path setup if `~/.android/emu-dev-cli.json` is missing:
```bash
emu-dev-cli init
```
