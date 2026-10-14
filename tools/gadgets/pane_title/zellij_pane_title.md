---
trigger: always_on
description: "Keep the current terminal/Zellij pane title updated with a concise summary of the active task or bug."
---

# Terminal & Zellij Pane Title Policy

1. **Set Semantic Problem Title on Task Start/Switch**:
   Whenever you start working on a new problem, bug, or feature (or switch topics), update the current pane/tab title with a concise 3–5 word summary (including any bug ID like `b/123456` or branch/component name) by running:
   ```bash
   python3 ~/.gemini/config/hooks/zellij_pane_title.py --set "<concise problem title>"
   ```
   Examples:
   - `python3 ~/.gemini/config/hooks/zellij_pane_title.py --set "b/555286414: Keyboard DPAD"`
   - `python3 ~/.gemini/config/hooks/zellij_pane_title.py --set "win-aarch64 toolchain"`

2. **Target Only the Current Pane**:
   Always use `~/.gemini/config/hooks/zellij_pane_title.py` instead of `zellij action rename-pane`. The script walks `/proc` to write the OSC title directly to the ancestor PTY of the pane you are currently running in, ensuring unfocused panes are never overwritten and `PreInvocation` / `Stop` hooks automatically toggle `⏳` (working) and `✅` (idle/done).
