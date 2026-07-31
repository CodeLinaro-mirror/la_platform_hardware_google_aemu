import os
import sys
import shutil
import platform
import subprocess
import shlex
import py_compile
from lib.output import print_result
from commands.source_directory import ensure_codesearch_urls_config

SKILL_MARKDOWN_CONTENT = """---
name: emu_dev_cli
description: CLI tool for fetching prebuilt emulator binaries and system images from Android Build (go/ab), creating AVDs, launching emulator instances, and executing automated CTS-Verifier test modules (e.g. tts, battery_saver, vibrations) on the emulator using 'emu-dev-cli cts run-cts-verifier --module <name>' and discovering modules with '--list-modules'. Use when setting up test instances, reproducing bugs, or running CTS-Verifier tests.
---

# Android Emulator Developer CLI (`emu-dev-cli`)

`emu-dev-cli` is an agent-first CLI for emulator developers and AI engineering assistants to construct, manage, and verify Android Emulator environments.

Executable command:
```bash
emu-dev-cli <command>
```

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

### 5. Automated CTS-Verifier Runner (`cts run-cts-verifier`)

Downloads and executes automated CTS-Verifier test modules (`tts`, `battery_saver`, `vibrations`, `tile_service`, `screen_pinning`, `has_vibrator`, etc.) against an online emulator.

* **Discover available automated test modules (`--list-modules`):**
  Dynamically scans and lists all available CTS-Verifier automation modules (e.g. `tts`, `battery_saver`, `vibrations`, `tile_service`, `screen_pinning`, `has_vibrator`):
  ```bash
  emu-dev-cli cts run-cts-verifier --list-modules
  ```
* **Run specific CTS-Verifier module on emulator (`--module <name>`):**
  Runs the specified module against an active or launched emulator instance:
  ```bash
  emu-dev-cli cts run-cts-verifier --module tts
  ```
* **Run using specific Android Build (`go/ab`) build ID:**
  ```bash
  emu-dev-cli cts run-cts-verifier --build-id 15900270 --module vibrations
  ```

---

### 6. Onboarding Initialization (`init`)

Installs global agent skills into `~/.gemini/` and launches interactive workspace source path setup if `~/.android/emu-dev-cli.json` is missing:
```bash
emu-dev-cli init
```

---

### 7. Query Documentation Paths (`docs`)

Accesses documentation files using configured branch source directory paths in `~/.android/emu-dev-cli.json`:

* **Get path to CTS Verifier automation documentation (`README.md`):**
  ```bash
  emu-dev-cli docs cts-verifier-automation
  # Output: /work/emu-main-next/third_party/adt-infra/goldfish_test/xts/verifier/README.md
  ```

---

### 8. Rebuild & Update Executable (`update`)

Rebuilds `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` via Bazel from the configured local `emu-main-next` source directory and re-installs the compiled release package:

```bash
emu-dev-cli update
```
"""


def detect_default_install_path():
    system = platform.system().lower()
    if system == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        emu_dev_dir = Path(local_appdata) / "Google" / "EmuDevCLI"
        emu_dev_dir.mkdir(parents=True, exist_ok=True)
        return str(emu_dev_dir / "emu-dev-cli.exe")
    else:
        try:
            android_bin = os.path.expanduser("~/.android/bin")
            os.makedirs(android_bin, exist_ok=True)
            return os.path.join(android_bin, "emu-dev-cli")
        except Exception:
            fallback = get_fallback_install_path()
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            return fallback


def get_fallback_install_path():
    system = platform.system().lower()
    if system == "windows":
        return os.path.join(os.path.expanduser("~"), "bin", "emu-dev-cli.exe")
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "emu-dev-cli")


def get_release_package_directory(dest_path):
    """
    Returns the dedicated release package root directory ~/.android/emu-dev-cli/ containing:
      - emu-dev-cli  (C++ native compiled executable)
      - lib/
        - __main__.pyc
        - commands/*.pyc
        - install/*.pyc
        - lib/*.pyc
        - targets/*.pyc
    """
    home_dir = os.path.expanduser("~")
    system = platform.system().lower()
    if system == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(home_dir, "AppData", "Local"))
        return os.path.join(local_appdata, "Google", "EmuDevCLI")
    else:
        return os.path.join(home_dir, ".android", "emu-dev-cli")


def copy_src_to_release_lib(src_dir, release_lib_dir):
    """
    Copies python modules to release/lib/ directory.
    Includes .py source files and cleans up stale .pyc bytecodes.
    """
    os.makedirs(release_lib_dir, exist_ok=True)
    for root, _, files in os.walk(release_lib_dir):
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass

    for root, _, files in os.walk(src_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            rel_path = os.path.relpath(os.path.join(root, f), src_dir)
            dest_py = os.path.join(release_lib_dir, rel_path)
            os.makedirs(os.path.dirname(dest_py), exist_ok=True)
            source_py = os.path.join(root, f)
            if os.path.abspath(source_py) != os.path.abspath(dest_py):
                shutil.copy2(source_py, dest_py)


def resolve_source_directory(source_dir=None):
    if source_dir:
        cand = os.path.join(source_dir, "hardware", "google", "aemu", "tools", "emu-dev-cli", "src")
        if os.path.exists(cand):
            return cand
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_launcher_wrapper(built_bin, dest_path, source_dir=None):
    release_dir = get_release_package_directory(dest_path)
    release_lib_dir = os.path.join(release_dir, "lib")
    os.makedirs(release_dir, exist_ok=True)
    os.makedirs(release_lib_dir, exist_ok=True)

    # 1. Install regular C++ compiled ELF executable into release directory ~/.android/emu-dev-cli/emu-dev-cli
    release_bin = os.path.join(release_dir, "emu-dev-cli")
    if platform.system().lower() == "windows":
        release_bin += ".exe"
    if os.path.exists(release_bin) or os.path.islink(release_bin):
        os.unlink(release_bin)
    shutil.copy2(built_bin, release_bin)
    if platform.system().lower() != "windows":
        os.chmod(release_bin, 0o755)

    # 2. Copy python source code to release/lib/ directory
    src_dir = resolve_source_directory(source_dir)
    copy_src_to_release_lib(src_dir, release_lib_dir)

    # Copy advisor self-contained executable and runfiles if built
    source_workspace = source_dir or get_source_directory("emu-main-next")
    if not source_workspace or not os.path.exists(source_workspace):
        source_workspace = resolve_source_directory(source_dir)

    if source_workspace:
        advisor_src = os.path.join(
            source_workspace,
            "bazel-bin",
            "external",
            "goldfish+",
            "emulator",
            "crashreport",
            "tool",
            "advisor",
            "advisor",
        )
        if os.path.exists(advisor_src):
            bin_dir = os.path.join(release_lib_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            advisor_dest = os.path.join(bin_dir, "advisor")
            if os.path.exists(advisor_dest) or os.path.islink(advisor_dest):
                os.unlink(advisor_dest)
            shutil.copy2(advisor_src, advisor_dest)
            if platform.system().lower() != "windows":
                os.chmod(advisor_dest, 0o755)

            advisor_rf = advisor_src + ".runfiles"
            advisor_rf_dest = advisor_dest + ".runfiles"
            if os.path.exists(advisor_rf):
                if os.path.exists(advisor_rf_dest) or os.path.islink(advisor_rf_dest):
                    if os.path.islink(advisor_rf_dest):
                        os.unlink(advisor_rf_dest)
                    else:
                        shutil.rmtree(advisor_rf_dest)
                try:
                    shutil.copytree(advisor_rf, advisor_rf_dest, symlinks=True)
                except Exception:
                    pass

        # Copy compiled emu-main-next crashreport executable directly to bin/
        exe_suffix = ".exe" if platform.system().lower() == "windows" else ""
        crashreport_src = os.path.join(
            source_workspace,
            "bazel-bin",
            "external",
            "goldfish+",
            "emulator",
            "crashreport",
            "tool",
            f"crashreport{exe_suffix}",
        )
        if os.path.exists(crashreport_src):
            bin_dir = os.path.join(release_lib_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            crashreport_dest = os.path.join(bin_dir, f"crashreport{exe_suffix}")
            if os.path.exists(crashreport_dest) or os.path.islink(crashreport_dest):
                os.unlink(crashreport_dest)
            shutil.copy2(crashreport_src, crashreport_dest)
            if platform.system().lower() != "windows":
                os.chmod(crashreport_dest, 0o755)

    # 3. Create PATH executable symlink at ~/.android/bin/emu-dev-cli pointing to release_bin
    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    if os.path.exists(dest_path) or os.path.islink(dest_path):
        os.unlink(dest_path)

    if os.path.abspath(dest_path) != os.path.abspath(release_bin):
        try:
            os.symlink(release_bin, dest_path)
        except OSError:
            shutil.copy2(release_bin, dest_path)


def install_launcher_with_sudo(built_bin, dest_path, source_dir=None):
    if platform.system().lower() == "windows":
        return False
    release_dir = get_release_package_directory(dest_path)
    release_lib_dir = os.path.join(release_dir, "lib")
    os.makedirs(release_dir, exist_ok=True)
    os.makedirs(release_lib_dir, exist_ok=True)

    release_bin = os.path.join(release_dir, "emu-dev-cli")
    if os.path.exists(release_bin) or os.path.islink(release_bin):
        os.unlink(release_bin)
    shutil.copy2(built_bin, release_bin)
    os.chmod(release_bin, 0o755)

    src_dir = resolve_source_directory(source_dir)
    copy_src_to_release_lib(src_dir, release_lib_dir)

    quoted_release_bin = shlex.quote(release_bin)
    quoted_dest = shlex.quote(str(dest_path))
    cmd = ["sudo", "sh", "-c", f"ln -sf {quoted_release_bin} {quoted_dest} || cp {quoted_release_bin} {quoted_dest}"]
    res = subprocess.run(cmd, check=False)
    return res.returncode == 0


def install_skill(source_dir=None):
    home_dir = os.path.expanduser("~")
    target_dirs = [
        os.path.join(home_dir, ".gemini", "config", "skills", "emu_dev_cli"),
        os.path.join(home_dir, ".gemini", "skills", "emu_dev_cli"),
    ]
    installed_files = []
    
    candidate_skills = []
    if source_dir:
        candidate_skills.append(os.path.join(source_dir, "hardware", "google", "aemu", "tools", "emu-dev-cli", "skills", "SKILL.md"))
    candidate_skills.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills", "SKILL.md"))
    candidate_skills.append(os.path.join(home_dir, ".android", "emu-dev-cli", "skills", "SKILL.md"))

    content_to_write = SKILL_MARKDOWN_CONTENT
    for cand in candidate_skills:
        if cand and os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    content_to_write = f.read()
                break
            except Exception:
                pass

    for target_dir in target_dirs:
        try:
            os.makedirs(target_dir, exist_ok=True)
            skill_file = os.path.join(target_dir, "SKILL.md")
            with open(skill_file, "w", encoding="utf-8") as f:
                f.write(content_to_write)
            installed_files.append(skill_file)
        except Exception:
            pass

    return installed_files


def print_path_instructions(installed_bin_path):
    installed_dir = os.path.dirname(str(installed_bin_path))
    path_dirs = [os.path.normpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    norm_installed_dir = os.path.normpath(installed_dir)

    if norm_installed_dir in path_dirs:
        print(f"  ✓ Directory {installed_dir} is already in your PATH.")
        return

    system = platform.system().lower()
    print("-" * 50)
    print(f"⚠️  To use 'emu-dev-cli' from any terminal, add its directory to your PATH:")
    if system == "windows":
        print(f"\n  In PowerShell, run:")
        print(f'    [Environment]::SetEnvironmentVariable("Path", $env:Path + ";{installed_dir}", "User")')
        print(f"\n  Or in Command Prompt (cmd), run:")
        print(f'    setx PATH "%PATH%;{installed_dir}"')
    else:
        shell_rc = "~/.bashrc"
        user_shell = os.environ.get("SHELL", "")
        if "zsh" in user_shell:
            shell_rc = "~/.zshrc"
        print(f"\n  Run the following command in your terminal:")
        print(f'    echo \'export PATH="{installed_dir}:$PATH"\' >> {shell_rc} && source {shell_rc}')
    print("-" * 50)


def register_parser(subparsers):
    install_parser = subparsers.add_parser(
        "install",
        help="Install emu-dev-cli global launcher and agent SKILL.md into user environment"
    )
    default_path = detect_default_install_path()
    install_parser.add_argument(
        "--path",
        type=str,
        default=default_path,
        help=f"Destination executable path (defaults to '{default_path}')"
    )
    install_parser.set_defaults(func=run_install_cmd)


def run_install_cmd(args):
    json_mode = getattr(args, "json", False)
    dest_path = getattr(args, "path", None) or detect_default_install_path()

    built_bin = os.path.normpath(sys.argv[0])
    if "emu-dev-cli-backend" in built_bin:
        candidate_cc_bin = built_bin.replace("emu-dev-cli-backend", "emu-dev-cli")
        if os.path.exists(candidate_cc_bin):
            built_bin = candidate_cc_bin

    final_installed = None
    try:
        install_launcher_wrapper(built_bin, dest_path)
        final_installed = dest_path
    except PermissionError:
        if install_launcher_with_sudo(built_bin, dest_path):
            final_installed = dest_path
        else:
            fallback = get_fallback_install_path()
            install_launcher_wrapper(built_bin, fallback)
            final_installed = fallback

    skill_files = install_skill()
    ensure_codesearch_urls_config()

    print_result({
        "status": "success",
        "action": "install",
        "summary": f"Successfully installed emu_dev_cli compiled release package and skills",
        "launcher_path": str(final_installed),
        "skill_files": skill_files,
    }, json_mode=json_mode)

    if final_installed:
        print_path_instructions(final_installed)
