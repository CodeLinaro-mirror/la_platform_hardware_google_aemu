import os
import shutil
from lib.output import print_result
from commands.source_directory import run_first_time_setup, CONFIG_FILE_PATH, load_config

SKILL_MARKDOWN_CONTENT = """---
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
emu-dev-cli create avd \\
  --name my-dev-phone \\
  --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ \\
  --profile medium_phone \\
  --force
```

---

### 4. Launch Emulator Instances (`launch emulator`)

Launches a prebuilt `emulator` executable. Auto-configures Linux dynamic shared library paths (`LD_LIBRARY_PATH` for Qt, Vulkan, and GLES) and verifies X11 `DISPLAY` sockets (`DISPLAY=:20` on CRD).

* **Dry-run verification (no process spawned):**
  ```bash
  emu-dev-cli launch emulator \\
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \\
    --dry-run \\
    -- -avd my-dev-phone
  ```
* **Launch interactive foreground emulator:**
  ```bash
  emu-dev-cli launch emulator \\
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \\
    -- -avd my-dev-phone
  ```
* **Launch detached daemon background emulator:**
  ```bash
  emu-dev-cli launch emulator \\
    --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator \\
    --detached \\
    -- -avd my-dev-phone -no-window
  ```

---

### 5. Onboarding Initialization (`init`)

Installs global agent skills into `~/.gemini/` and launches interactive workspace source path setup if `~/.android/emu-dev-cli.json` is missing:
```bash
emu-dev-cli init
```
"""


def register_parser(subparsers):
    init_parser = subparsers.add_parser(
        "init",
        help="Install global agent skill (SKILL.md) and run first-time source directory setup if ~/.android/emu-dev-cli.json is missing"
    )
    init_parser.set_defaults(func=run_init)


def run_init(args):
    json_mode = getattr(args, "json", False)

    # 1. Run first-time setup for ~/.android/emu-dev-cli.json if config is empty or missing
    cfg = load_config()
    if not cfg.get("source_directories"):
        run_first_time_setup(interactive=not json_mode)

    # 2. Install SKILL.md files into user's ~/.gemini directories
    home_dir = os.path.expanduser("~")
    target_dirs = [
        os.path.join(home_dir, ".gemini", "config", "skills", "emu_dev_cli"),
        os.path.join(home_dir, ".gemini", "skills", "emu_dev_cli"),
    ]

    installed_files = []
    source_skill = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills", "SKILL.md")
    content_to_write = SKILL_MARKDOWN_CONTENT
    if os.path.exists(source_skill):
        try:
            with open(source_skill, "r", encoding="utf-8") as f:
                content_to_write = f.read()
        except Exception:
            pass

    for target_dir in target_dirs:
        try:
            os.makedirs(target_dir, exist_ok=True)
            skill_file = os.path.join(target_dir, "SKILL.md")
            with open(skill_file, "w", encoding="utf-8") as f:
                f.write(content_to_write)
            installed_files.append(skill_file)
        except Exception as e:
            pass

    if not installed_files:
        print_result({
            "status": "error",
            "action": "init",
            "summary": "Failed to write SKILL.md into home directory ~/.gemini",
            "error_message": "Could not access ~/.gemini/config/skills/ or ~/.gemini/skills/.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)

    print_result({
        "status": "success",
        "action": "init",
        "summary": "Successfully initialized emu-dev-cli environment and agent skills",
        "config_file": CONFIG_FILE_PATH,
        "installed_locations": installed_files,
    }, json_mode=json_mode)
