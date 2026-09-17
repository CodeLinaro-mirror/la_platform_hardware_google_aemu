# Bug to Markdown Workflow

Automated workflow that guides developers and agents to collect bug details and
produce a standardized `b<bug number>.md` summary file in the user-local cache
directory.

## Required Fields:

- `Bug number`: The numeric Buganizer issue ID.
- `Status`: Current bug status (e.g. `Assigned`, `New`, `In Progress`).
- `Assignee`: Assigned LDAP user or `None`.
- `Problem`: Brief summary of the bug problem.
- `Proposed Fix`: Proposed fix or `N/A`.

## Usage:

```bash
# Initialize with target bug number
emu-dev-cli workflow bug-to-markdown init <bug-number>

# Check progress and verify output
emu-dev-cli workflow bug-to-markdown --state=<state-id>
```

## Output Location:

`~/.cache/emu-dev-cli/workflows/bug_to_markdown/b<bug number>.md`
