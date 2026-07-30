#!/usr/bin/env python3
from pathlib import Path
import shutil
import json
import os
import sys
import subprocess
import argparse

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
    parser = argparse.ArgumentParser(
        description="Setup or disable AEMU Agents, Skills, and emu-dev-cli."
    )
    parser.add_argument(
        "--emu-dev-cli-only",
        "--only-emu-dev-cli",
        action="store_true",
        dest="emu_dev_cli_only",
        help="Install only emu-dev-cli and its skill (WARNING: deletes existing workspace AGENTS.md, .gemini/agents/, and .gemini/skills/)."
    )
    parser.add_argument(
        "--disable-agents-skills",
        "--disable-all",
        action="store_true",
        dest="disable_agents_skills",
        help="Disable all agents and skills (WARNING: deletes existing workspace AGENTS.md, .gemini/agents/, .gemini/skills/, and policies)."
    )
    parser.add_argument(
        "--level",
        type=str,
        default=None,
        help="Autonomy level (0=emu-dev-cli only, 1=minimal, 2=standard, 3=advanced, 4=maximum, 5=disabled)."
    )
    parser.add_argument(
        "--install-emu-dev-cli",
        nargs="?",
        const="",
        default=None,
        help="Install emu-dev-cli globally (optionally specify install path)."
    )
    parser.add_argument(
        "--no-emu-dev-cli",
        action="store_true",
        help="Do not install emu-dev-cli."
    )
    parser.add_argument(
        "-y",
        "--yes",
        "--force",
        action="store_true",
        dest="force_yes",
        help="Skip interactive confirmation prompts for deletions."
    )
    args = parser.parse_args()

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

    # 3. Prompt for Autonomy Level / Configuration
    print("\nChoose an Autonomy Level / Configuration:")
    print("0) emu-dev-cli Only - Install only emu-dev-cli & its skill (WARNING: deletes existing workspace AGENTS.md, .gemini/agents/, and .gemini/skills/).")
    print("1) Minimal          - [minimal_discovery.toml] Read-only discovery.")
    print("2) Standard         - [standard_verification.toml] Adds build/test tools. [RECOMMENDED]")
    print("3) Advanced         - [advanced_surgical.toml] Adds autonomous file editing.")
    print("4) Maximum          - [maximum_autonomy.toml] Full shell/git access.")
    print("5) Disabled         - Disable all agents and skills completely (WARNING: deletes existing workspace AGENTS.md, .gemini/agents/, .gemini/skills/, and policies).")

    if args.emu_dev_cli_only:
        choice = "0"
    elif args.disable_agents_skills:
        choice = "5"
    elif args.level is not None:
        choice = str(args.level).strip()
    else:
        try:
            choice = input("Select option (0-5) [2]: ").strip() or "2"
        except EOFError:
            choice = "2"

    choice_lower = choice.lower()
    is_emu_dev_cli_only = choice_lower in ["0", "emu-dev-cli", "emu-dev-cli-only", "only", "cli-only"]
    is_disabled = choice_lower in ["5", "d", "disable", "disabled", "none", "off"]
    install_other_agents_skills = not (is_emu_dev_cli_only or is_disabled)

    tier_map = {
        "0": "standard_verification.toml",
        "emu-dev-cli": "standard_verification.toml",
        "1": "minimal_discovery.toml",
        "minimal": "minimal_discovery.toml",
        "2": "standard_verification.toml",
        "standard": "standard_verification.toml",
        "3": "advanced_surgical.toml",
        "advanced": "advanced_surgical.toml",
        "4": "maximum_autonomy.toml",
        "maximum": "maximum_autonomy.toml",
    }
    tier_file = tier_map.get(choice_lower, "standard_verification.toml")
    tier_path = script_dir / ".gemini/policies" / tier_file

    gemini_dir = source_root / ".gemini"

    if is_emu_dev_cli_only or is_disabled:
        if is_emu_dev_cli_only:
            print("\n-> 'emu-dev-cli Only' selected. Scanning for existing workspace AGENTS.md, .gemini/agents/, and .gemini/skills/ to remove...")
        else:
            print("\n-> Agents and skills disabled. Scanning for existing workspace AGENTS.md, .gemini/agents/, .gemini/skills/, and policies to remove...")

        files_to_remove = []
        agents_md = source_root / "AGENTS.md"
        if agents_md.exists() or agents_md.is_symlink():
            files_to_remove.append(agents_md)

        subdirs_to_check = ["agents", "skills"]
        if is_disabled:
            subdirs_to_check.extend(["policies", "policies_templates"])
            settings_file = gemini_dir / "settings.json"
            if settings_file.exists() or settings_file.is_symlink():
                files_to_remove.append(settings_file)

            home_dir = Path.home()
            for global_skill_dir in [
                home_dir / ".gemini/config/skills/emu_dev_cli",
                home_dir / ".gemini/skills/emu_dev_cli",
            ]:
                if global_skill_dir.exists():
                    for root, _, files in os.walk(global_skill_dir):
                        for f in files:
                            files_to_remove.append(Path(root) / f)

        for subdir in subdirs_to_check:
            d = gemini_dir / subdir
            if d.exists() or d.is_symlink():
                if d.is_dir() and not d.is_symlink():
                    for root, _, files in os.walk(d):
                        for f in sorted(files):
                            files_to_remove.append(Path(root) / f)
                else:
                    files_to_remove.append(d)

        files_to_remove = sorted(set(files_to_remove), key=lambda p: str(p))

        if not files_to_remove:
            print("-> No existing agent or skill files found to remove.")
        else:
            print("\n" + "!" * 68)
            print("WARNING: The following files will be PERMANENTLY REMOVED:")
            print("!" * 68)
            for f_path in files_to_remove:
                try:
                    print(f"  - {f_path.relative_to(source_root)}")
                except ValueError:
                    print(f"  - {f_path}")
            print("!" * 68)

            if not args.force_yes:
                try:
                    confirm = input("\nAre you sure you want to permanently delete these files? (y/N): ").strip().lower()
                except EOFError:
                    confirm = "n"

                if confirm not in ["y", "yes"]:
                    print("Aborted file deletion. Exiting setup.")
                    sys.exit(0)

            if agents_md.exists() or agents_md.is_symlink():
                agents_md.unlink()
                print("  - Removed AGENTS.md")

            for subdir in ["agents", "skills"]:
                d = gemini_dir / subdir
                if d.exists() or d.is_symlink():
                    if d.is_dir() and not d.is_symlink():
                        shutil.rmtree(d)
                    else:
                        d.unlink()
                    print(f"  - Removed .gemini/{subdir}")

            if is_disabled:
                for subdir in ["policies", "policies_templates"]:
                    d = gemini_dir / subdir
                    if d.exists() or d.is_symlink():
                        if d.is_dir() and not d.is_symlink():
                            shutil.rmtree(d)
                        else:
                            d.unlink()
                        print(f"  - Removed .gemini/{subdir}")
                settings_file = gemini_dir / "settings.json"
                if settings_file.exists() or settings_file.is_symlink():
                    settings_file.unlink()
                    print("  - Removed .gemini/settings.json")
                try:
                    if gemini_dir.exists() and not any(gemini_dir.iterdir()):
                        gemini_dir.rmdir()
                        print("  - Removed empty .gemini directory")
                except OSError:
                    pass

                home_dir = Path.home()
                for global_skill_dir in [
                    home_dir / ".gemini/config/skills/emu_dev_cli",
                    home_dir / ".gemini/skills/emu_dev_cli",
                ]:
                    if global_skill_dir.exists():
                        shutil.rmtree(global_skill_dir, ignore_errors=True)
                        print(f"  - Removed global skill {global_skill_dir}")

    if not is_disabled:
        # Create .gemini directory
        gemini_dir.mkdir(exist_ok=True)

        # 4. Generate adaptive policy.toml
        policies_dir = gemini_dir / "policies"
        policies_dir.mkdir(exist_ok=True)

        policy_dest = policies_dir / "aemu_policy.toml"

        with open(tier_path, 'r') as f:
            policy_content = f.read()

        # Inject detected build tools if Tier >= 2 or emu-dev-cli only
        if choice_lower in ["0", "2", "3", "4", "standard", "advanced", "maximum", "emu-dev-cli"]:
            for tool in build_tools:
                if f'commandPrefix = ["{tool} "]' not in policy_content:
                    policy_content += (
                        f'\n[[rule]]\ntoolName = "run_shell_command"\n'
                        f'commandPrefix = ["{tool} "]\ndecision = "allow"\n'
                        f'priority = 100\n'
                    )

        with open(policy_dest, 'w') as f:
            f.write(policy_content)

        def safe_copy(source_path_rel, dest_name, base_dir=source_root):
            source_path = source_root / source_path_rel
            full_dest_path = base_dir / dest_name

            if full_dest_path.is_symlink():
                full_dest_path.unlink()
            elif full_dest_path.exists():
                if full_dest_path.is_dir():
                    shutil.rmtree(full_dest_path)
                else:
                    full_dest_path.unlink()  # Overwrite for setup

            try:
                if source_path.is_dir():
                    shutil.copytree(source_path, full_dest_path)
                else:
                    shutil.copy2(source_path, full_dest_path)
                return True
            except OSError as e:
                print(f"ERROR: Failed to copy {dest_name}: {e}")
                return False

        if is_emu_dev_cli_only:
            print("Installing emu-dev-cli specific AGENTS.md to workspace root...")
            if not safe_copy("hardware/google/aemu/tools/emu-dev-cli/AGENTS.md", "AGENTS.md"):
                sys.exit(1)
        elif install_other_agents_skills:
            # 5. Create copies of AGENTS.md, settings.json, agents, skills, and policy templates
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
                                rmtree_path = dest
                                shutil.rmtree(rmtree_path)
                            else:
                                dest.unlink()

                        shutil.copytree(skill_dir, dest)
                        print(f"  + Copied skill {skill_dir.name}")
                    except Exception as e:
                        print(f"  ! Failed to copy skill {skill_dir.name}: {e}")

            # Copy Generic AEMU Skills
            copy_skills_from("hardware/google/aemu/agents/skills", skills_dir)
            copy_skills_from("hardware/generic/goldfish/agents/skills", skills_dir)
            copy_skills_from("external/qemu/android/agents/skills", skills_dir)

            # Also copy the tiers/policies folder so they are visible
            if not safe_copy("hardware/google/aemu/agents/.gemini/policies",
                             "policies_templates", base_dir=gemini_dir):
                print("Warning: Could not copy policies directory.")

    # 6. Prompt and install emu-dev-cli developer helper globally
    default_install_path = installer.detect_default_install_path() if installer else "/usr/local/bin/emu-dev-cli"
    if args.no_emu_dev_cli:
        should_install_emu_dev_cli = False
    elif args.install_emu_dev_cli is not None:
        should_install_emu_dev_cli = True
        install_path_str = args.install_emu_dev_cli or default_install_path
    else:
        print("\nInstall emu-dev-cli developer helper globally?")
        try:
            install_path_str = input(f"Specify installation binary path [{default_install_path}] (or type 'no' to skip): ").strip()
            if not install_path_str:
                install_path_str = default_install_path
        except EOFError:
            install_path_str = default_install_path

        should_install_emu_dev_cli = install_path_str.lower() not in ["no", "n", "skip", "none", "false", "0"]

    if should_install_emu_dev_cli and installer:
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
                    print(f"  + Permission denied for {dest_path}. Attempting to escalate with sudo...")
                    if installer.install_launcher_with_sudo(str(built_bin), str(dest_path)):
                        final_installed = dest_path
                        print(f"  + Installed emu-dev-cli global launcher to {dest_path} (via sudo)")
                    else:
                        fallback_path = Path(installer.get_fallback_install_path())
                        installer.install_launcher_wrapper(str(built_bin), str(fallback_path))
                        final_installed = fallback_path
                        print(f"  ! Sudo escalation failed; installed to fallback location: {fallback_path}")

                if not is_disabled:
                    installer.install_skill()
                if final_installed:
                    installer.print_path_instructions(final_installed)
            else:
                print("  ! Bazel build failed for emu-dev-cli.")
        except Exception as e:
            print(f"  ! Warning: Could not install emu-dev-cli: {e}")

    print("-" * 50)
    if is_emu_dev_cli_only:
        print("Done. Configured with emu-dev-cli only (no other custom agents or skills).")
        print(f"IMPORTANT: Trust the root folder: gemini-cli trust {source_root}")
    elif not is_disabled:
        print(f"Done. Gemini CLI configured with {tier_file}.")
        print(f"IMPORTANT: Trust the root folder: gemini-cli trust {source_root}")
    else:
        print("Done. All AEMU agents and skills have been disabled.")
    print("-" * 50)


if __name__ == "__main__":
    setup()

