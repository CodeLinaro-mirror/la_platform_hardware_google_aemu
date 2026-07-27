#!/usr/bin/env python3
from pathlib import Path
import shutil
import json
import os
import sys
import subprocess

# Add emu-dev-cli source directory to sys.path to reuse installation package
_SCRIPT_DIR = Path(__file__).resolve().parent
_SOURCE_ROOT = _SCRIPT_DIR.parents[3]
_EMU_DEV_CLI_SRC = _SOURCE_ROOT / "hardware/google/aemu/tools/emu-dev-cli/src"
if _EMU_DEV_CLI_SRC.exists():
    sys.path.insert(0, str(_EMU_DEV_CLI_SRC))

try:
    from install import installer
    from commands.update_cmd import find_bazel_cmd
except ImportError:
    installer = None
    find_bazel_cmd = None


def setup():
    script_dir = Path(__file__).resolve().parent
    source_root = script_dir.parents[3]

    print("-" * 50)
    print(f"Setting up AEMU Agents at: {source_root}")
    print("-" * 50)

    # 2. Environment Detection
    print("Detecting build environment...")
    build_tools = []
    if (source_root / "MODULE.bazel").exists() or \
       (source_root / "WORKSPACE").exists():
        print("-> Detected Bazel Environment")
        build_tools.append("bazel")

    if (source_root / "CMakeLists.txt").exists() or \
       (source_root / "rebuild.sh").exists():
        print("-> Detected CMake/Python Environment")
        build_tools.extend(["cmake", "make", "python3", "python"])

    # 3. Prompt for Autonomy Level
    print("\nChoose an Autonomy Level for the Agents:")
    print("1) Minimal    - [minimal_discovery.toml] Read-only discovery.")
    print("2) Standard   - [standard_verification.toml] Adds build/test tools. [RECOMMENDED]")
    print("3) Advanced   - [advanced_surgical.toml] Adds autonomous file editing.")
    print("4) Maximum    - [maximum_autonomy.toml] Full shell/git access.")

    try:
        choice = input("Select level (1-4) [2]: ").strip() or "2"
    except EOFError:
        choice = "2"

    tier_map = {
        "1": "minimal_discovery.toml",
        "2": "standard_verification.toml",
        "3": "advanced_surgical.toml",
        "4": "maximum_autonomy.toml"
    }
    tier_file = tier_map.get(choice, "standard_verification.toml")
    tier_path = script_dir / ".gemini/policies" / tier_file

    # Prompt for emu-dev-cli installation location using install package
    default_install_path = installer.detect_default_install_path() if installer else "/usr/local/bin/emu-dev-cli"
    print("\nInstall emu-dev-cli developer helper globally?")
    try:
        install_path_str = input(f"Specify installation binary path [{default_install_path}]: ").strip()
        if not install_path_str:
            install_path_str = default_install_path
    except EOFError:
        install_path_str = default_install_path

    # Create .gemini directory
    gemini_dir = source_root / ".gemini"
    gemini_dir.mkdir(exist_ok=True)

    # 4. Generate adaptive policy.toml
    policies_dir = gemini_dir / "policies"
    policies_dir.mkdir(exist_ok=True)

    policy_dest = policies_dir / "aemu_policy.toml"

    with open(tier_path, 'r') as f:
        policy_content = f.read()

    # Inject detected build tools if Tier >= 2
    if choice in ["2", "3", "4"]:
        for tool in build_tools:
            if f'commandPrefix = ["{tool} "]' not in policy_content:
                policy_content += (
                    f'\n[[rule]]\ntoolName = "run_shell_command"\n'
                    f'commandPrefix = ["{tool} "]\ndecision = "allow"\n'
                    f'priority = 100\n'
                )

    with open(policy_dest, 'w') as f:
        f.write(policy_content)

    # 5. Create copies
    def safe_copy(source_path_rel, dest_name, base_dir=source_root):
        source_path = source_root / source_path_rel
        full_dest_path = base_dir / dest_name

        if full_dest_path.is_symlink():
            full_dest_path.unlink()
        elif full_dest_path.exists():
            if full_dest_path.is_dir():
                shutil.rmtree(full_dest_path)
            else:
                full_dest_path.unlink() # Overwrite for setup

        try:
            if source_path.is_dir():
                shutil.copytree(source_path, full_dest_path)
            else:
                shutil.copy2(source_path, full_dest_path)
            return True
        except OSError as e:
            print(f"ERROR: Failed to copy {dest_name}: {e}")
            return False

    if not safe_copy("hardware/google/aemu/agents/AGENTS.md", "AGENTS.md"):
        sys.exit(1)

    # Use safe_copy for settings.json to ensure it is read correctly
    if not safe_copy("hardware/google/aemu/agents/.gemini/settings.json", "settings.json", base_dir=gemini_dir):
        print("Warning: Could not copy settings.json.")

    # Smart Agent Aggregation
    agents_dir = gemini_dir / "agents"
    if agents_dir.is_symlink():
        agents_dir.unlink()
    agents_dir.mkdir(exist_ok=True)

    def copy_agents_from(source_path_rel, target_dir):
        source_full = source_root / source_path_rel
        if not source_full.exists():
            return

        print(f"Copying agents from: {source_path_rel}")
        for agent_file in source_full.glob("*.md"):
            try:
                dest = target_dir / agent_file.name
                if dest.exists() or dest.is_symlink():
                    dest.unlink()

                shutil.copy2(agent_file, dest)
                print(f"  + Copied {agent_file.name}")
            except Exception as e:
                print(f"  ! Failed to copy {agent_file.name}: {e}")

    # Copy Generic AEMU Agents
    copy_agents_from("hardware/google/aemu/agents/.gemini/agents", agents_dir)
    copy_agents_from("hardware/generic/goldfish/agents/.gemini/agents", agents_dir)
    copy_agents_from("external/qemu/android/agents/.gemini/agents", agents_dir)

    # Smart Skills Aggregation
    skills_dir = gemini_dir / "skills"
    if skills_dir.is_symlink():
        skills_dir.unlink()
    skills_dir.mkdir(exist_ok=True)

    def copy_skills_from(source_path_rel, target_dir):
        source_full = source_root / source_path_rel
        if not source_full.exists():
            return

        print(f"Copying skills from: {source_path_rel}")
        for skill_dir in source_full.iterdir():
            if not skill_dir.is_dir():
                continue

            try:
                dest = target_dir / skill_dir.name
                if dest.exists() or dest.is_symlink():
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()

                shutil.copytree(skill_dir, dest)
                print(f"  + Copied skill {skill_dir.name}")
            except Exception as e:
                print(f"  ! Failed to copy skill {skill_dir.name}: {e}")

    # Copy Generic AEMU Skills
    copy_skills_from("hardware/google/aemu/agents/skills", skills_dir)
    copy_skills_from("hardware/google/aemu/tools/emu-dev-cli/skills", skills_dir)
    copy_skills_from("hardware/generic/goldfish/agents/skills", skills_dir)
    copy_skills_from("external/qemu/android/agents/skills", skills_dir)

    # Also copy the tiers/policies folder so they are visible
    if not safe_copy("hardware/google/aemu/agents/.gemini/policies",
                     "policies_templates", base_dir=gemini_dir):
        print("Warning: Could not copy policies directory.")

    # 6. Build and install emu-dev-cli using src/install module
    if "bazel" in build_tools and installer:
        print("\nBuilding and installing emu-dev-cli...")
        try:
            bazel_cmd = find_bazel_cmd(str(source_root)) if find_bazel_cmd else "bazel"
            res = subprocess.run(
                [bazel_cmd, "build", "//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli"],
                cwd=source_root, check=False
            )
            if res.returncode == 0:
                built_bin = source_root / "bazel-bin/hardware/google/aemu/tools/emu-dev-cli/emu-dev-cli"
                if sys.platform.startswith("win") and not built_bin.exists():
                    built_bin = built_bin.with_suffix(".exe")
                dest_path = Path(install_path_str)
                final_installed = None
                try:
                    installer.install_launcher_wrapper(str(built_bin), str(dest_path))
                    final_installed = dest_path
                    print(f"  + Installed emu-dev-cli global launcher to {dest_path}")
                except PermissionError:
                    print(f"  ! Permission denied for {dest_path}. Attempting to escalate with sudo...")
                    if installer.install_launcher_with_sudo(str(built_bin), str(dest_path)):
                        final_installed = dest_path
                        print(f"  + Installed emu-dev-cli global launcher to {dest_path} (via sudo)")
                    else:
                        fallback_path = Path(installer.get_fallback_install_path())
                        installer.install_launcher_wrapper(str(built_bin), str(fallback_path))
                        final_installed = fallback_path
                        print(f"  ! Sudo escalation failed; installed to fallback location: {fallback_path}")

                installer.install_skill()
                if final_installed:
                    installer.print_path_instructions(final_installed)
            else:
                print("  ! Bazel build failed for emu-dev-cli.")
        except Exception as e:
            print(f"  ! Warning: Could not install emu-dev-cli: {e}")

    print("-" * 50)
    print(f"Done. Gemini CLI configured with {tier_file}.")
    print(f"IMPORTANT: Trust the root folder: gemini-cli trust {source_root}")
    print("-" * 50)


if __name__ == "__main__":
    setup()
