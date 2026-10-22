# Compare Performance Workflow (`compare-perf`)

Executes a standardized, cross-platform (`linux-x64`, `mac-arm64`, `mac-x64`) A/B performance qualification benchmark comparing a baseline emulator (default: official `stable` release from `dl.google.com`) against a candidate build (default: `latest:emu-main-next` from `go/ab`, or `tree` from the local workspace checkout), verifies all 22 macro/micro workloads across 9 suites, and produces an unredacted 4-part Weekly Emulator Performance Qualification Report.

## Usage

```bash
# 1. Default weekly qualification: official stable vs latest emu-main-next on go/ab (works on Linux & macOS)
emu-dev-cli workflow compare-perf init

# 2. Compare official stable vs local workspace build (//:emulator-repo-release)
emu-dev-cli workflow compare-perf init --baseline stable --candidate tree

# 3. Compare two specific Android Build IDs or subset of suites
emu-dev-cli workflow compare-perf init --baseline 16428233 --candidate 16540112 --suite adb,input,disk,memory,cpu

# 4. Advance the workflow session
emu-dev-cli workflow compare-perf --state=<STATE_ID>
```

## Weekly Scheduling

To run this qualification automatically every week in Jetski, use the `/schedule` command (or `schedule` tool) with cron expression `0 9 * * 1` (Mondays at 9:00 AM):

```text
Run `emu-dev-cli workflow compare-perf init --baseline stable --candidate latest:emu-main-next` and share the full weekly performance qualification report.
```
