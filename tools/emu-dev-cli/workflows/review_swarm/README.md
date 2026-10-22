# `review-swarm` Workflow

Orchestrates the **8-Specialist Single-Wave Parallel Code-Review Swarm** for Android Emulator Gerrit CLs and local commits.

## Usage

```bash
emu-dev-cli harness review-swarm --cl 43137255 --mode workflow
emu-dev-cli harness review-swarm --commit HEAD --mode workflow
emu-dev-cli workflow review-swarm init --target 43137255
```
