# Address Gerrit Review Workflow (`address-review`)

Declarative, multi-round workflow that automates the Gerrit code review cycle:
1. **Fetch & Checkout (`init`)**: Queries Gerrit for unresolved human and AI (`groupagent-android-review`) comments on a target CL (or all open CLs with `--all-open true`), checks out the latest patchset locally, and generates a Markdown dossier (`{dossier-file}`) and machine queue (`{queue-file}`).
2. **Step 0 — Fix, Stage Replies, & Test**: The agent applies code/test fixes, stages per-comment replies via `emu-dev-cli gerrit reply ... --stage-only`, and runs `mise presubmit` (or custom `--test-cmd`).
3. **Step 1 — Amend Commit (`amend-commit` sub-workflow)**: Enforces commit message hygiene and guarantees the existing Gerrit `Change-Id` is preserved.
4. **Step 2 — Upload, Resolve, & Poll Loop**: Uploads the new patchset (`repo upload . --cbr -y --label Presubmit-Ready+1`), publishes all staged replies with `unresolved: false`, and polls Gerrit for follow-up review comments.
   - If new unresolved comments arrive during the poll window, `{dossier-file}` and `{queue-file}` are refreshed and the workflow automatically loops back to **Step 0** (`loop_to_step: 0`).
   - If review feedback arrives hours later, run `emu-dev-cli gerrit resume <cl>` to reset the session to Step 0 and wake the original Jetski conversation via `agentapi`.

## Usage

```bash
# Single CL (by CL number, Gerrit URL, Change-Id, or local HEAD)
emu-dev-cli workflow address-review init --cl 42659931

# Batch mode: all open CLs owned by self with unresolved comments
emu-dev-cli workflow address-review init --all-open true

# Resume an existing review session when new comments arrive later
emu-dev-cli gerrit resume 42659931
```
