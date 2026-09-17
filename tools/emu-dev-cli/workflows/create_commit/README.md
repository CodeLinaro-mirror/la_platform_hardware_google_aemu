# Create Commit Workflow (`create-commit`)

Guides developers and AI agents in drafting a commit message in a cache file, validating it against Gerrit review guidelines, and applying the commit to the working tree.

## Verification Requirements

The workflow enforces a safe two-step process:
1. **Step 0 (`Draft Proposed Commit Message`)**:
   - The user/agent writes the proposed message into `{cache-dir}/proposed_commit_msg_{state_id}.txt`.
   - **Forbidden Tags Ban**: Ensures no `Tag=` or `CONV=` metadata tags are included.
   - **Bug Tracking Line**: Enforces a valid `Bug: <number>` line or `Bug: N/A`.
   - **Test Command Line**: Enforces a valid `Test: <command>` or `TEST: N/A` line.
2. **Step 1 (`Create Commit`)**:
   - The user/agent creates the commit using the verified message.
   - **New Commit Created**: Verifies that a new commit has been generated on HEAD relative to the session base commit.
   - **Message Integrity**: Verifies that the Git commit message matches the approved proposed message file.

## Usage

Initialize the workflow (requires no arguments; operates in the current working directory):
```bash
emu-dev-cli workflow create-commit init
```

Follow the prompt to write the proposed message into the specified cache file, verify it, and apply the commit:
```bash
emu-dev-cli workflow create-commit --state=<state_id>
```
