# Test-Driven Bug Fixer Workflow (`test-driven-bug-fixer`)

Autonomous TDD bug-fixing workflow for Android Emulator codebases (`emu-main-dev` and `emu-main-next`).

## Workflow Overview

1. **Step 0 (`bug-to-markdown`)**: Fetches Buganizer issue details and generates a structured markdown summary in `{cache-dir}/b{bug-number}.md`.
2. **Step 1 (`create-test`)**: Guides the agent to author a reproducing test that fails against unfixed code. Verifies compilation (`cmake.py --task Compile` in `emu-main-dev`, `bazel build @goldfish//emulator/launcher:launch_emulator` in `emu-main-next`).
3. **Step 2 (`commit-test`)**: Invokes the `create-commit` sub-workflow to commit the test code in its own commit meeting Gerrit guidelines.
4. **Step 3 (`apply-fix`)**: Guides the agent to implement the fix using the proposed solution from the markdown document. Verifies compilation.
5. **Step 4 (`commit-fix`)**: Invokes the `create-commit` sub-workflow to commit the bug fix in its own commit meeting Gerrit guidelines.
6. **Step 5 (`summary`)**: Summarizes the completed commits and bug resolution.

## Usage

```bash
# Initialize workflow for a bug:
emu-dev-cli workflow test-driven-bug-fixer init 553514805

# Advance workflow:
emu-dev-cli workflow test-driven-bug-fixer --state=<state_id>
```
