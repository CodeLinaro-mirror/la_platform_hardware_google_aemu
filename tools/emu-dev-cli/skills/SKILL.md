---
name: emu_dev_cli
description:
  CLI tool for codebase architecture discovery (docs search/index/lint), fetching prebuilt emulator
  binaries and system images from Android Build (go/ab), creating AVDs, launching emulator instances,
  executing automated CTS-Verifier tests, discovering production qemu-next crashes from go/crash
  (crash list-crashes --unassigned-only), linking Buganizer issues back to go/crash (crash link-bug),
  investigating crashes with LLDB and autonomous AI fixes, querying Android Test Hub (ATH) flakiness,
  inspecting test execution history, reproducing flaky Bazel tests under ASAN/TSAN/Windows/macOS,
  triaging Buganizer issues with AI patches, fixing flaky tests, and running mise/clang-tidy tasks.
---
# Android Emulator Developer CLI (`emu-dev-cli`) & Goldfish Codebase Guide

`emu-dev-cli` (`emudev`) is the CLI for `hardware/generic/goldfish` (`@goldfish`) and `hardware/google/aemu` (`@aemu`) codebase discovery, emulator/AVD/mesh lifecycle, CTS-Verifier, crash triage, flakiness, build-times, and `mise`/`tidy` workflows: `emu-dev-cli <command>`.

---
## ⚡ Fast Codebase & CLI Discovery Protocol
1. **Operational `emu-dev-cli` & `mise` CLI Questions (1-Turn Fast Path):**
   - All `emu-dev-cli` subcommands (`fetch-build`, `source-directory`, `create`, `launch`, `mesh`, `cts`, `init`, `update`, `docs`, `crash`, `flakiness`, `tidy`, `build-times`, `workflow`) and `mise` tasks (`presubmit`, `submit`, `gum`, `fastbuild`, `fasttest`, `tidy`, `bcc`) are documented in **Sections 1–13 below**. Answer CLI usage questions **directly from this `SKILL.md` in 1 turn** (do not read `tools/emu-dev-cli/src/` unless modifying `emu-dev-cli` code).
2. **Locating `@goldfish` Source Files, Symbols & Bazel Targets:**
   - Consult the **Package Directory Map below** OR run `emu-dev-cli docs search "<keywords>"` (searches all 106+ `ARCHITECTURE.md`/`DESIGN.md`/playbooks + `BUILD.bazel` targets + source/header filenames and prints `Targets:` and `Files:` directly).
3. **Batch `view_file` Calls in a Single Parallel Turn:**
   - Once you identify the package directory or candidate files from the map below or `docs search`, issue **all** `view_file` calls (`BUILD.bazel`, `.h`, `.cc`/`.py`, `_test.cc`/`_test.py`) **in parallel in a single turn** rather than reading one file per turn.

### `hardware/generic/goldfish` Package, Target & Symbol Directory
All paths below are relative to `hardware/generic/goldfish/` (Bazel workspace `@goldfish//`):

| Subsystem | Package (`@goldfish//...`) | Key Bazel Targets | Key Headers & Files | Primary Classes, Functions & Macros |
| :--- | :--- | :--- | :--- | :--- |
| **Python Launcher** | `emulator/launcher` | `:launch_emulator`, `:emulator_lib{_test}`, `:process_monitor{_test}`, `:launcher_lib{_test}` | `tools/{launch_emulator,emulator_lib,emulator_lib_test}.py`, `tools/launcher_test.bzl`, `include/android/goldfish/{avd,image_list}.h`, `input_paths.cc` | `launch_and_monitor_emulator`, `EmulatorLocator`, `EmulatorAvd`, `EmulatorCommand`, `EmulatorRunner` (`_launch_device_hub`, `_launch_aquarium`), `AVD_IMAGE_LIST`, `ResolveSystemImagePaths` |
| **QEMU Launch & Drives** | `emulator/launcher/launch_qemu` | `:launch_qemu`, `:configure_drives{_test}`, `:disk_drive{_test}`, `:user_data_drive{_test}`, `:device` | `launch_qemu.{h,cc}`, `device.h`, `{configure_drives,disk_drive,user_data_drive}.{h,cc,_test.cc}` | `LaunchQemu` (`addDevice`, `addDevices`, `initialize`, `launch_config`), `Device`, `PciDevice`, `addDrives`, `internal::getDiskConfigs`, `Qcow2Drive`, `WritableImgDrive`, `BlankWritableDrive`, `WritableDirectoryDrive`, `NullDrive`, `prepareUserdataSrcDir` |
| **CLI Flags & Help** | `emulator/launcher/cmdline` | `:cmdline`, `:cmdline_unittests` | `include/android/cmdline_{options,definitions,option}.h`, `cmdline_option{_test}.cc`, `help.cc`, `main_help.cc` | `AndroidOptions`, `OPT_FLAG`, `OPT_PARAM`, `CFG_FLAG`, `CFG_PARAM`, `OPT_FLAG_OBSOLETE`, `OPT_PARAM_OBSOLETE`, `kOptionKeys`, `android_parse_options`, `kOptionHelp`, `help_<name>`, `HelpDiskImages` |
| **Hardware Config** | `emulator/libs/hardware_config` | `:hardware_config`, `:hw-config-gen`, `:gen-hw-config`, `:fake_hardware_config`, `:memory_config_test` | `data/hardware-properties.ini`, `tools/gen-hw-config.py`, `include/android/goldfish/hardware_config.h`, `hardware_config.cc`, `test/android/goldfish/fake_hardware_config.h` | `HardwareConfig` (`Load`, `Write`, `Accept`), `FakeHardwareConfig`, `HWCFG_*` X-Macros generated into `include/avd/hw-config-defs.h` |
| **Metrics & Analytics** | `emulator/libs/metrics` | `:metrics`, `:metrics_reporter{_test}`, `:metrics_writer`, `:fake_metrics_writer`, `:studio_config{_test}` | `include/goldfish/metrics/{metrics_reporter,metrics_writer,studio_config,testing/fake_metrics_writer}.h`, `{metrics_reporter,studio_config}{,_test}.cc` | `MetricsReporter` (`Report`, `SetWriter`), `MetricsWriter`, `FakeMetricsWriter`, `studio::EnsureAnalyticsSettings`, `GetAnonymizationSalt`, `GetCurrentSaltSkew`, `IsSaltRotationNeeded`, `GenerateRandomSalt` |
| **QEMU Plugin Core** | `emulator/plugin` | `:goldfish_x86_64`, `:goldfish_aarch64` | `plugin.cc`, `BUILD.bazel` | `GF_STARTUP_FUNC`, `GF_SHUTDOWN_FUNC`, `start_tracing`, `stop_tracing`, `OnQemuGoldfishTrace` |
| **Display & Recording** | `emulator/plugin/display` | `:base`, `:primary_display`, `:display{_unittests}`, `:video_recorder`, `:test-display` | `include/goldfish/display/*.h`, `{display,pixman_display,qemu_display,multi_display,video_recorder,webm_encoder}.{h,cc}` | `IDisplay` (`GetPixels`, `WaitForFreshFrame`, `AcquireLatestFrame`), `PixmanDisplay` (`UpdateSurface`, `UpdateSourceImage`, `CaptureSource`), `QemuDisplay`, `IMultiDisplay`, `MultiDisplayImpl` (`grpc_dpy_gfx_update`, `grpc_dpy_gfx_switch`), `VideoRecorder` (`GenerationLoop`), `WebMEncoder` (`AddFrame`, `EncodeLoop`, `ProcessFrame`) |
| **Display Pipeline** | `emulator/libs/display_pipeline` | `:core{_test}`, `:cpu_pipeline{_test}`, `:testing` | `DESIGN.md`, `include/goldfish/display/{display_frames,latest_frame_pump,cpu_read_pipeline,slot_pool,cpu_slot_storage,legacy_surface_source}.{h,cc}` | `DisplayFrames`, `SurfaceFrame`, `LatestFramePump`, `CpuReadPipeline`, `SlotPool`, `CpuSlotStorage`, `LegacySurfaceSource`, `FrameProducer` |
| **VideoBridge & WebRTC** | `emulator/videobridge` | `:videobridge_core`, `:in_process_video_source{_test}`, `:in_process_media_providers`, `:rtc_service{_test}`, `:participant_test`, `:switchboard_test`, `:in_process_input_sender_test`, `:mf_video_encoder_h264`, `:mf_hardware_test` | `include/goldfish/videobridge/*.h`, `media/in_process_video_source{,_test}.cc`, `core/{in_process_input_sender,event_forwarder,participant,switchboard}.{h,cc}`, `core/win/mf_video_encoder_h264.{h,cc}`, `core/win/test/mf_hardware_test.cc` | `InProcessVideoSource` (`ProcessFrame`, `webrtc_dropped_frames`), `DataChannelLabel` (`kInput`, `kInputV2`, `kSensorsV2`), `InputSender`, `InProcessInputSender`, `EventForwarder`, `Participant` (`OnDataChannel`, `AddDataChannel`), `Switchboard`, `MFVideoEncoderH264` (`InitEncode`, `ReleaseUnlocked`, `ProcessOutput`) |
| **gRPC Services (V1)** | `emulator/plugin/grpc{,/services}` | `:grpc`, `:emulator_controller`, `:screen_recorder`, `:screen_recording_impl_test`, `:in_process_rtc_service{_test}` | `grpc.cc`, `services/{emulator_controller,screen_recording_impl,in_process_rtc_service}{,_test}.cc` | `GrpcDev`, `GrpcConfig`, `grpc_shutdown_notify` (`shutdown_notifier`), `EmulatorControllerImpl`, `ScreenRecordingServiceImpl` (`StartRecording`, `StopRecording`, `FramesEncodedForTesting`), `CreateInProcessInputSenderFactory`, `CreateInProcessSwitchboard` |
| **gRPC Methods (V1)** | `emulator/plugin/grpc/services/emulator_controller_methods` | `:emulator_controller_methods{_test}` | `{display_service,sensor_service}.{h,cc,_test.cc}` | `DisplayServiceImpl` (`streamScreenshot`, `getScreenshot`, `dropped_frames`, `grpc_frame_interval_ms`), `SensorServiceImpl` (`getSensor`, `setSensor`, `getPhysicalModel`, `setPhysicalModel`) |
| **gRPC Services (V2)** | `emulator/plugin/grpc/services/v2` | `:v2_services`, `{:rtc,:sensors,:capabilities}_service{,_test}` | `include/goldfish/grpc/v2/v2_services.h`, `v2_services.cc`, `webrtc/rtc_service_impl.{h,cc,_test.cc}`, `environment/sensors_service_impl.{h,cc,_test.cc}` | `CreateV2Services`, `RtcServiceImpl` (`ReceiveJsepMessageStream`, `ParseSwitchboardJsonToJsep`, `SerializeJsepToSwitchboardJson`), `SensorsServiceImpl` (`GetSensorsState`, `StreamSensorsState`, `UpdateSensorsState`, `ApplySensorsUpdate`) |
| **gRPC Interceptors** | `emulator/plugin/grpc/interceptors` | `:interceptors{_test}`, `{:breadcrumb,:perfetto,:metrics}_interceptor{,_test}` | `include/android/control/interceptor/{breadcrumb,perfetto}_interceptor.h`, `{breadcrumb,perfetto}_interceptor{,_test}.cc` | `BreadcrumbInterceptor`, `BreadcrumbInterceptorFactory`, `kDefaultCaptureGrpcPayload`, `SetMessageDetail`, `PerfettoInterceptor`, `PerfettoInterceptorFactory`, `MetricsInterceptor` |
| **Netsim Netdev/Chardev** | `emulator/plugin/netsim` | `:netsim-{netdev,chardev}`, `:netsim_{transport,connection}` | `include/goldfish/netsim/netsim_{netdev,chardev}.h`, `netsim_{netdev,chardev}.cc`, `netsim_transport.{h,cc}` | `netsim_netdev_receive_iov`, `netsim_netdev_send`, `netsim_netdev_send_completed`, `netsim_netdev_nic_info` (`NetClientInfo`), `netsim_chr_open`, `netsim_chardev_set_fe_open`, `NetsimTransport` |
| **ADB-Vsock Logger** | `emulator/plugin/adb-vsock/logger` | `:logger{_unittests}`, `:adb_breadcrumb_tracker_perf` | `include/goldfish/adb/adb_{message_logger,breadcrumb_tracker}.h`, `adb_{message_logger,breadcrumb_tracker}{,_test}.cc` | `AdbMessageLogger`, `AdbLogger`, `AdbBreadcrumbTracker`, `AdbPacketCallback` |
| **Async & EventLoop** | `emulator/libs/async` | `:async`, `{:event_loop,:loop_handoff,:future}_test` | `include/goldfish/async/{event_loop,loop_io,future}.h`, `event_loop{,_test}.cc`, `loop_io.cc` | `EventLoop` (`Post`, `PostAndWait`, `Schedule`), `ThreadedEventLoop`, `LibuvEventLoop`, `LoopIo`, `Future`, `Promise` |
| **Status Macros** | `emulator/libs/status_macros` | `:status_macros`, `:flatten{_test}`, `:status_matcher_macros` | `include/android/status/{status_macros,flatten,status_matcher_macros}.h`, `flatten_test.cc` | `RETURN_IF_ERROR`, `ASSIGN_OR_RETURN`, `flatten_status_or`, `flatten_status_or_t`, `flatten_t`, `EXPECT_OK`, `ASSERT_OK` |
| **Sensors & Foldable** | `emulator/libs/sensors` | `{:physical_model,:foldable_model,:foldable}{,_test}` | `include/goldfish/sensors/{physical_model,goldfish_sensors_list,goldfish_physical_parameters_list,foldable_model}.h`, `{physical_model,foldable_model}{,_test}.cc` | `PhysicalModel` (`GetSensorData`, `SetSensorValue`, `IsSensorOverridden`, `GetPhysicalParameterValue`, `SetPhysicalParameterValue`), `GOLDFISH_SENSORS_LIST`, `GOLDFISH_PHYSICAL_PARAMETERS_LIST`, `FoldableModel` |
| **Perfetto Tracing** | `emulator/libs/perfetto` | `:init{,_test}` | `include/goldfish/perfetto/perfetto{,_categories}.h`, `perfetto_config{,_test}.cc` | `CONFIG_TRACE_GOLDFISH` (`--@qemu//:trace_backend=goldfish`), `TRACE_EVENT`, `TRACE_COUNTER`, `TRACE_EVENT_CATEGORY_ENABLED`, `GOLDFISH_PERFETTO_NOOP`, `EmulatorTracingSession` |
| **Unit Test Main/TSAN** | `emulator/libs/test` | `:googletest_main` | `gtest_main.cc`, `BUILD.bazel`, `leak_suppressions.txt` | `main`, `__tsan_default_options`, `__tsan_default_suppressions`, `SetupSanitizers` |
| **Telnet Console** | `emulator/telnet` | `{:command_registry,:legacy_console_bridge,:legacy_console_controller}{,_test}` | `{command_registry,legacy_console_bridge,legacy_console_controller}.{h,cc,_test.cc}` | `CommandRegistry` (`CommandObserver`, `DispatchCommand`, `GetCanonicalName`), `CommandRegistryBuilder`, `LegacyConsoleBridge` (`RecordCommand`, `ReportMetrics`), `LegacyConsoleController` |
| **Aquarium UI & Web** | `emulator/ui/aquarium`, `@aemu//api/web` | `:aquarium_pkg`, `:web_pkg`, `:example`, `@aemu//api/web:{web,pkg}` | `DESIGN.md`, `BUILD.bazel`, `example/main.tsx`, `hardware/google/aemu/api/web/src/core/{rtc_controller,sensors_channel,input_channel,sensors}.ts` | `RtcController` (`connect`, `setupPeerConnectionEvents`), `SensorsChannel` (`sendUpdate`), `InputChannel`, `SensorsClient` (`startBrowserSensorStream`), `DeviceClient` |
| **Crash Advisor** | `emulator/crashreport/tool/{advisor,breadcrumbs}` | `:advisor{_test}`, `:buganizer_test`, `:breadcrumbs{_test}`, `:breadcrumb_processor_test` | `advisor/{advisor,buganizer,client,metadata,advisor_test,buganizer_test}.py`, `breadcrumbs/include/android/crashreport/breadcrumbs/breadcrumb_processor.h`, `breadcrumbs/{breadcrumb_processor,breadcrumbs_test}.cc` | `CrashAdvisorApp`, `BuganizerClient` (`ensure_crash_hotlists`, `has_new_crash_information`, `format_new_crash_info_comment`), `GossoClient`, `CrashMetadata`, `BreadcrumbProcessor` |
| **Dev Tools & `mise`** | `development{,/tools}` | `//development/tools:generate_bcc`, `//emulator:release`, `//:clang_tidy_report` | `development/mise.toml` (`<workspace_root>/mise.toml`), `development/tools/{clang_tidy,generate_bcc}.py`, `development/fasttest_exclusions.txt` | `mise presubmit`, `mise submit`, `mise gum`, `mise fastbuild`, `mise fasttest`, `mise tidy`, `mise tidy_fix`, `mise bcc` |

---
## 🛠️ Command Reference
### 1. Fetch Artifacts from Android Build (`fetch-build`)
Pulls and extracts prebuilt emulator host binaries or system image archives from `go/ab` into `/tmp/`.

#### A. Fetch Host Emulator (`fetch-build emulator`)
```bash
emu-dev-cli fetch-build emulator --latest                        # emu-main-next (default)
emu-dev-cli fetch-build emulator --latest --branch emu-main-dev  # emu-main-dev
emu-dev-cli fetch-build emulator --build-id 15943073             # specific build ID
```
#### B. Fetch System Image (`fetch-build system-image`)
Auto-detects host CPU architecture (`x86_64` vs `arm64`):
```bash
emu-dev-cli fetch-build system-image --latest                             # trunk-release (default)
emu-dev-cli fetch-build system-image --latest --branch 26Q2-emu-release   # release branch
emu-dev-cli fetch-build system-image --build-id 15900270                  # specific build ID
```
#### C. Update Checked-in Prebuilts (`update-prebuilt`)
Fetches prebuilt artifacts from Android Build (`go/ab`) into `prebuilts/android-emulator-build/common/`:
```bash
emu-dev-cli update-prebuilt --list
emu-dev-cli update-prebuilt lavapipe --build-number=16534139 [--target=linux-x64|mac-arm64|win-x64] [--dry-run]
emu-dev-cli update-prebuilt kosmickrisp --build-number=16476462
emu-dev-cli update-prebuilt android-device-hub --latest [--target=linux-x64|linux-arm64|mac-arm64|win-x64]
```
### 2. Source Directory Registry (`source-directory`)
Manage mappings between branch names (`emu-main-dev`, `emu-main-next`, `git_main`) and local checkout paths in `~/.android/emu-dev-cli.json`:
```bash
emu-dev-cli source-directory get emu-main-dev
emu-dev-cli source-directory set emu-main-dev /work/emu-main-dev
emu-dev-cli source-directory list
```
### 3. Create Android Virtual Devices (`create avd`)
Creates a valid AVD pointer (`~/.android/avd/<name>.ini`) and hardware profile (`~/.android/avd/<name>.avd/config.ini`) bound to a system-image directory. Profiles (`--list-profiles`): `small_phone`, `medium_phone` (default), `medium_tablet`, `small_desktop`, `medium_desktop`, `large_desktop`.
```bash
emu-dev-cli create avd --list-profiles
emu-dev-cli create avd --name my-dev-phone --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ --profile medium_phone --force
emu-dev-cli create mesh --prefix bt-mesh --count 2 --profile medium_phone
```
### 4. Launch Emulator Instances (`launch emulator`)
Launches a prebuilt `emulator` executable (auto-configures `LD_LIBRARY_PATH` for Qt/Vulkan/GLES and verifies X11 `DISPLAY=:20` on CRD):
```bash
emu-dev-cli launch emulator --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator --dry-run -- -avd my-dev-phone
emu-dev-cli launch emulator --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator -- -avd my-dev-phone
emu-dev-cli launch emulator --emulator-dir /tmp/emulator-linux-x64-15942201/extracted/emulator --detached -- -avd my-dev-phone -no-window
emu-dev-cli launch mesh --prefix bt-mesh --count 2 --packet-streamer default --no-window
```
### 5. Mesh Health & Radio Verification (`mesh`)
Monitors mesh nodes, waits for boot completion, unlocks keyguards, and verifies Netsim virtual radio chip registration (`ble`, `classic`, `wifi`):
```bash
emu-dev-cli mesh wait-ready --prefix bt-mesh --count 2 --timeout 180
emu-dev-cli mesh wait-ready --serials emulator-5554,emulator-5556 --timeout 120
emu-dev-cli mesh status
emu-dev-cli mesh teardown --prefix bt-mesh --count 2
emu-dev-cli mesh teardown --all
```
### 6. Automated CTS-Verifier Runner (`cts run-cts-verifier`)
Downloads and executes automated CTS-Verifier test modules (`tts`, `battery_saver`, `vibrations`, `tile_service`, `screen_pinning`, `has_vibrator`, etc.) against an online emulator:
```bash
emu-dev-cli cts run-cts-verifier --list-modules
emu-dev-cli cts run-cts-verifier --module tts
emu-dev-cli cts run-cts-verifier --build-id 15900270 --module vibrations
```
### 7. Onboarding Initialization (`init`) & Executable Rebuild (`update`)
- **Initialize environment and install global agent skills (`init`):** Installs global agent skills
  into `~/.gemini/` and launches interactive workspace source path setup if
  `~/.android/emu-dev-cli.json` is missing:
  ```bash
  emu-dev-cli init
  ```
- **Rebuild & update executable (`update`):** Compares the installed `version.json` against the
  configured local `emu-main-next` checkout, logs whether an update is needed, and rebuilds
  `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli` via Bazel when out of date or `--force`
  (`-f`) is passed (`emu-dev-cli --version` prints the installed version):
  ```bash
  emu-dev-cli update [--force]
  emu-dev-cli --version
  ```
### 8. Codebase Wiki & Documentation Engine (`docs`)
Provides a persistent **LLM Wiki** interface (`Query → Ingest → Lint`) across all `ARCHITECTURE.md`,
`DESIGN.md`, and operational playbooks in the workspace:

- **List all indexed architecture docs and playbooks (`docs index`):**
  ```bash
  emu-dev-cli docs index
  emu-dev-cli docs index --category playbook
  ```
- **Search across architecture docs, playbooks, `BUILD.bazel` targets & source files (`docs search`):**
  ```bash
  emu-dev-cli docs search "VideoRecorder WebMEncoder"
  emu-dev-cli docs search "push.negotiate"
  ```
- **Audit `ARCHITECTURE.md` and `DESIGN.md` for drift (`docs lint`):** Verifies `**Location:**`
  headers, Bazel package labels (`@goldfish//...`, `//...`), and relative markdown links:
  ```bash
  emu-dev-cli docs lint
  emu-dev-cli docs lint --fix
  ```
- **Resolve named documentation & operational playbooks:**
  ```bash
  emu-dev-cli docs playbook-index
  emu-dev-cli docs playbook-build-and-tidy
  emu-dev-cli docs playbook-git-and-gerrit
  emu-dev-cli docs playbook-threading-and-invariants
  emu-dev-cli docs playbook-crash-triage
  emu-dev-cli docs goldfish-architecture
  emu-dev-cli docs cts-verifier-automation
  ```
### 9. Crash Investigation, Triage & Automated Fixing (`crash`)
End-to-end `qemu-next` crash discovery (`list-crashes`), `go/crash` bug linking (`link-bug`), CrashAdvisor minidump diagnostics, stack fingerprinting, Buganizer deduplication across open/closed tickets (`find-bug`), issue filing (`file-bug`), and autonomous AI fixing (`autofix`). See `emu-dev-cli docs playbook-crash-triage`.

- **Discover production `qemu-next` crashes (`list-crashes`):** Queries `crash.prod.latest` joined with `crash_annotations.prod.latest` via F1 SQL (`/google/bin/releases/f1-team/public/f1-sql`) and cross-checks `QueryAPIService.SearchAnnotations` on `blade:crash-queryapi`. Isolates `qemu-next` via `product.Version` (`--version-regex '0\.0\.1-|37'`) and excludes classic emulator/`adb`/`netsimd` (`--exclude-modules 'Qt6|libandroid-emu|adb|netsimd'`). Returns clusters grouped by `stable_signature` with counts, `latest_report_id`, `build_ids`, host OS, crash location, custom keys (`internal-msg`, `looper_registrations`, `list_annotations`), and `bug_urls`:
  ```bash
  emu-dev-cli crash list-crashes
  emu-dev-cli crash list-crashes --unassigned-only --days 7
  emu-dev-cli crash list-crashes --unassigned-only --days 7 --json
  ```
- **Associate or unlink a Buganizer issue in `go/crash` (`link-bug`):** Calls `QueryAPIService.UpdateAnnotation` on `blade:crash-queryapi` to attach (or `--unlink`) a Buganizer issue to a `stable_signature` or 16-hex `ReportID`:
  ```bash
  emu-dev-cli crash link-bug "virtio_gpu_rutabaga_load-e709a6ba" b/567125517
  emu-dev-cli crash link-bug 9dd7b05170400000 567125517
  emu-dev-cli crash link-bug "virtio_gpu_rutabaga_load-e709a6ba" b/567125517 --unlink
  ```
- **Find existing / duplicate Buganizer issues (`find-bug`):** Checks `go/crash` annotations, symbolicates the minidump, queries Buganizer Component 29601 across open/closed issues, and reports `[ALREADY FIXED / RESOLVED ✅]` vs `[OPEN]`:
  ```bash
  emu-dev-cli crash find-bug 05d8356e2f800000
  emu-dev-cli crash find-bug https://crash.corp.google.com/05d8356e2f800000
  ```
- **File or update Buganizer issue (`file-bug`):** Files/updates a Buganizer issue in Component 29601 directly from an agent-authored RCA Markdown file (`--from-rca <file.md>`, bypassing nested AI subprocesses) or via standalone CrashAdvisor RCA, and links it back in `go/crash`:
  ```bash
  emu-dev-cli crash file-bug 05d8356e2f800000 --signature "virtio_gpu_rutabaga_load-e709a6ba" --from-rca /tmp/rca_05d8356e2f800000.md
  emu-dev-cli crash file-bug 05d8356e2f800000
  ```
- **Attempt autonomous fix (`autofix`):** Parses RCA actionability YAML, checks if already resolved, and dispatches `emu_main_next_engineer`:
  ```bash
  emu-dev-cli crash autofix 05d8356e2f800000
  emu-dev-cli crash autofix 05d8356e2f800000 --dry-run
  emu-dev-cli crash autofix 05d8356e2f800000 --force
  ```
- **Local Crash Reproduction (`reproduce`) & Unified Analysis (`analyze`):** Downloads the matching emulator binary and boots under interactive LLDB (`--lldb`), or runs unified symbolication + RCA:
  ```bash
  emu-dev-cli crash reproduce 05d8356e2f800000 --lldb
  emu-dev-cli crash reproduce 05d8356e2f800000 --dry-run
  emu-dev-cli crash analyze 05d8356e2f800000 --file-bug --autofix
  ```
### 10. Automated Flakiness Suite (`flakiness`)
The `flakiness` command suite is a closed-loop system for AI agents and developers to monitor,
investigate, reproduce, triage, and fix flaky emulator tests across all CI pipelines and target
platforms.

#### Target Platform Matrix (`--target`)
| Target Name | Description | Local Bazel Flags |
| :--- | :--- | :--- |
| `emulator_linux_x64` (default) | Standard Linux x86_64 host build | `bazel test <test>` |
| `emulator_linux_x64_asan` | AddressSanitizer memory safety build | `bazel test <test> --config=asan` |
| `emulator_linux_x64_tsan` | ThreadSanitizer data-race build | `bazel test <test> --config=tsan` |
| `emulator_windows_x64` | Windows x86_64 host build | `bazel test <test> --config=rbe-win-x64` (on Linux via RBE) or `--config=windows` (on Windows) |
| `emulator_mac_aarch64` | Apple Silicon ARM64 host build | `bazel test <test> --config=macos_arm64` |
| `all` | Aggregates runs across all 5 platforms | _(Queries all CI targets)_ |

#### Test Identifiers (`--config` / `--test-id` / `--suite`)

| Test Identifier | Shorthand Alias | Bazel Targets / Scope | Total Tests |
| :--- | :--- | :--- | :--- |
| `android_emulator/ets` | `ets` | `@goldfish_test//testlib/...`, `@goldfish_test//ets:{presubmit,postsubmit}`, `@goldfish_test//xts:postsubmit` | 21 |
| `android_emulator/release` | `release` | `@goldfish//emulator:release_build_test`, `@goldfish_build//:sanity_checks`, `@goldfish//emulator/launcher:boot_tests`, `@qemu//google/toolchain:generated_diff` | 4 |
| `android_emulator/unit_tests` | `unit_tests` | `@goldfish//...`, `@aemu//...`, `@gfxstream//...` host unit tests | 7,114 |
| `all` (default) | `all` | Queries across all 3 test identifiers (auto-inferred from `--test` for `history` and `sponge`) | All |

#### Query Modes (`--mode`)
- `all` (default for `history` and `sponge`): Examines both pre-submit Gerrit tryjobs and post-submit merged CI runs.
- `presubmit`: Restricts queries to pre-submit tryjob builds (`P...`).
- `postsubmit` (default for `list` and `triage`): Restricts queries to merged post-submit CI builds.

#### Authentication Protocol & macOS Workstation Handling (`--token`)
Android Test Hub and Android Build internal endpoints require an OAuth2 token with
`https://www.googleapis.com/auth/androidbuild.internal`.

On **macOS workstations**, standard `oauth2l fetch` grants public cloud-platform credentials which
return `HTTP 403 Forbidden` (`ACCESS_TOKEN_SCOPE_INSUFFICIENT`).

**Agent Execution Protocol when running on macOS (`sys.platform == 'darwin'`):**

1. **Environment Check**: Check if `ANDROID_BUILD_TOKEN` environment variable or `--token <token>`
   argument is set.
2. **Automated Remote SSH Fetch**: If an SSH connection to a GLinux machine (`$GLINUX_HOST` or
   `$USER.c.googlers.com`) is available, `OAuthTokenManager` automatically fetches the token via
   SSH.
3. **Interactive Escalation**: If an auth failure (`HTTP 403`) occurs, the agent MUST interactively
   ask the user to run:
   ```bash
   oauth2l fetch --sso $USER@google.com androidbuild.internal
   ```
   and either set `export ANDROID_BUILD_TOKEN="<token>"` or supply `--token "<token>"`.

#### 10.1 List Flaky Tests (`flakiness list`)
Scans Android Test Hub (ATH) / AnTS invocations over a time window and aggregates test failure rates
into an aligned Markdown table (or JSON with `--json`).
```bash
# List flaky tests on TSAN with failure rate >= 10% across the last 7 days
emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

# List all flaky tests across all 5 target platforms combined
emu-dev-cli flakiness list --target all --mode all --min-flake-rate 5.0

# Machine-readable output for programmatic agent parsing
emu-dev-cli --json flakiness list --target emulator_linux_x64_tsan
```
#### 10.2 Inspect Individual Test History (`flakiness history`)
Retrieves chronological execution records for a specific test target, showing pass/fail status,
timestamps, build IDs, invocation IDs, target platforms, and direct clickable Fusion2 links.
```bash
# Query execution history for hal_plug_adapter_unittests on TSAN
emu-dev-cli flakiness history --test @@goldfish+//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --days 7 --limit 25

# Query history across all platforms to compare Linux vs Windows vs Mac
emu-dev-cli flakiness history --test @@goldfish+//emulator/libs/async:loop_handoff_test --target all --days 3
```
#### 10.3 Inspect Sponge & ResultStore Logs (`flakiness sponge`)
Queries ResultStore via Stubby RPC to inspect build/test actions, failure exit codes, error
messages, process execution durations, and diagnostic file URIs (`test.log`, `test.xml`) for any
Sponge / Fusion2 invocation UUID or URL:
```bash
# 1. Inspect a specific invocation by UUID or Fusion2 URL
emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26

# 2. Inspect invocation and filter for a specific test target
emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26 --test @@goldfish+//emulator/launcher:can_boot_with_minigbm

# 3. Automatically inspect the latest 3 failed invocations for a flaky test on macOS
emu-dev-cli flakiness sponge --test @@goldfish+//emulator/launcher:can_boot_with_minigbm --target emulator_mac_aarch64 --latest-failures 3

# 4. Output machine-readable JSON for automated agent triage
emu-dev-cli --json flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26
```
#### 10.4 Fetch Diagnostic Artifacts (`flakiness fetch-logs`)
Downloads logcat files, host stdout/stderr logs, thread dumps, Perfetto traces, and ResultStore /
Sponge summaries for any invocation ID (`I...` or UUID).
```bash
# Fetch host log for an invocation
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type HOST_LOG

# Fetch all logs, ResultStore action summaries, and traces to a custom directory
emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type ALL --out-dir /tmp/test_logs/
```
#### 10.5 Reproduce Flakes Locally (`flakiness reproduce`)
Executes the target's Bazel test runner with targeted sanitizers/configs and `--runs_per_test=N` to
stress-test and locally reproduce non-deterministic failures.

##### Windows Test Flakiness on Linux (`--config=rbe-win-x64`)
Windows unit tests can be compiled, stress-tested, and reproduced directly on Linux host
workstations via Remote Build Execution (RBE) using `--config=rbe-win-x64`. `emu-dev-cli`
automatically maps `emulator_windows_x64` to `--config=rbe-win-x64` when executing on Linux hosts.
```bash
# Stress-test TSAN data races (runs test 30 times in loop)
emu-dev-cli flakiness reproduce --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --iterations 30

# Stress-test memory safety under ASAN
emu-dev-cli flakiness reproduce --test @goldfish//emulator/videobridge:in_process_media_provider_test --target emulator_linux_x64_asan --iterations 50

# Stress-test Windows host test target 30x directly on Linux workstation via RBE
emu-dev-cli flakiness reproduce --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_windows_x64 --iterations 30
```
#### 10.6 Automated Bug Triage & AI Patch Proposal (`flakiness triage`)
Cross-references detected flaky tests against existing open Buganizer issues in component `1016880`.
For newly discovered flakes, generates an AI root-cause hypothesis and proposed unified diff patch.
```bash
# Preview triage results and AI patch proposals without creating bugs
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --dry-run

# Automatically file Buganizer issues with AI patches for unfiled flakes
emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --auto-file
```
#### 10.7 One-Command Fix & Verify (`flakiness fix`)
End-to-end remediation workflow: fetches the AI patch and stack trace from Buganizer, applies the
code change to the local workspace, verifies with a 50x stress-test reproduction run, and optionally
uploads a Gerrit CL.
```bash
# Dry run verification of bug fix
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --dry-run

# Apply fix, stress-test 50x, and upload Gerrit CL
emu-dev-cli flakiness fix --bug 123456789 --target emulator_linux_x64_tsan --iterations 50 --upload
```
### 11. `mise` Workspace Tasks, Clang-Tidy (`tidy`) & Refactoring
#### A. `mise` Workspace Task Runner (`<workspace_root>/mise.toml` $\rightarrow$ `hardware/generic/goldfish/development/mise.toml`)
- **Per-Repository Git/Gerrit Tasks (`dir = "{{ cwd }}"` — MUST set `Cwd` to the specific git repo, e.g. `hardware/generic/goldfish` or `hardware/google/aemu`):**
  - `mise presubmit`: Sets `git config push.negotiate false` and uploads the current branch to Gerrit with `Presubmit-Ready+1` (`repo upload . --cbr -y --label 'Presubmit-Ready+1'`).
  - `mise submit`: Sets `git config push.negotiate false` and uploads the current branch for review (`repo upload . --cbr -y`).
  - `mise gum`: Stages modified tracked files and amends `HEAD` (`git add --update . && git commit --amend`) if `$USER` is the commit owner.
- **Workspace-Root Build, Test & Lint Tasks (run from `<workspace_root>`):**
  - `mise fastbuild`: Fast C++-only verification build (`bazel build --noshow_progress -- @goldfish//...` excluding non-`cc_*` targets).
  - `mise fasttest`: Fast `@goldfish//...` unit test run excluding slow symbol/zip targets (`fasttest_exclusions.txt`).
  - `mise build` / `mise test`: Full release build (`@goldfish//emulator:release`) / full test suite (`@goldfish//...`).
  - `mise tidy` / `mise tidy_fix`: Run `hardware/generic/goldfish/development/tools/clang_tidy.py --commit HEAD` / apply fixes (`bazel run @goldfish//:clang_tidy_report`).
  - `mise bcc`: Generate `compile_commands.json` (`bazel run @goldfish//development/tools:generate_bcc -- --workspace $BAZEL_ROOT`).

#### B. Automated Refactoring & Clang-Tidy Diagnostics (`tidy`)
The `tidy` command suite provides first-class inspection, leveling up, and automated remediation for
`clang-tidy` diagnostics (e.g. `readability-identifier-naming`) using a Git-native, compiler-guided
Engine.

- **Check Diagnostics (`tidy check`):** Scans targets and diffs, rendering structured JSON or tables
  for current diagnostics, categorized by complexity levels (`safe`, `local`, `types`, `public`).
  ```bash
  emu-dev-cli tidy check @goldfish//emulator/libs/sockets:tidy --level safe
  ```
- **Automated Fix Engine (`tidy fix`):** Progressively remediates code from safe intra-file tweaks
  (Level 1 / `safe`) up to full cross-module public API refactoring (Level 4 / `public`) using
  compiler orchestration, agent AI, fastbuild verification, and rollback safety.
  ```bash
  emu-dev-cli tidy fix @goldfish//emulator/libs/sockets:tidy --level local --model auto
  # Continue in-flight transaction after manual adjustments:
  emu-dev-cli tidy fix --continue
  # Inspect active transaction status:
  emu-dev-cli tidy fix --status
  # Abort transaction and restore baseline:
  emu-dev-cli tidy fix --abort
  ```
- **Add Tidy Tests (`tidy add`):** Injects or configures `clang_tidy_test` rules in target package
  directories or `BUILD.bazel` files.
  ```bash
  emu-dev-cli tidy add emulator/libs/sockets --target sockets
  ```
### 12. Build Time & Bazel Profile Analysis (`build-times` / `profile`)
Analyzes Android Build (`go/ab`) CI build times and Bazel Chrome Trace stage profiles under
`logs/bazel/*.profile.gz` (plus `BUILD_INFO`) across all 8 `emu-main-next` targets
(`emulator_linux_x64`, `emulator_linux_x64_asan`, `emulator_linux_x64_debug`,
`emulator_linux_x64_tsan`, `emulator_mac_aarch64`, `emulator_mac_aarch64_debug`,
`emulator_windows_x64`, `emulator_windows_x64_debug`).

Discovers and reports on every Bazel stage profile (`clang_tidy`, `aosp_build`, `command` / `release`,
`unit_tests`, `ets`, and custom `logs/bazel/*.profile.gz` stage artifacts).

Decomposes every CI build into **4 layers + Per-Stage Breakdown**:
1. **Queue Wait Time** (`sync_start_time - creationTimeMillis`): Worker queue wait.
2. **Repo Sync Time** (`sync_finish_time - sync_start_time`): Git checkout duration.
3. **`build.py` Wrapper Time** (`target_finish_time - target_start_time`): Outer build script duration.
4. **Per-Stage & Aggregate Bazel Profile Wall Time & Critical Path** (`logs/bazel/*.profile.gz`):
   Phase breakdown (`Launch`, `Init`, `Evaluate`, `Load & Analyze`, `Execute`), `critical path component` chain, per-mnemonic stats (`CppCompile`, `CcLtoBackendCompile`, `CppLTOIndexing`, `TestRunner`, `SymlinkTree`, `PackageZip`, `AmcGeneratorRun`), and RBE cache hit/execution telemetry.

- **Inspect / History / Diagnose (`build-times inspect` / `history` / `diagnose` / `compare`):**
  ```bash
  # Inspect build 16411294 on Linux x64 or compare across all 8 targets side-by-side
  emu-dev-cli build-times inspect --build-id 16411294 --target emulator_linux_x64
  emu-dev-cli build-times inspect --build-id 16411294 --target all
  # Inspect multi-build history & trends for the last 10 mac arm builds
  emu-dev-cli build-times history --target emulator_mac_aarch64 --limit 10
  # Auto-detect regressed builds vs baseline and attribute root causes (Queue vs Critical Path vs Mnemonics vs Git commits)
  emu-dev-cli build-times diagnose --target emulator_mac_aarch64 --limit 10
  emu-dev-cli build-times diagnose --target emulator_mac_aarch64 --baseline 16411294 --regressed 16405496
  ```
### 12.5 Emulator Binary Performance Benchmarking & Profiling (`perf`)
Profiles, benchmarks, and compares **fully built emulator binaries** (`qemu-next`, `qemu-now`, `stable`, `go/ab` build IDs, or running instances) across standardized workloads (`adb`, `adb-tmpfs`, `adb-disk`, `adb-dir`, `input`, `disk`, `memory`, `cpu`, `idle`, `boot`, `snapshot`, `display`) paired with cross-platform host/guest/hypervisor telemetry profilers (`proc`, `perf-stat`, `perf-record`, `perfetto`, `emu-perf-stat`). Automatically installs `perf_probe.apk` (`com.android.emulator.perfprobe`) on clean isolated AVDs.
```bash
emu-dev-cli perf list
emu-dev-cli perf run --suite adb-tmpfs --iterations 5 -o /tmp/candidate_perf.json
emu-dev-cli perf run --suite input,disk,memory,cpu --iterations 3
emu-dev-cli perf run --target 15075377 --avd Generic_Phone_API_36 --suite idle,adb-tmpfs,adb-disk,input,disk,memory,cpu
emu-dev-cli perf compare stable tree --suite all --iterations 5
emu-dev-cli perf compare latest:emu-main-dev local --suite adb-tmpfs --format commit
emu-dev-cli perf compare /tmp/baseline.json /tmp/candidate.json
emu-dev-cli workflow compare-perf init [--baseline stable --candidate tree]
emu-dev-cli perf history --limit 20
```

### 12.6 Software Bill of Materials (`sbom`)
Generates, inspects, audits, and diffs SPDX 2.3 JSON Software Bills of Materials (`go/spdx-style-guide`) for Google internal releases and AOSP snapshot builds (`--aosp`):
```bash
emu-dev-cli sbom generate --build-id 12345678 [--aosp --out /tmp/aosp-sbom.spdx.json]
emu-dev-cli sbom inspect [--filter qemu]
emu-dev-cli sbom audit
emu-dev-cli sbom diff /tmp/old.spdx.json /tmp/new.spdx.json
```

### 12.7 Screencast Upload (`screencast`)
Uploads `.mp4`, `.webm`, or `.mov` videos to `screencast.googleplex.com` (gLinux/gMac/gWindows via `gosso`):
```bash
emu-dev-cli screencast /tmp/demo.mp4 [--title "Title" --description "Notes" --dry-run]
```

### 13. Common Natural Language Requests & Agent Action Mapping
| User Request | Target Command |
| :--- | :--- |
| _"Run the weekly emulator performance qualification / compare stable vs latest emu-main-next"_ | `emu-dev-cli workflow compare-perf init` |
| _"Compare performance of stable release vs current emulator in the tree"_ | `emu-dev-cli workflow compare-perf init --baseline stable --candidate tree` |
| _"Compare performance of emulator build 123 vs 321"_ | `emu-dev-cli perf compare 123 321 --suite idle,adb-tmpfs,adb-disk` |
| _"Benchmark ADB push/pull throughput on the running emulator"_ | `emu-dev-cli perf run --suite adb-tmpfs,adb-disk --iterations 5` |
| _"Fetch the latest emulator and system image, create a medium_phone AVD, and launch it in the background"_ | `emu-dev-cli fetch-build emulator --latest && emu-dev-cli fetch-build system-image --latest --branch 26Q2-emu-release && emu-dev-cli create avd --name my-dev-phone --sysimg-dir /tmp/system-image-x86_64-26Q2-emu-release-latest/extracted/ --profile medium_phone --force && emu-dev-cli launch emulator --emulator-dir /tmp/emulator-linux-x64-latest/extracted/emulator --detached -- -avd my-dev-phone -no-window` |
| _"Launch a 2-device emulator mesh for Bluetooth/Wi-Fi Netsim testing and wait until both are booted"_ | `emu-dev-cli create mesh --prefix bt-mesh --count 2 --profile medium_phone && emu-dev-cli launch mesh --prefix bt-mesh --count 2 --packet-streamer default --no-window && emu-dev-cli mesh wait-ready --prefix bt-mesh --count 2 --timeout 180` |
| _"Run the automated CTS-Verifier vibrations or tts module on the emulator"_ | `emu-dev-cli cts run-cts-verifier --list-modules` / `emu-dev-cli cts run-cts-verifier --module tts` |
| _"Upload the current branch to Gerrit and mark it Presubmit-Ready+1"_ | `mise presubmit` _(run with `Cwd` set to the specific git repository, e.g. `hardware/generic/goldfish` or `hardware/google/aemu`)_ |
| _"How fast is build 16411294?"_ | `emu-dev-cli build-times inspect --build-id 16411294 --target all` |
| _"What happened to the last 10 mac arm builds?"_ | `emu-dev-cli build-times history --target emulator_mac_aarch64 --limit 10` |
| _"Can you find out why the mac arm builds all of a sudden became slow?"_ | `emu-dev-cli build-times diagnose --target emulator_mac_aarch64 --limit 10` |
| _"Please do an analysis of all the flaky tests of today on the linux tsan target and file bugs for failing tests for which no bug exists"_ | `emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --days 1 --auto-file` |
| _"Show me what bugs would be filed for today's TSAN flaky tests without modifying Buganizer"_ | `emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --days 1 --dry-run` |
| _"List all flaky tests across all platforms with flake rate > 5% over the past 7 days"_ | `emu-dev-cli flakiness list --target all --days 7 --min-flake-rate 5.0` |
| _"Show me the run history and failure status for loop_handoff_test on TSAN"_ | `emu-dev-cli flakiness history --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_linux_x64_tsan --days 7` |
| _"Download the failure logs for invocation I99100010599127182"_ | `emu-dev-cli flakiness fetch-logs --invocation-id I99100010599127182 --artifact-type ALL` |
| _"Reproduce hal_plug_adapter_unittests locally under TSAN"_ | `emu-dev-cli flakiness reproduce --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --iterations 30` |
| _"Reproduce and stress-test Windows flaky test on Linux"_ | `emu-dev-cli flakiness reproduce --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_windows_x64 --iterations 30` _(uses RBE `--config=rbe-win-x64` automatically on Linux)_ |
| _"Fix flaky test bug b/123456789 and upload the CL"_ | `emu-dev-cli flakiness fix --bug 123456789 --iterations 50 --upload` |
| _"Run the daily untriaged production crash triage workflow"_ | `emu-dev-cli workflow triage-crashes init -- --days 2` |
| _"Review a commit for duplication / modularity / control-flow / constants / unicode"_ | `emu-dev-cli workflow review-{duplication,modularity,control-flow,constants,unicode} init -- --commit HEAD` |
| _"List untriaged production qemu-next crashes from the last 7 days"_ | `emu-dev-cli crash list-crashes --unassigned-only --days 7` |
| _"Associate Buganizer issue b/567125517 with crash signature virtio_gpu_rutabaga_load-e709a6ba in go/crash"_ | `emu-dev-cli crash link-bug "virtio_gpu_rutabaga_load-e709a6ba" b/567125517` |
| _"Upload a screen recording to Screencast"_ | `emu-dev-cli screencast /tmp/demo.mp4 --title "Title"` |

---
## 🤖 Agent Autonomous Flakiness Remediation Protocol
1. **Audit & Identify:** `emu-dev-cli flakiness list --target <target>` or `emu-dev-cli flakiness history --test <target>`.
2. **Reproduce:** `emu-dev-cli flakiness reproduce --test <target> --target <target> --iterations 30`.
3. **Diagnose:** `emu-dev-cli flakiness fetch-logs --invocation-id <id>` and inspect stack traces.
4. **Implement Fix:** Eliminate race conditions, add mutex guards, or synchronize teardown lifetimes.
5. **Verify:** Re-run `emu-dev-cli flakiness reproduce --test <target> --target <target> --iterations 50` until **0% flake rate (PASSED)**.
6. **Submit:** Author the commit using `emu-dev-cli workflow create-commit` (`<type>[optional scope]: <Description>` <= 50 chars, body <= 72 chars, no `TAG=`/`CONV=` tags, `Bug:` and `Test:` footers).

---
## 🤖 Agent Autonomous Crash Triage Protocol (`qemu-next`)
When triaging production `qemu-next` crashes from `go/crash` (see `emu-dev-cli docs playbook-crash-triage`), use `emu-dev-cli workflow triage-crashes`:
1. **Initialize Daily Triage Batch & Dossier:** `emu-dev-cli workflow triage-crashes init -- --days 2` (queries `crash.prod.latest` + `crash_annotations.prod.latest` `--unassigned-only`, cross-checks `QueryAPIService.SearchAnnotations` on `blade:crash-queryapi`, auto-links existing Component `29601` matches, and writes `dossier-<state_id>.md`).
2. **Triage Each Cluster:** Link duplicates with `emu-dev-cli crash link-bug "<stable_signature>" b/<BUG_ID>`, or write `/tmp/rca_<report_id>.md` and file + auto-link directly via `emu-dev-cli crash file-bug <latest_report_id> --signature "<stable_signature>" --from-rca /tmp/rca_<latest_report_id>.md`.
3. **Verify 100% Linkage & Complete:** `emu-dev-cli workflow triage-crashes next --id <state_id>` (blocks if any signature is missing a `BugUrl`).
4. **Remediate (If Actionable):** `emu-dev-cli crash autofix <latest_report_id>`.

---
## 🤖 Agent Weekly Performance Qualification Protocol (`compare-perf`)
1. **Initialize Session:** `emu-dev-cli workflow compare-perf init --baseline stable --candidate latest:emu-main-next` (validates all 22 workloads across 9 suites: `boot`, `idle`, `adb`, `input`, `disk`, `memory`, `cpu`, `display`, `snapshot`).
2. **Execute A/B Benchmark & Verify Scorecard (Step 0):** `emu-dev-cli perf compare stable latest:emu-main-next --suite all --iterations 5 --output <comparison-json>` followed by `emu-dev-cli workflow compare-perf --state=<state_id>`.
3. **Author Weekly Report (Step 1):** Write the unredacted 4-part scorecard and root-cause analysis to `<report-file>` and advance `emu-dev-cli workflow compare-perf --state=<state_id>`.
