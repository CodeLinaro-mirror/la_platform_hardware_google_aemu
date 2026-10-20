# Android Emulator Custom Agent Harness (`aemu-harness`)

`aemu-harness` is an experimental **Jetski Web & Jetski CLI Custom Agent Harness** packaged as a native Jetski plugin inside `emu-dev-cli`.

Rather than replacing the Jetski runtime, `aemu-harness` extends Jetski with deterministic Android Emulator guardrails, declarative workflow state persistence, specialized subagents, and an interactive **AuxPane Control Center** in Jetski Web.

## Quick Start

Install or update the harness plugin into `~/.gemini/config/plugins/aemu-harness`:

```bash
emu-dev-cli harness install
# Or automatically via:
emu-dev-cli init
emu-dev-cli install
```

Inspect active harness status, workflows, and session ledgers:

```bash
emu-dev-cli harness status
```

## What's Inside `aemu-harness`

1. **Deterministic Lifecycle Hooks (`hooks.json`)**:
   - **`PreToolUse` (`aemu-git-gerrit-guardrail`)**:
     - Intercepts direct `git commit -m` / `git commit -F` commands and validates the commit message against `emu-dev-cli`'s Semantic Commit Message verifier (`<type>[optional scope]: <Description>`, `<= 50` char subject, mandatory explanatory body `<= 72` chars, `Bug:` and `Test:` footers, no `Tag=`/`CONV=`).
     - Intercepts `gerrit vote`, `gerrit messages post`, `gerrit comments publish`, and `gob-curl .../review` commands to enforce `--notify=OWNER` / `"notify": "OWNER"` (`b/567559614`).
   - **`PostToolUse` (`aemu-session-ledger-tracker`)**:
     - Tracks `emu-dev-cli launch emulator|mesh` and teardown commands in the per-conversation Session Ledger (`~/.cache/emu-dev-cli/harness/sessions/<conversationId>.json`).
   - **`PreInvocation` (`aemu-workflow-context-injector`)**:
     - Automatically injects active `emu-dev-cli workflow` sessions (`RUNNING`, `RETRY`, `WAITING_HUMAN`) and tracked running AVDs into the prompt via `<ephemeralMessage>` so the agent never loses workflow state across context compactions.
   - **`Stop` (`aemu-workflow-continuation-guard`)**:
     - Automatically continues the turn (up to 3 times) if the conversation has an active `RUNNING` `emu-dev-cli` workflow that hasn't reached `DONE` or `WAITING_HUMAN`.

2. **Always-On Emulator Engineering Rules (`rules/AGENTS.md`)**:
   - Repository architecture map (`emu-main-next` / `goldfish` Bazel vs `emu-main-dev` / `external/qemu` CMake vs `studio-main` / `aemu`).
   - Mandatory reuse of `//emulator/libs/*` (`IniFile`, `goldfish/file/file.h`, `android::base::System`, `goldfish::http::WebServer`, `goldfish::network::HttpClient`, `goldfish::async`).
   - Cross-platform Windows UTF-8 file I/O and `cc_binary_with_manifest`.
   - `absl::StatusOr` (TotW 181) and actionable error messages (`go/error-messages`).

3. **Specialized Emulator Agents (`agents/*.md`)**:
   - **`aemu-harness-orchestrator`** (selectable in the Jetski Web agent dropdown or via subagent): Structured multi-track orchestrator (`BUG_FIX`, `FLAKE_REPRO_FIX`, `CRASH_TRIAGE`, `CODE_REVIEW`).
   - **`aemu-code-reviewer`**: Subagent that runs the 5-way code complexity audit (`duplication`, `modularity`, `control-flow`, `constants`, `unicode`), `//emulator/libs/*` reuse audit, `go/error-messages` audit, and Semantic Commit check.
   - **`aemu-flake-hunter`**: Subagent that inspects ATH / Studio `TestActions` flake history and enforces local 50x stress reproduction (`--config=tsan`, `--config=asan`, `--config=remote-windows`) before proposing fixes.

4. **Jetski Web AuxPane Control Center (`sidecars/aemu_harness_ui/`)**:
   - Appears in the right-hand auxiliary pane in Jetski Web (`SIDECAR_UI_ENTRYPOINT_AUX_PANE`).
   - Shows live conversation guardrail stats, active workflows (with one-click `Approve` for `WAITING_HUMAN` barriers), and one-click prompt dispatch buttons via `window.sidecar.agent.sendMessage(...)`.
