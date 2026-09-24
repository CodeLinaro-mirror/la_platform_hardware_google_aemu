# Amend Commit Workflow (`amend-commit`)

Guides developers and AI agents in amending the latest Git commit (`HEAD`) while preserving its Gerrit `Change-Id`.

## Two-File Architecture

During initialization (`init`), the workflow sets up two dedicated files in the session cache directory:
1. `current_commit_msg_{state_id}.txt`: The workflow prompts the user or AI agent to copy the current commit message into this file for reference and verification.
2. `proposed_commit_msg_{state_id}.txt`: Initialized as an empty file for the user/agent to write the amended message.

## Verification Requirements

The workflow enforces a safe two-step process:
1. **Step 0 (`Draft Proposed Commit Message`)**:
   - The user/agent reviews the current message in `{current_commit_msg}` and writes the proposed amended message into `{proposed_commit_msg}`.
   - **Single Change-Id Preservation**: Enforces that exactly one Gerrit `Change-Id` line exists in the message (disallowing duplicate or multiple Change-Id lines) and that the hash matches the original commit's `Change-Id` exactly.
   - **Forbidden Tags Ban**: Ensures no `Tag=` or `CONV=` metadata tags are included.
   - **Bug Tracking Line**: Enforces a valid `Bug: <number>` line or `Bug: N/A`.
   - **Test Command Line**: Enforces a valid `Test: <command>` or `TEST: N/A` line.
2. **Step 1 (`Amend Commit`)**:
   - The user/agent amends the commit using the verified message.
   - **Commit Amended**: Verifies that the target commit has been amended (its commit hash has changed).
   - **No Extraneous Commits**: Verifies that the base commit is not an ancestor of the amended commit (ensuring an amend took place rather than committing on top).
   - **Message Integrity**: Verifies that the Git commit message matches the approved proposed message file.

## Usage

Initialize the workflow (requires no arguments; operates on `HEAD` in the current working directory):
```bash
emu-dev-cli workflow amend-commit init
```

Follow the prompt to draft the proposed message, verify it, and amend the commit:
```bash
emu-dev-cli workflow amend-commit --state=<state_id>
```
