# Triage Crashes Workflow (`triage-crashes`)

Discover untriaged `qemu-next` production crashes from `crash.prod.latest`, pre-build a compact triage dossier with symbolicated stack frames and annotations, and verify that every signature in the batch is linked to a Buganizer issue in `go/crash`.

## Usage

```bash
# 1. Initialize the daily triage batch (default: last 48 hours, up to 10 clusters)
emu-dev-cli workflow triage-crashes init --hours 48 --limit 10

# 2. Start / advance the workflow session
emu-dev-cli workflow triage-crashes --state=<STATE_ID>
```

## How It Minimizes Agent Token Usage

1. **Pre-Triage & Auto-Linking (`init`):**
   - `prepare_triage_batch.py` queries `crash.prod.latest` joined with `crash_annotations.prod.latest` and live `SearchAnnotations` on `blade:crash-queryapi`.
   - Any unassigned signature that already has an exact Buganizer issue match in Component `29601` is automatically linked in `go/crash` during `init` with zero LLM tokens spent.
   - For all remaining untriaged clusters, `prepare_triage_batch.py` extracts the top 8 symbolicated crashing stack frames, `looper_registrations`, `internal-msg`, `list_annotations`, and `build_ids` into a single compact Markdown file (`dossier-<state_id>.md`).
2. **Direct One-Shot Bug Filing (`--from-rca`):**
   - The agent reads `dossier-<state_id>.md`, inspects the faulting source file, writes `rca-<report_id>.md`, and runs:
     ```bash
     emu-dev-cli crash file-bug <report_id> --signature "<stable_signature>" --from-rca "<cache_dir>/rca-<report_id>.md"
     ```
   - This creates/updates the Buganizer ticket and links it in `go/crash` directly without spawning a nested AI session.
3. **Hard `go/crash` Verification (`verify_triage_batch.py`):**
   - Queries `blade:crash-queryapi` (`SearchAnnotations`) and only completes the workflow once 100% of signatures in the batch have a linked `BugUrl` in `go/crash`.
