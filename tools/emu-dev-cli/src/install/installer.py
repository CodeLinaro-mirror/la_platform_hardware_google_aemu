import hashlib
import os
from pathlib import Path
import platform
import py_compile
import shlex
import shutil
import subprocess
import sys

from commands.source_directory import (
    ensure_codesearch_urls_config,
    get_all_source_directories,
    get_source_directory,
)
from lib.bazel import BazelRunner
from lib.output import print_result
from lib.workspace import WorkspacePathResolver

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

### 7. Codebase Wiki & Documentation Engine (`docs`)

Provides a persistent **LLM Wiki** interface (`Query → Ingest → Lint`) across all `ARCHITECTURE.md`, `DESIGN.md`, and operational playbooks in the workspace:

* **List all indexed architecture docs and playbooks (`docs index`):**
  ```bash
  emu-dev-cli docs index
  ```
* **Search across architecture docs and playbooks by keyword (`docs search`):**
  ```bash
  emu-dev-cli docs search VideoRecorder
  ```
* **Audit `ARCHITECTURE.md` and `DESIGN.md` for drift (`docs lint`):**
  ```bash
  emu-dev-cli docs lint
  ```
* **Resolve named documentation & operational playbooks:**
  ```bash
  emu-dev-cli docs playbook-index
  emu-dev-cli docs playbook-build-and-tidy
  emu-dev-cli docs playbook-git-and-gerrit
  emu-dev-cli docs playbook-threading-and-invariants
  emu-dev-cli docs goldfish-architecture
  emu-dev-cli docs cts-verifier-automation
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
        local_appdata = os.environ.get(
            "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
        )
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
        local_appdata = os.environ.get(
            "LOCALAPPDATA", os.path.join(home_dir, "AppData", "Local")
        )
        return os.path.join(local_appdata, "Google", "EmuDevCLI")
    else:
        return os.path.join(home_dir, ".android", "emu-dev-cli")


def _safe_copy2(src: str, dst: str) -> None:
    """Copies file contents and permissions while tolerating sandbox xattr EACCES."""
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        shutil.copyfile(src, dst)


def copy_src_to_release_lib(src_dir, release_lib_dir):
    """
    Copies python modules to release/lib/ directory.
    Includes .py source files and cleans up stale .pyc bytecodes.
    Also copies workflows/ directory if present.
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
                _safe_copy2(source_py, dest_py)

    # Copy workflows/ directory into release/lib/workflows/ if present
    if os.path.abspath(src_dir) != os.path.abspath(release_lib_dir):
        parent_dir = os.path.dirname(src_dir)
        workflows_dir = os.path.join(parent_dir, "workflows")
        if os.path.isdir(workflows_dir):
            dest_workflows = os.path.join(release_lib_dir, "workflows")
            for root, _, files in os.walk(workflows_dir):
                for f in files:
                    if not (f.endswith(".py") or f.endswith(".md") or f.endswith(".yaml") or f.endswith(".yml") or f.endswith(".html")):
                        continue
                    rel_path = os.path.relpath(os.path.join(root, f), workflows_dir)
                    dest_file = os.path.join(dest_workflows, rel_path)
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    source_file = os.path.join(root, f)
                    if os.path.abspath(source_file) != os.path.abspath(dest_file):
                        _safe_copy2(source_file, dest_file)

        docs_dir = os.path.join(parent_dir, "docs")
        if os.path.isdir(docs_dir):
            dest_docs = os.path.join(release_lib_dir, "docs")
            for root, _, files in os.walk(docs_dir):
                for f in files:
                    if not f.endswith(".md"):
                        continue
                    rel_path = os.path.relpath(os.path.join(root, f), docs_dir)
                    dest_file = os.path.join(dest_docs, rel_path)
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    source_file = os.path.join(root, f)
                    if os.path.abspath(source_file) != os.path.abspath(dest_file):
                        _safe_copy2(source_file, dest_file)


def resolve_source_directory(source_dir=None):
    if source_dir:
        cand = os.path.join(
            source_dir, "hardware", "google", "aemu", "tools", "emu-dev-cli", "src"
        )
        if os.path.exists(cand):
            return cand
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_built_artifact(source_workspace, target, binary_name=None):
    """Dynamically resolves built artifact via BazelRunner cquery."""
    is_windows = platform.system().lower() == "windows"
    src_path = Path(source_workspace)

    try:
        runner = BazelRunner(source_dir=src_path)
        artifacts = runner.query_artifacts([target], cwd=src_path)
        for art in artifacts:
            if art.is_file() and (is_windows or os.access(art, os.X_OK)):
                return str(art)
    except Exception as e:
        sys.stderr.write(
            f"ℹ️ Note: dynamic Bazel cquery for {target} failed ({e}).\n"
        )

    return None


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

    # Copy third-party site-packages from runfiles if present
    built_rf = built_bin + ".runfiles"
    if os.path.exists(built_rf):
        for root, _, _ in os.walk(built_rf):
            if os.path.basename(root) == "site-packages":
                for item in os.listdir(root):
                    if item.startswith((".", "_")) and not item.endswith((".py", ".so")):
                        continue
                    item_path = os.path.join(root, item)
                    dest_item = os.path.join(release_lib_dir, item)
                    if not os.path.exists(dest_item):
                        try:
                            if os.path.isdir(item_path):
                                shutil.copytree(item_path, dest_item, symlinks=False)
                            else:
                                shutil.copy2(item_path, dest_item)
                        except OSError:
                            pass

    # Copy advisor self-contained executable and runfiles if built
    source_workspace = source_dir or get_source_directory("emu-main-next")
    if not source_workspace or not os.path.exists(source_workspace):
        source_workspace = resolve_source_directory(source_dir)

    if source_workspace:
        advisor_src = resolve_built_artifact(
            source_workspace,
            target="@goldfish//emulator/crashreport/tool/advisor:advisor",
            binary_name="advisor",
        )
        if advisor_src and os.path.exists(advisor_src):
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
                except OSError as e:
                    sys.stderr.write(
                        f"⚠️ Warning: Failed to copy advisor runfiles from {advisor_rf} to {advisor_rf_dest}: {e}\n"
                    )

        # Copy compiled emu-main-next crashreport executable directly to bin/
        exe_suffix = ".exe" if platform.system().lower() == "windows" else ""
        crashreport_src = resolve_built_artifact(
            source_workspace,
            target="@goldfish//emulator/crashreport/tool:crashreport",
            binary_name=f"crashreport{exe_suffix}",
        )
        if crashreport_src and os.path.exists(crashreport_src):

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
    cmd = [
        "sudo",
        "sh",
        "-c",
        f"ln -sf {quoted_release_bin} {quoted_dest} || cp {quoted_release_bin} {quoted_dest}",
    ]
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
        candidate_skills.append(
            os.path.join(
                source_dir,
                "hardware",
                "google",
                "aemu",
                "tools",
                "emu-dev-cli",
                "skills",
                "SKILL.md",
            )
        )
    candidate_skills.append(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "skills",
            "SKILL.md",
        )
    )
    candidate_skills.append(
        os.path.join(home_dir, ".android", "emu-dev-cli", "skills", "SKILL.md")
    )

    content_to_write = SKILL_MARKDOWN_CONTENT
    for cand in candidate_skills:
        if cand and os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    content_to_write = f.read()
                break
            except OSError as e:
                sys.stderr.write(f"ℹ️ Could not read skill from {cand}: {e}\n")

    for target_dir in target_dirs:
        try:
            os.makedirs(target_dir, exist_ok=True)
            skill_file = os.path.join(target_dir, "SKILL.md")
            with open(skill_file, "w", encoding="utf-8") as f:
                f.write(content_to_write)
            installed_files.append(skill_file)
        except OSError as e:
            sys.stderr.write(
                f"⚠️ Warning: Could not install skill to {target_dir}: {e}\n"
            )

    return installed_files


KNOWN_LEGACY_SHA256_HASHES = {
    "039593b114eb6962b9b2d2dcea8c2797479a86a83f9356dd93e2e92f6bc198fe",
    "0b6547a9bbeb8cef9e1ec35ba992a7d96cc52e4894686249b736e30f5ff94af8",
    "0dbf13a0a4e699dd2c3038d5a95b7c02dcc83cf6d8ce5d59b13cfec867a0e126",
    "0fbf5df48812d3ca053373ed2e5a6de331dbe13f2127e3df62d6435352ca6b2e",
    "12e1624b7918053de4b153e778ee9c853aadbeacc9f463e219dfc3a845f76f76",
    "15a41f4a7fdce422329296a7873013e27024da78f6299eeb4ca38b1da10a5bc6",
    "15cf3e1e44fcaa44209c2a90afa51ed580514a1cbc935a96be5c2fa1fa2676a1",
    "1c51afab08a1ac17ec57dea683b015fdb746be80d43aa9626cbe14e632acd6ee",
    "1cd0f2e6d12b56b5e127370a14af6b5bb3c1a45b3d616c72c242137b823279f7",
    "1dbf30ea81c68b9a12208e31122c9c4aa7095d4d71fcc45f4eb72fefe0f6b716",
    "1dd4de98152f7a0aab832b5d5b625c45e91fc9b5f87756a8ea44df3582af5b1d",
    "202ff5c44e3e6fb19195cfb8ca465e2e946500eb5c1c0b259be023baa212e0e6",
    "20d8c62ad7f5bda40327fecf83e592085e4e41f183b171461b6b9b1e6ecee0fe",
    "21bff47c5126e22c2b522e917112db009c89c1ac00a2d2eceda0feb86b5e6401",
    "248339b6c15e3cbd2d1c3380680a4dd68cd0c5fd3d2056717dfea2b544164fce",
    "24fb586ca1f588f6029c0b11bd518e4321b9ec26d634a5edbe7119f8bb8a112f",
    "2ba6741a68bb0c0a3e29223053643820a9c14e697c9dfa201a4974d197fd0107",
    "2c05b575cb74a29ec59ec9339022ed19864ea4f1ea3f184f3799785813231f96",
    "2cde50002a6440b0317397c11675f0918762474791c88e055609894c3e523083",
    "2d136db98473b4803be8193ee6b82c2956cdda6ff391271267a62483b468e025",
    "2f595493a8031e27268a605ba54dd0532f7a0afd04e5e7395235c20470f112e2",
    "318c5a97b1053b5cebd8f6da1b346ecb200ca901fa219d27e16b6f740168dc2d",
    "3277a0f27bc5def8d77fee64f13cf33a4b1bdcfe1729e345547cd99249b732df",
    "32c7bbc8697bc99ebd5adb3758dd2bec8fd2ca84d488b3294cb6c97b558c9f26",
    "32f2b5d4d7e55b34bc92deb7aeb20a53ed1a806b5b54b6c2d23846e9876c7db8",
    "37f6ed4686431f8bb1d00f8acd1210f0e29965244d95442d314c0535d260a854",
    "3c73618f53b51532d9affc9a6979ea3934168236bc5fda83f49661cc097c18ab",
    "3f4b3a3942eaaafdd713f9b5f0bc5fc61fa6ba4cedd5bf1e7afa166e6748a30e",
    "3ff75febf9a9b7954a60b6b28603c6226256261b102a58edb827b595aa6b0df3",
    "43f961e2cec9b63b8f915c704dc878b5384ba533980efaf0ac99d648fc3f8c59",
    "4b2f010935d4106920b554d635368fbb5efa1ce918cf43f1b6e53a0ed00d9215",
    "50788dfba17bcff36bba1c37463e301e39086ec007cbc1a68960c436cd7e041d",
    "52c77970fb55bbb0cbabe2a80bed24d2e624cdce6f35d95626150c424600097b",
    "539fc2d8971bbeac1fc6303159c3b7c3f0b99920b15224f68530a4d923ff31bd",
    "53bd531fc023d012bab2896cec0f5a7cc196ded310c56fdbcd2a7268957dc9e5",
    "5618f45145a44fa57d322648be4d2b92ec405e936388b325e717ca156520e1c9",
    "56e8ac6b17c76b564c4fbfccd55fbd10b3118b79a2eda9d3c60a9ee97d90c381",
    "5792725e99ad6c6e0652615203f4359dbf181277a42c82ecdb47351bd13fa058",
    "591e7d66c6eef15b51453799f6cd58433d1508a09f50482c4d2697a19419732a",
    "5d9113acdc30548907d25c7d61e0bf20f9370b6b5720c6d70c05fd0f2597e411",
    "6236d508072c073a51bf0b718b95dfa08d4442af0f33721be673331391cffefd",
    "62638355d74a7b915c8dc2f06e5173beb0d977691949aadb1a4c0fcdec278eb1",
    "64ad7818dd26b4bb6ff7d5e2d0a87d28a1faf43c9d9e99d21da0926a2139d924",
    "65a92a8af497a3403ae85cf8235e953408b6b8386ed85193d8fb0cd29a4c7147",
    "67477f224f70106b28aadb6492961f3fb82ebb066f44852f2e871d31edfe819c",
    "681cd44763e4e1ca0f72422d1371f78088f0f5caad24f19da9dcd96d7abfda6a",
    "6d1576fddce043bf07dcdc3b8155f1b532a048e519883136eba53d71df09964d",
    "70ccf7cfa34cf53d02add54cd50290310222e30a8aefc7c58b30abeb7acb858f",
    "757f98c4698b2b9416e82cda5777e7f49202d24e86bcc1d4a5ee9e116453bd8c",
    "76db67f2080b57e2f3a3d9412a4f55dff8952c3e0507e7bf2c104a9830c63da9",
    "7785817c66eed44d0b8e655cdf190ba9d289c449f543cd2c33391337e9e99d2a",
    "780dbb42cd5ee9f46937639e35f6c524ecebbdaffe31f48f0b5a0d9f0587d519",
    "7849de938355ee41a8be91fbc175d2d2756a1a3dedb4ba21363bdddd3ad43f91",
    "7b3be37bf646d5303a8ba9469cced548205a6d4ec4b6158e856998c0ae60fe1f",
    "7f55a8569b26f5580c2631526506b94cc84daabca17f7531bb38df69d7a657b2",
    "7fdd8cbf9cd6b92fceda253376b22287c2ecb320729bc87cb309c53b8df0485e",
    "839626d138cc6809464dff4d34a2c781b685c6c11d442515507e4ab50afd2e21",
    "8469566f00962c476a664406e34b61e3c412b271a13d6b97564e27527e20037c",
    "881242b7315aa98df03351c414c4e57a72c8b7423a1e43aa3b309daaf896c6f4",
    "8aab0d21a8943a9c60da1a6eff3b24ddeaa7a20e1b1b9a29ec82169737e00bcb",
    "8fdbc9d65663d0b1181207dcef4d1a65f7599324f354892dd98dd9c3de530875",
    "93b812adffee7208434232067e5f356f11e81dd51b8e9ca7b766ca6fccb9c0b7",
    "9af3a0aa3411f0b6b9a9f68297c8fc561fef83de452ff20ae212c8047ceda19c",
    "9ce85443081414ae19a79fa45492d399f0e9eb2a9aad8e008bdd7ee0026565c3",
    "a13680de14cfc773d3d28b32ade72403bf872168438aa0e320d5d62b3be08829",
    "a4c07951003923a492060d324ed76fb2bf756748b8cb90c2b3f0cb3ae5fbf3d9",
    "a59ebe1ddcd106ed2b35dc013baa00dc6dd12ecc96e768aedd00e70956ba6403",
    "a9fe1dde15c5528652c1158c632fac015461ed95a99a8e2c7d3aa2469132e9cc",
    "b1648ec8bf312ab051eae2af03b69cf41c27512d9b8b3054533761becc204639",
    "b3dd10c1c05892e7a9e1c146c42773d812336dd802a997316630749ddff10dba",
    "b7068ae99aa9d3b0c34cf2f064d2d24a67f375d566c57d73442a6602868e2aa9",
    "c3cd082ba8f88217eba963ec46397f57173bfb67383a38db9651276ab5323202",
    "c86f31763c1605ea403d426b0bb9b2f7c118c4c5ac41a284d5e2307cf0bc404a",
    "ce5e238cc1fbb2e2462f14f1e7befc38857f814e995509e9ff88fb6bbc42a058",
    "d20e3384ea7ebd5a107db9996d33b5c4152ac214b783b6c5bb0b09966b2feba5",
    "d2af1f1ceaea8a6e4efe65151ab8282e8cb48a44637adcb6331f83eb2071ec93",
    "d561df41cbcb4ff1ef8cf4b97926094408dc6bc38e51716fbf4458b4c1be2ea8",
    "de2f440a13c899678facd35ef30c20bcbed710bbf382d7b0e69b1811c57aeb79",
    "de42047230582e60318c821d19e307bd14e25b0153ad5c686535296c0f48ca62",
    "ded59d630c441549e6ee394a66ed86e27cb9661c34fd1f375a552ae575ccea2f",
    "df275934106035199c56c3af4a7e742f89cbd76fafef58206d16245ccfee980d",
    "e1b2ff70b648e8f654b95c396abe63c13c3f28dcf99189b64ae47246f9991049",
    "e2911262ddb4fa061ef60a9079ead16063828b88949460a5e44ea2c69ec24654",
    "e50b96cd780cf7d37d8bad93206e9a2c81b1afb83bfc2364486a50174cbe6745",
    "e8929c7102629ae3407a20f32a61cd63799b46c8cf745eb1bdfea0ebb8454801",
    "eac05c74155c5637c22178f680e4da9ea6e2ba38154bd35f92edea1153bb9988",
    "edf0ee5528d397c7ef2547adefedbce29c740998915c99f3ee9de5fa65d44af3",
    "ee629a7a33c834f24cb9c6c2faa806a42c25966f80b95de31268b448e22b45d2",
    "f00c73cd43a1745d4908fcbbb5e49c6abf863a3a3d18019c7de555b7a94c8898",
    "f07871f32de96e177263f0e5d4de2b3ff1e2866e6b506d8ead5f59f7bc8bdb26",
    "f19a0d8751fb575bf8884f29acb95ccc0f63de0e77568e1ad530dc708cbc1aa6",
    "f87e4106bda194ebed97df24acfb3f8174079e0ed2d46b003fd1506896caecae",
    "fbe178b31756f6da4006ac7a4e459b52cf608d432d581e9000267863357ae566",
    "fbe1c0c79f3e129d0053334f84ad862588e2cdf29c68f4e1dd1b6f71f0ade9a7",
    "fd58d91aded6938235d108cd2ee5e6c1f6f5209296750ba5b252b7221f7946cb",
}


def _compute_file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def collect_legacy_sha256_hashes(source_root: Path) -> set[str]:
    """Collects SHA-256 hashes of known legacy setup_agents.py files."""
    hashes = set(KNOWN_LEGACY_SHA256_HASHES)
    template_paths = [
        source_root / "hardware" / "google" / "aemu" / "agents",
        source_root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "AGENTS.md",
        source_root / "hardware" / "generic" / "goldfish" / "agents" / ".gemini",
        source_root / "hardware" / "generic" / "goldfish" / "agents" / "skills",
        source_root / "external" / "qemu" / "android" / "agents",
    ]
    for tp in template_paths:
        if not tp.exists():
            continue
        if tp.is_file():
            digest = _compute_file_sha256(tp)
            if digest:
                hashes.add(digest)
        elif tp.is_dir():
            for root_dir, _, files in os.walk(tp):
                for fname in files:
                    fpath = Path(root_dir) / fname
                    digest = _compute_file_sha256(fpath)
                    if digest:
                        hashes.add(digest)

    # Also include generated aemu_policy.toml combinations
    policies_dir = (
        source_root / "hardware" / "google" / "aemu" / "agents" / ".gemini" / "policies"
    )
    if policies_dir.is_dir():
        tool_combos = [
            ["bazel"],
            ["cmake", "make", "python3", "python"],
            ["bazel", "cmake", "make", "python3", "python"],
        ]
        for toml_file in policies_dir.glob("*.toml"):
            try:
                base_text = toml_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hashes.add(hashlib.sha256(base_text.encode("utf-8")).hexdigest())
            for combo in tool_combos:
                modified_text = base_text
                for tool in combo:
                    if f'commandPrefix = ["{tool} "]' not in modified_text:
                        modified_text += (
                            f'\n[[rule]]\ntoolName = "run_shell_command"\n'
                            f'commandPrefix = ["{tool} "]\ndecision = "allow"\n'
                            f'priority = 100\n'
                        )
                hashes.add(hashlib.sha256(modified_text.encode("utf-8")).hexdigest())

    return hashes


def cleanup_legacy_workspace_files(source_dir: str | Path, quiet: bool = False) -> dict:
    """
    Removes legacy workspace root files (AGENTS.md and .gemini/*) installed by
    older versions of setup_agents.py ONLY if their SHA-256 matches known legacy
    templates, preserving any user-created or modified files.
    """
    source_root = Path(source_dir).resolve()
    known_hashes = collect_legacy_sha256_hashes(source_root)

    removed_files = []
    kept_files = []

    candidates = []
    agents_md = source_root / "AGENTS.md"
    if agents_md.exists() or agents_md.is_symlink():
        candidates.append(agents_md)

    gemini_dir = source_root / ".gemini"
    if gemini_dir.exists() and gemini_dir.is_dir() and not gemini_dir.is_symlink():
        for root_dir, _, files in os.walk(gemini_dir, followlinks=False):
            for fname in sorted(files):
                candidates.append(Path(root_dir) / fname)

    for file_path in candidates:
        rel_str = file_path.relative_to(source_root).as_posix()
        if file_path.is_symlink():
            try:
                file_path.unlink()
                removed_files.append(rel_str)
                if not quiet:
                    print(f"  - Removed legacy symlink: {rel_str}")
            except OSError:
                kept_files.append(rel_str)
            continue

        digest = _compute_file_sha256(file_path)
        if digest and digest in known_hashes:
            try:
                file_path.unlink()
                removed_files.append(rel_str)
                if not quiet:
                    print(f"  - Removed legacy workspace file: {rel_str}")
            except OSError:
                kept_files.append(rel_str)
        else:
            kept_files.append(rel_str)
            if not quiet:
                print(
                    f"  ! Keeping customized file: {rel_str} (SHA-256 does not match legacy template)"
                )

    # Prune empty directories under .gemini (bottom-up)
    if gemini_dir.exists() and gemini_dir.is_dir() and not gemini_dir.is_symlink():
        for root_dir, dirnames, _ in os.walk(gemini_dir, topdown=False, followlinks=False):
            for dname in dirnames:
                dpath = Path(root_dir) / dname
                try:
                    if dpath.is_dir() and not dpath.is_symlink() and not any(dpath.iterdir()):
                        dpath.rmdir()
                except OSError:
                    pass
        try:
            if not any(gemini_dir.iterdir()):
                gemini_dir.rmdir()
                if not quiet and removed_files:
                    print("  - Removed empty legacy .gemini directory")
        except OSError:
            pass

    return {
        "removed_files": removed_files,
        "kept_files": kept_files,
    }


def print_path_instructions(installed_bin_path):
    installed_dir = os.path.dirname(str(installed_bin_path))
    path_dirs = [
        os.path.normpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p
    ]
    norm_installed_dir = os.path.normpath(installed_dir)

    if norm_installed_dir in path_dirs:
        print(f"  ✓ Directory {installed_dir} is already in your PATH.")
        return

    system = platform.system().lower()
    print("-" * 50)
    print(
        f"⚠️  To use 'emu-dev-cli' from any terminal, add its directory to your PATH:"
    )
    if system == "windows":
        print(f"\n  In PowerShell, run:")
        print(
            f'    [Environment]::SetEnvironmentVariable("Path", $env:Path + ";{installed_dir}", "User")'
        )
        print(f"\n  Or in Command Prompt (cmd), run:")
        print(f'    setx PATH "%PATH%;{installed_dir}"')
    else:
        shell_rc = "~/.bashrc"
        user_shell = os.environ.get("SHELL", "")
        if "zsh" in user_shell:
            shell_rc = "~/.zshrc"
        print(f"\n  Run the following command in your terminal:")
        print(
            f"    echo 'export PATH=\"{installed_dir}:$PATH\"' >> {shell_rc} && source {shell_rc}"
        )
    print("-" * 50)


def register_parser(subparsers):
    install_parser = subparsers.add_parser(
        "install",
        help="Install emu-dev-cli global launcher and agent SKILL.md into user environment",
    )
    default_path = detect_default_install_path()
    install_parser.add_argument(
        "--path",
        type=str,
        default=default_path,
        help=f"Destination executable path (defaults to '{default_path}')",
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

    cleanup_reports = {}
    for entry in get_all_source_directories():
        s_path = entry.get("path") if isinstance(entry, dict) else entry
        if s_path and os.path.isdir(s_path):
            cleanup_reports[s_path] = cleanup_legacy_workspace_files(
                s_path, quiet=json_mode
            )

    print_result(
        {
            "status": "success",
            "action": "install",
            "summary": f"Successfully installed emu_dev_cli compiled release package and skills",
            "launcher_path": str(final_installed),
            "skill_files": skill_files,
            "legacy_cleanup": cleanup_reports,
        },
        json_mode=json_mode,
    )

    if final_installed:
        print_path_instructions(final_installed)
