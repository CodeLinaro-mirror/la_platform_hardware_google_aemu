import os
import sys
import shutil
import platform
import subprocess
import shlex
import py_compile
from lib.output import print_result

SKILL_MARKDOWN_CONTENT = """---
name: emu_dev_cli
description: CLI tool for fetching prebuilt Android Emulator binaries and system images from Android Build (go/ab) into /tmp/. Use when setting up test instances or reproducing bugs.
---

# Android Emulator Developer CLI (`emu-dev-cli`)

Use `emu-dev-cli` to pull and extract prebuilt emulator host binaries or system image archives from Android Build (`go/ab`).

Executable path:
```bash
bazel-bin/hardware/google/aemu/tools/emu-dev-cli/emu-dev-cli
```
*(If missing, run `bazel build //hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` once).*

---

## Supported Commands

### 1. Fetch Emulator Binaries (`fetch-build emulator`)

Pulls host emulator release package (`sdk-repo-linux-emulator-<build-id>.zip`) into `/tmp/emulator-linux-x64-<build-id>/extracted/emulator/emulator`.

* **Fetch latest on `emu-main-next` (Default):**
  ```bash
  emu-dev-cli fetch-build emulator --latest
  ```
* **Fetch latest on `emu-main-dev`:**
  ```bash
  emu-dev-cli fetch-build emulator --latest --branch emu-main-dev
  ```
* **Fetch by numeric build ID:**
  ```bash
  emu-dev-cli fetch-build emulator --build-id 15943073
  ```

---

### 2. Fetch System Images (`fetch-build system-image`)

Pulls system image archive (`sdk-repo-linux-system-images-<build-id>.zip`) into `/tmp/system-image-<arch>-<build-id>/extracted/`. Auto-detects workstation host architecture (`x86_64` vs `arm64`).

* **Fetch latest system image on `trunk-release` (Default):**
  ```bash
  emu-dev-cli fetch-build system-image --latest
  ```
* **Fetch latest system image on release branch:**
  ```bash
  emu-dev-cli fetch-build system-image --latest --branch 26Q2-emu-release
  ```
* **Fetch by numeric build ID:**
  ```bash
  emu-dev-cli fetch-build system-image --build-id 15900270
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
        android_bin = os.path.expanduser("~/.android/bin")
        os.makedirs(android_bin, exist_ok=True)
        return os.path.join(android_bin, "emu-dev-cli")


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
    Includes .py source files and compiles optional .pyc bytecodes using sys.executable.
    """
    os.makedirs(release_lib_dir, exist_ok=True)
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
            pyc_rel = rel_path[:-3] + ".pyc"
            dest_pyc = os.path.join(release_lib_dir, pyc_rel)
            try:
                py_compile.compile(source_py, cfile=dest_pyc, doraise=False)
            except Exception:
                pass


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


def install_skill():
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

    print_result({
        "status": "success",
        "action": "install",
        "summary": f"Successfully installed emu_dev_cli compiled release package and skills",
        "launcher_path": str(final_installed),
        "skill_files": skill_files,
    }, json_mode=json_mode)

    if final_installed:
        print_path_instructions(final_installed)
