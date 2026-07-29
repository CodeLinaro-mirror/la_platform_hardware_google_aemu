# Android Emulator Developer CLI (`emu-dev-cli`) - Development Guide

This document outlines the architecture, motivation, installation paths, and development workflow for `emu-dev-cli`.

---

## 🎯 Motivation & Core Philosophy: Shortcut Commands for Deterministic Workflows

The primary objective of `emu-dev-cli` is to provide **shortcut commands that exercise deterministic operations and workflows**.

It does not create determinism itself; rather, it encapsulates operations that already have a single, canonical solution so that AI agents and developers do not need to figure out *how* to execute them.

### Core Principle:
> **If an operation or workflow is deterministic—meaning it has only one correct/canonical solution—it should be exercised via a shortcut command in `emu-dev-cli`.**

### Examples of Deterministic Operations & Workflows:

- 📚 **Locating Documentation & References**: Finding exact markdown documentation files, design specs, or source directory roots (`emu-dev-cli docs <target>`, `emu-dev-cli source-directory get <branch>`).
- 🏗️ **Compiling & Building Targets**: Compiling specific Bazel build targets or CLI binaries (`emu-dev-cli update`).
- 📦 **Fetching Build Artifacts**: Downloading prebuilt system images or CTS-Verifier packages from Android Build (`go/ab`) via `fetch_artifact` given a build ID and target.
- 📱 **AVD Creation & Setup**: Generating standardized Android Virtual Devices (AVD) with deterministic RAM, skin, density, and hardware parameters (`emu-dev-cli create`).
- 🧪 **Executing Test Modules**: Running hermetic CTS-Verifier test modules or test suites via Bazel (`emu-dev-cli cts run-cts-verifier --module <name>`).
- 🔍 **Module Discovery**: Dynamically scanning available test scripts in the repository without hardcoding (`emu-dev-cli cts run-cts-verifier --list-modules`).
- 📊 **Parsing & Verifying Test Reports**: Extracting, parsing, and validating `test_result.xml` test outputs into structured summary results.

---

## 🏗️ Architecture & Component Overview

`emu-dev-cli` is an extensible CLI framework for managing Android Emulator binaries, system images, AVDs, CTS-Verifier automation, and development workflows.

### Directory Structure
```
hardware/google/aemu/tools/emu-dev-cli/
├── BUILD.bazel                   # Bazel build definition (cc_binary)
├── README.md                     # General user overview & quickstart
├── docs/                         # Developer & Architecture documentation
│   └── DEVELOPMENT.md            # This document
├── skills/
│   └── SKILL.md                  # Gemini / Agent skill integration definition
├── src/                          # Python CLI source code & C++ launcher
│   ├── launcher.cpp              # C++ wrapper binary (compiled into output executable)
│   ├── __main__.py               # Python entry point
│   ├── commands/                 # Command plugin modules
│   │   ├── init_cmd.py           # First-time setup & environment check
│   │   ├── create.py             # AVD creation & configuration
│   │   ├── launch.py             # Emulator instance launcher
│   │   ├── fetch_build.py        # Android Build (go/ab) artifact fetcher
│   │   ├── cts.py                # CTS & CTS-Verifier test runner & TestBuilder
│   │   ├── docs.py               # Documentation query command
│   │   ├── update_cmd.py         # Self-update command (`emu-dev-cli update`)
│   │   └── source_directory.py   # Registry manager for local repo source paths
│   ├── targets/                  # Target definition plugins (emulator, system_image, etc.)
│   ├── install/                  # Package installer & release bundler
│   └── lib/                      # Shared helper functions (JSON formatting, output handling)
└── tests/                        # Unit tests for CLI components
```

---

## ⚙️ Installation & Environment Architecture

`emu-dev-cli` is designed as a standalone, zero-dependency binary launcher backed by modular Python packages.

### 1. Installation Layout & Path Locations
When installed or updated, `emu-dev-cli` manages the following paths on the host system:

- 🚀 **Launcher Symlink (`PATH`)**: `~/.android/bin/emu-dev-cli`
  - Symlinked into `~/.android/bin/` so `emu-dev-cli` can be executed directly from any shell directory without prefixing python paths.
  - **PATH Requirement**: Ensure `export PATH="$HOME/.android/bin:$PATH"` is present in your `~/.bashrc` or `~/.zshrc`.
- 📦 **Release Package Root**: `~/.android/emu-dev-cli/`
  - `~/.android/emu-dev-cli/emu-dev-cli`: Compiled C++ launcher executable binary (compiled via `launcher.cpp`).
  - `~/.android/emu-dev-cli/lib/`: Embedded Python source package containing `commands/`, `targets/`, `install/`, and `lib/`.
- ⚙️ **Source Directory Registry**: `~/.android/emu-dev-cli.json`
  - Stores local source tree registrations (e.g., `"emu-main-next": "/work/emu-main-next"`).
  - Used by `find_verifier_scripts_dir()` and `get_source_directory()` to resolve repository roots deterministically.
- 🤖 **Gemini Agent Skill Definitions**:
  - `~/.gemini/config/skills/emu_dev_cli/SKILL.md`
  - `~/.gemini/skills/emu_dev_cli/SKILL.md`
  - Enables Gemini and Jetski AI agents to discover and execute `emu-dev-cli` commands automatically.

---

### 2. Initial Bootstrap Installation
If setting up `emu-dev-cli` for the first time on a fresh workstation or workspace:

```bash
# 1. From the source root (/work/emu-main-next):
python3 hardware/google/aemu/tools/emu-dev-cli/src/install/installer.py

# 2. Verify installation and PATH resolution:
which emu-dev-cli
emu-dev-cli init
```

---

## 🔄 How to Build and Push Updates

When modifying or developing new features in `emu-dev-cli` source files under `hardware/google/aemu/tools/emu-dev-cli/`:

### 1. Recompile and Push Global Update
Run the built-in update command from anywhere within the repository workspace:

```bash
emu-dev-cli update
```

### What `emu-dev-cli update` Does Step-by-Step:
1. **Source Discovery**: Resolves the root source repository (e.g. `/work/emu-main-next`).
2. **Bazel Compilation**: Compiles `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` into a native C++ launcher binary (`bazel-bin/hardware/google/aemu/tools/emu-dev-cli/emu-dev-cli`).
3. **Library Sync**: Copies updated Python modules under `src/` to `~/.android/emu-dev-cli/lib/`.
4. **Binary & Symlink Refresh**: Replaces `~/.android/emu-dev-cli/emu-dev-cli` and refreshes the `~/.android/bin/emu-dev-cli` launcher symlink.
5. **Skill Definition Sync**: Updates `~/.gemini/config/skills/emu_dev_cli/SKILL.md` with updated command schemas.

---

## 🛠️ Adding New Subcommands

To add a new subcommand to `emu-dev-cli`:
1. Add a module inside `src/commands/<command_name>.py`.
2. Define `register_parser(subparsers)` with argument definitions.
3. Import and register the command in `src/__main__.py`.
4. Run `emu-dev-cli update` to test your changes.
