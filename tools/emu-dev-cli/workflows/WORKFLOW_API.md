# Declarative Workflow API Reference (YAML: `workflow.yaml`)

This document defines the schema, lifecycle, and API contract for authoring
declarative, hook-enforced workflows in `emu-dev-cli`.

---

## 1. Quick Start: Creating a New Workflow

To create a new workflow:

1. Create a directory: `tools/emu-dev-cli/workflows/<workflow_name>/`
2. Create `workflow.yaml` using
   [`workflow-template.yaml`](./workflow-template.yaml) as your starter.

That's it! `emu-dev-cli workflow <name> init` automatically discovers and runs
`workflow.yaml` matching the workflow's `name`. No `main.py` or Python code
required.

---

## 2. Top-Level Specification Fields

| Field         | Type     | Required | Description                                                                        |
| :------------ | :------- | :------- | :--------------------------------------------------------------------------------- |
| `name`        | `string` | **Yes**  | Unique identifier for the workflow (e.g. `"git-commit"`, `"hello-world-example"`). |
| `description` | `string` | **Yes**  | One-sentence description shown when running `emu-dev-cli workflow`.                |
| `args`        | `array`  | No       | Positional or named CLI arguments accepted by `init`.                              |
| `init`        | `object` | No       | Variables initialized once upon session creation. Supports commands and literals.  |
| `steps`       | `array`  | **Yes**  | Ordered array of step objects defining the workflow lifecycle.                     |

---

## 3. Workflow CLI Arguments (`"args"`)

Workflows can declare CLI parameters accepted during session initialization
(`emu-dev-cli workflow <name> init [arguments...]`).

### Specification Format:

```yaml
args:
  - name: bug-number
    help: "Buganizer bug number (e.g. 553514805 or b/553514805)"
    required: true
```

Parsed arguments are automatically stored in the session's `metadata` and made
available across all prompts and commands as `{arg_name}` (e.g.,
`{bug-number}`). If `required: true`, the CLI will enforce that the argument is
provided when calling `init`.

---

## 4. Generic System Metadata & The Optional `"init"` Section

### Generic System Metadata

The workflow engine automatically initializes and seeds standard, system-managed
metadata values into every session upon `emu-dev-cli workflow <name> init`.
These values are saved directly into the session's state file under `metadata`
and are immediately available across all `init` commands, step prompts,
verifiers, and error handlers:

| Metadata Key | Description                                                                                                                            | Example Value                                             |
| :----------- | :------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------- |
| `cache-dir`  | Workflow-specific cache directory (`~/.cache/emu-dev-cli/workflows/<workflow-name>/`), created automatically with `0o700` permissions. | `/home/user/.cache/emu-dev-cli/workflows/bug-to-markdown` |

> [!NOTE] **Extensibility**: The engine is architected to support additional
> generic metadata values in the future (e.g., repository roots, workspace info,
> git branch/commit state, target architecture, or environment identifiers)
> uniformly across all workflows without requiring individual workflows to
> re-implement boilerplate discovery scripts.

### The Optional `"init"` Section: Custom Workflow Variables

Workflows that only need generic scratch space (like `bug-to-markdown`) do not
need an `init` section at all. The `init` section is reserved for custom,
workflow-specific initialization that needs to run once when a session starts.

Evaluated results are merged into the session's `metadata` alongside the generic
metadata.

#### Two Types of Custom Values:

1. **Shell Command**:

   ```yaml
   hello-file:
     cmd: "mktemp {cache-dir}/hello-workflow-XXXXXX.txt"
   ```

   Executes safely via parameterized execution and stores trimmed `stdout` in
   `metadata["hello-file"]`. Commands can reference generic metadata (like
   `{cache-dir}`) and declared CLI `args` (like `{bug-number}`).

2. **Literal Primitive**:
   ```yaml
   max-attempts: 3
   mode: strict
   debug: true
   ```
   Stores the literal value directly in `metadata`.

---

## 5. Variable Interpolation & Security

Any string in `prompt`, `verifier_cmd`, `error_prompt`, `stuck_prompt`, or
`stuck_reason` can use `{placeholder}` syntax.

For commands (`verifier_cmd` and `init` commands), the engine executes commands
via **parameterized execution (`shell=False`)** with arguments tokenized using
`shlex.split`. Variables are interpolated into individual argument tokens in a
single pass, preventing command injection vulnerabilities on both POSIX and
Windows without relying on fragile shell escaping.

### Available Variables Summary

#### 1. Dynamic Execution Variables (Evaluated per step)

- `{step}`: Current 0-indexed step number.
- `{retries}`: Number of consecutive failed verification attempts on the current
  step.
- `{max_retries}`: Max allowed retries for the active step.
- `{verifier_error}`: Trimmed stdout/stderr from failed verifier commands.

#### 2. Session Identification Variables

- `{workflow_name}`: Name of the active workflow.
- `{workflow_dir}`: Absolute path to the workflow directory.
- `{state_id}`: Unique 8-character session hex identifier.

#### 3. Persistent Session Metadata Variables (Stored in `STATE-<id>.yaml`)

- **Generic Metadata**: Pre-seeded by the engine (`{cache-dir}`).
- **CLI Arguments**: Declared in `args` and passed to `init` (e.g.
  `{bug-number}`).
- **Custom Init**: Evaluated from the workflow's `"init"` section (e.g.
  `{hello-file}`).

---

## 6. Step Specification Fields (`steps`)

Each entry in `steps` defines a discrete phase:

| Field          | Type             | Required | Description                                                                                     |
| :------------- | :--------------- | :------- | :---------------------------------------------------------------------------------------------- |
| `step`         | `integer`        | **Yes**  | 0-indexed step number (`0`, `1`, `2`, ...).                                                     |
| `title`        | `string`         | **Yes**  | Short human-readable title (e.g. `"Write Name"`, `"Run Tests"`).                                |
| `prompt`       | `array<str>`     | **Yes**  | Guidance prompt shown to the agent. The runner automatically appends continuation instructions. |
| `verifier_cmd` | `string \| null` | **Yes**  | Command executed to verify completion. Set to `null` on the terminal completion step.           |
| `max_retries`  | `integer`        | No       | Number of failed attempts before state becomes `STUCK` (default: 3).                            |
| `error_prompt` | `array<str>`     | No       | Prompt displayed when `verifier_cmd` fails and `retries < max_retries`.                         |
| `stuck_prompt` | `array<str>`     | No       | Prompt displayed when `retries >= max_retries`.                                                 |
| `stuck_reason` | `string`         | No       | String recorded in the state YAML when stuck.                                                   |

---

## 7. Execution Lifecycle & Status Flow

```mermaid
stateDiagram-v2
    [*] --> INIT: emu-dev-cli workflow <name> init
    INIT --> RUNNING: emu-dev-cli workflow <name> --state=<id> (Step 0)

    state RUNNING {
        [*] --> Verifying
        Verifying --> Passed: verifier_cmd == 0
        Verifying --> Retry: verifier_cmd != 0 (retries < max_retries)
        Retry --> Verifying: --state=<id>
    }

    RUNNING --> STUCK: retries >= max_retries
    STUCK --> RUNNING: --state=<id> with fix (verifier_cmd == 0)
    RUNNING --> DONE: Final step passed (verifier_cmd is null)
    DONE --> [*]
```

---

## 8. State Management & Persistence (`STATE-<id>.yaml`)

State files are saved per workflow under:

```text
~/.cache/emu-dev-cli/workflows/state/<workflow_name>/STATE-<state_id>.yaml
```

The storage directory can be customized using environment variables (checked in
order of precedence):

1. `EMU_DEV_CLI_WORKFLOW_STATE_DIR`: Overrides the base state directory
   directly.
2. `XDG_CACHE_HOME`: Defaults to `$XDG_CACHE_HOME/emu-dev-cli/workflows/state/`.
3. Default fallback: `~/.cache/emu-dev-cli/workflows/state/`.

### State File Schema

```yaml
state: RUNNING # Lifecycle status: 'INIT', 'RUNNING', 'STUCK', or 'DONE'
step: 1 # Current 0-indexed active step number
retries: 0 # Consecutive verification failures for the current step
stuck_reason: "" # Detailed failure message when stuck (supports multi-line block `|-`)
metadata: # Key-value store seeded with generic metadata and populated during "init"
  cache-dir: /home/user/.cache/emu-dev-cli/workflows/hello-world-example
  hello-file: /home/user/.cache/emu-dev-cli/workflows/hello-world-example/hello-workflow-123456.txt
```

### State Fields Reference

| Field          | Type      | Description                                                                                                                 |
| :------------- | :-------- | :-------------------------------------------------------------------------------------------------------------------------- |
| `state`        | `string`  | High-level status: `INIT` (created), `RUNNING` (in progress), `STUCK` (max retries reached), or `DONE` (completed).         |
| `step`         | `integer` | The current active step number (matches `step` in `workflow.yaml`).                                                         |
| `retries`      | `integer` | Count of failed verification attempts on the current step; resets to `0` when a step passes.                                |
| `stuck_reason` | `string`  | Human-readable explanation when stuck. Rendered as a multi-line literal block (`\|-`) if captured output contains newlines. |
| `metadata`     | `object`  | Persistent key-value dictionary seeded with generic metadata, args, and custom init values. Used for `{key}` substitution.  |

### CLI Argument Resolution

The `--state` CLI argument is resolved flexibly by the runner:

- **Bare 8-character ID**: `emu-dev-cli workflow <name> --state=38a492be`
- **File Basename**: `emu-dev-cli workflow <name> --state=STATE-38a492be.yaml`
- **Absolute / Relative Path**:
  `emu-dev-cli workflow <name> --state=/path/to/STATE-38a492be.yaml`
