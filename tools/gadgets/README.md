# AEMU Developer & Agent Gadgets

Small developer utilities, Jetski rules, and lifecycle hooks that improve day-to-day agentic workflows.

## Quick Install

From the repository root, run:

```bash
python3 hardware/google/aemu/tools/gadgets/install.py
```

This installs the gadgets into `~/.gemini/config/` (and merges any hook configurations into `~/.gemini/config/hooks.json` without overwriting existing hooks).

## Included Gadgets

### 1. Terminal & Zellij Pane Title Updater (`pane_title/`)

Automatically updates the current Zellij pane, tmux pane, or terminal tab (Ghostty, Kitty, WezTerm, iTerm2) with a concise summary of the bug/feature the agent is working on:
- **`PreInvocation` hook:** Prefixes the pane/tab title with `⏳` while the agent is actively working.
- **`Stop` hook:** Updates the prefix to `✅` when the agent finishes and is waiting for input (or keeps `⏳` if background tasks are still running).
- **Per-Pane PTY Isolation:** Walks `/proc` up to the session/multiplexer boundary to write ANSI OSC 0/2 sequences directly to the agent's own ancestor `/dev/pts/*` PTY, so unfocused panes are never overwritten.
