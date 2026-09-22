# Analyze Build Times Workflow (`analyze-build-times`)

Analyzes Android Emulator CI build times and Bazel `logs/bazel/command.profile.gz` traces across `emu-main-next` targets (`emulator_linux_x64`, `emulator_linux_x64_asan`, `emulator_linux_x64_debug`, `emulator_linux_x64_tsan`, `emulator_mac_aarch64`, `emulator_mac_aarch64_debug`, `emulator_windows_x64`, `emulator_windows_x64_debug`).

## Usage

```bash
emu-dev-cli workflow analyze-build-times init --target emulator_mac_aarch64 --limit 10
```
