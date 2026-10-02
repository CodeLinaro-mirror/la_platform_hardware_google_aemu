# AEMU & Goldfish Codebase Wiki Playbooks (`INDEX.md`)

**Role:** Operational Knowledge Base & Architecture Index  
**Location:** `hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/`

## Overview

This directory contains persistent operational playbooks and architectural guides for AI coding
agents and developers working in `emu-main-next` (`hardware/google/aemu`,
`hardware/generic/goldfish`, `external/qemu`).

Following the **LLM Wiki** pattern (`Query → Ingest → Lint`), agents should consult this index and
component `ARCHITECTURE.md` files **before** running broad exploratory searches or builds.

---

## Quick Discovery Commands (`emu-dev-cli docs`)

```bash
# List all indexed ARCHITECTURE.md, DESIGN.md, and operational playbooks
emu-dev-cli docs index

# Search across all architecture docs and playbooks by keyword
emu-dev-cli docs search <query>

# Audit ARCHITECTURE.md and DESIGN.md files for stale paths or moved Bazel targets
emu-dev-cli docs lint
```

---

## Operational Playbooks

| Playbook Target (`emu-dev-cli docs <name>`) | File                                    | Summary                                                                                                                                                                                      |
| :------------------------------------------ | :-------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `playbook-index`                            | `playbooks/INDEX.md`                    | Master index and LLM Wiki workflow (`Query → Ingest → Lint`).                                                                                                                                |
| `playbook-build-and-tidy`                   | `playbooks/build_and_tidy.md`           | Bazel RBE Windows cross-compilation (`--config=rbe-win-x64`), `clang_tidy.py` stack auditing (`mise tidy`), TSAN startup/suppression contracts, and asynchronous media test synchronization. |
| `playbook-git-and-gerrit`                   | `playbooks/git_and_gerrit.md`           | Git-on-Borg `push.negotiate=false` hang prevention, Gerrit commit message rules (no `TAG=`/`CONV=`), branch splitting, and `mise presubmit`.                                                 |
| `playbook-threading-and-invariants`         | `playbooks/threading_and_invariants.md` | Concurrency invariants across QEMU BQL, Looper/EventLoop, gfxstream RenderThread, gRPC server threads, and Display Pipeline (`DisplaySource` / `DisplaySink` / `VideoRecorder`).             |

---

## Core Architecture Entry Points

| Domain                              | Path                                                                  | Description                                                                                              |
| :---------------------------------- | :-------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------- |
| **Goldfish Top-Level Architecture** | `hardware/generic/goldfish/ARCHITECTURE.md`                           | System topology, Rust/C++ boundaries, plugin architecture, and build rules.                              |
| **Display Plugin & Pipeline**       | `hardware/generic/goldfish/emulator/plugin/display/ARCHITECTURE.md`   | `DisplaySource`/`DisplaySink` pipeline, `QemuDisplay`, `VirtualDisplay`, `VideoRecorder`, `WebMEncoder`. |
| **Host Composition (`gfxstream`)**  | `hardware/generic/goldfish/emulator/host-composition/ARCHITECTURE.md` | `FrameBuffer`, `Renderer`, `DisplayVk`, Vulkan/GLES composition and readback.                            |
| **gRPC Controller Services**        | `hardware/generic/goldfish/emulator/plugin/grpc/ARCHITECTURE.md`      | `EmulatorController`, streaming screenshot/recording RPCs, and BQL thread marshaling.                    |
| **Async & Looper Primitives**       | `hardware/generic/goldfish/emulator/libs/async/ARCHITECTURE.md`       | `Looper`, `FdWatch`, `RecurrentTask`, and timer scheduling contracts.                                    |

---

## The Three-Operation Wiki Protocol

1. **Query (Before Coding):** Run `emu-dev-cli docs search "<topic>"` or read the component's
   `ARCHITECTURE.md` to understand existing targets, invariants, and threading models before
   modifying source code.
2. **Ingest (During/After Implementation):** When moving packages, renaming Bazel targets, adding
   new plugins/components, or discovering non-obvious environment gotchas, update the corresponding
   `ARCHITECTURE.md` or playbook in the same branch.
3. **Lint (Before Upload):** Run `emu-dev-cli docs lint` to verify that all `**Location:**` headers,
   relative markdown links, and Bazel target labels in `ARCHITECTURE.md` and `DESIGN.md` resolve to
   existing files on disk.
