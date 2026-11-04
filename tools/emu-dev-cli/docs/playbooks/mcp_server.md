# MCP Server Playbook (`mcp_server.md`)

**Role:** Operational Playbook for serving `emu-dev-cli` to AI agents over the
Model Context Protocol (MCP)
**Location:** `hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/`

## Overview

`emu-dev-cli mcp serve` turns the most useful `emu-dev-cli` commands into MCP
tools. An agent such as Jetski or Claude Code starts the server, discovers the
tools with typed input schemas, and calls them without shell quoting or output
parsing. Each call runs the same command code as the CLI, in the same process,
and returns the JSON that `emu-dev-cli --json <command>` prints.

---

## 1. Quick Start

```bash
# Start the server on stdin/stdout (what an agent does for you).
emu-dev-cli mcp serve

# Only offer tools that never change Gerrit, git, or chat state.
emu-dev-cli mcp serve --read-only

# Talk to it by hand with the MCP CLI.
MCP=/google/bin/releases/gemini-agents-mcp/mcp_cli
$MCP tools --binary="$(command -v emu-dev-cli)" --server_args=mcp,serve
$MCP call --binary="$(command -v emu-dev-cli)" --server_args=mcp,serve \
  --tool gerrit_stack_status --args '{"cl": "43489759"}'
```

`emu-dev-cli mcp` without a subcommand also starts the server.

---

## 2. Agent Configuration

Point the agent at the `emu-dev-cli` binary and set `cwd` to your checkout:
relative paths, including the default `repo` of `.`, resolve against the
server's working directory.

### Jetski

Add an entry to `~/.gemini/config/mcp_config.json` (or use **MCP Servers >
Manage MCP Servers > View raw config**):

```json
{
  "mcpServers": {
    "emu-dev-cli": {
      "command": "/usr/local/google/home/<you>/.android/bin/emu-dev-cli",
      "args": ["mcp", "serve"],
      "cwd": "/usr/local/google/home/<you>/work/emu-main-next/hardware/google/aemu"
    }
  }
}
```

Add `"disabledTools": ["gerrit_stack_sync"]` to hide individual tools, or use
`"args": ["mcp", "serve", "--read-only"]` to hide every tool that changes state.

### Claude Code

```bash
claude mcp add --transport stdio emu-dev-cli -- emu-dev-cli mcp serve
```

Or check a project-scoped `.mcp.json` into the workspace root:

```json
{
  "mcpServers": {
    "emu-dev-cli": {
      "type": "stdio",
      "command": "emu-dev-cli",
      "args": ["mcp", "serve"]
    }
  }
}
```

Other MCP clients work the same way: run `emu-dev-cli` with the arguments
`mcp serve` over stdio.

---

## 3. Tools

| Tool                      | Runs                           | Changes state                          |
| :------------------------ | :----------------------------- | :------------------------------------- |
| `gerrit_stack_status`     | `gerrit stack status`          | No                                     |
| `gerrit_stack_pre_upload` | `gerrit stack pre-upload`      | No                                     |
| `gerrit_stack_sync`       | `gerrit stack sync`            | Rebases and pushes, with `dry_run=false` |
| `gerrit_stack_watch`      | `gerrit stack watch`           | Rebases and pings, when asked to       |
| `gerrit_ping_for_review`  | `gerrit ping-for-review`       | Sends a Chat DM                        |
| `gerrit_comments`         | `gerrit comments`              | No                                     |
| `gerrit_reply`            | `gerrit reply`                 | Posts review replies                   |
| `crash_list`              | `crash list-crashes`           | No                                     |
| `crash_find_bug`          | `crash find-bug`               | No                                     |
| `crash_reproduce`         | `crash reproduce` (plan only)  | No                                     |
| `perf_compare`            | `perf compare`                 | Starts emulators for live builds       |
| `tidy_check`              | `tidy check`                   | No (runs a Bazel build for a target)   |

`tools/list` reports each tool's schema, and `readOnlyHint` /
`destructiveHint` annotations let the client ask before running the tools that
change state.

---

## 4. How a Tool Call Works

- **Arguments are the CLI flags.** Each schema is read from the command's own
  argparse parser, so names, types, choices, and defaults match the CLI:
  `--poll-interval` becomes `poll_interval`, a positional CL becomes `cl`. A
  flag like `--no-rebase` keeps its name (`no_rebase`), so `true` always means
  "pass the flag". `--json` and credential flags such as `--token` are never
  exposed.
- **Results are the `--json` output.** The JSON object becomes
  `structuredContent`, and the same JSON is the text content. Anything the
  command wrote to stderr comes back as a second text block.
- **Errors stay with the model.** Bad arguments and failed commands return a
  normal result with `isError: true` and the CLI's error JSON (`error_code`,
  `error_message`, `remediation`), so the agent can fix the call and retry. Only
  an unknown tool name or a malformed request is a JSON-RPC error.
- **State changes are opt-in.** Three defaults differ from the CLI:

  | Tool                     | MCP default                   | To act                                  |
  | :----------------------- | :---------------------------- | :-------------------------------------- |
  | `gerrit_stack_sync`      | `dry_run=true`                | `dry_run=false`                         |
  | `gerrit_stack_watch`     | `once=true`, `no_rebase=true` | `once=false` + `timeout`, `no_rebase=false` |
  | `gerrit_ping_for_review` | `no_wait=true`                | `no_wait=false` + `timeout`             |

- **`crash_reproduce` only plans.** Over MCP it returns the emulator build, AVD
  settings, and command lines, and never downloads or launches the emulator.

---

## 5. Protocol Behavior

- **Transport:** newline-delimited JSON-RPC 2.0 on stdin/stdout. The server
  speaks MCP `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`, and
  answers `initialize` with the client's version when it is one of these.
  Clients that probe with newer methods first (such as `server/discover`) get
  "method not found" and fall back to `initialize`.
- **The client owns stdin and stdout:** while the server runs, file descriptor
  0 reads an empty stream and file descriptor 1 points at stderr, so commands
  and their child processes cannot swallow requests, block on a prompt, or
  corrupt the protocol stream with stray output. Logs go to stderr, which MCP
  clients keep in their server logs.
- **One call at a time:** tool calls run in order on a worker thread, while
  `ping` and cancellations are answered right away.
- **Cancellation:** `notifications/cancelled` drops the result of the call.
  `gerrit_stack_watch` and `gerrit_ping_for_review` also stop at their next
  poll; other commands run to completion first. Calls still waiting in the
  queue never start.
- **Shutdown:** when stdin closes, the server finishes the calls it already
  accepted and exits. Ctrl-C cancels them instead.

---

## 6. Adding a Tool

1. Add a `ToolSpec` to `default_tools()` in `src/commands/mcp/tools.py`: the
   tool name, a one-paragraph description written for the model, and the
   `Command` class. Set `group=GerritCommand` for `gerrit` subcommands, which
   read the group's `--host`.
2. Use `hidden` for flags that do nothing in JSON mode, `defaults` to keep a
   state-changing command safe by default, `read_only=False` (and
   `destructive=True` if it rewrites or deletes something) for commands that
   change state, and `takes_sleep_fn=True` for polling commands that accept a
   `sleep_fn`, so cancellation can stop them.
3. Extend `tests/commands/test_mcp_server_test.py`: the expected tool set, and a
   hermetic call through `FakeServiceFactory` when the command can run without
   network access.

---

## 7. Troubleshooting

- **The agent does not list the tools:** run the `mcp_cli tools` command from
  section 1; it shows startup errors directly. In Jetski, check that the
  workspace is trusted and that the server is enabled under **MCP Servers**.
- **A Gerrit, Buganizer, or F1 tool returns an auth error:** run `gcert` in the
  environment the agent starts the server from, then call the tool again.
- **A call takes minutes:** `tidy_check` with a target and `perf_compare` with
  live builds are slow by nature, and later calls wait for them. Cancel the call
  from the client if needed.

### Running the tests

```bash
# From the hardware/google/aemu checkout:
bazel test //tools/emu-dev-cli:commands_test_mcp_server_test
```
