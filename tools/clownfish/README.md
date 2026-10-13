# Clownfish: Deterministic Fake Android Emulator gRPC Server

**Clownfish** is a stateful, deterministic Python fake implementation of the
Android Emulator gRPC control plane. Just as _Goldfish_ and _Cuttlefish_ name
virtual device families, _Clownfish_ acts as a fast, lightweight stand-in
emulator designed for validating UI clients without launching a full QEMU/KVM
guest or Android system image.

## Supported Services

Clownfish hosts both the legacy v1 (`android.emulation.control`) and the
next-gen v2 (`android.emulation.v2`) gRPC services backed by a single
thread-safe `EmulatorState` store:

### v1 & Incubating Services (`android.emulation.control`)

- `EmulatorController` (`emulator_controller.proto`)
- `SnapshotService` (`snapshot_service.proto`)
- `ScreenRecording` (`screen_recording_service.proto`)

### v2 Services (`android.emulation.v2`)

- `CapabilitiesService` (`capabilities_service.proto`)
- `DisplayService` (`display_service.proto`)
- `InputService` (`input_service.proto`)
- `SensorsService` (`sensors_service.proto`)
- `LocationService` (`location_service.proto`)
- `BatteryService` (`battery_service.proto`)
- `SystemService` (`system_service.proto`)
- `TelephonyService` (`telephony_service.proto`)
- `CameraService` (`camera_service.proto`)
- `AudioService` (`audio_service.proto`)
- `HypervisorService` + `google.longrunning.Operations`
  (`hypervisor_service.proto`)
- `RtcService` (`rtc_service.proto`)

## Key Features

- **Animated Synthetic Frame Renderer:** Continuously renders an animated scene
  (rotating triangle, bouncing block, touch indicator, posture/battery HUD) and
  pushes frames to both v1 `streamScreenshot` and v2 `StreamScreenshots`.
- **Shared-Memory `file://` MMAP Transport:** Supports `ImageTransport.MMAP`
  with `file://` URIs and row-stride validation
  (`height * ((width * bpp + 3) & ~3)`).
- **Discovery Advertisement:** Writes standard `pid_<PID>.ini` files to the
  emulator discovery directory so `aemu.discovery.EmulatorDiscovery` and Android
  Studio / UI clients automatically detect Clownfish.
- **Device Presets:** Built-in profiles for `phone`, `foldable`, `tablet`, `xr`,
  and `wear`.
- **Fault Injection:** Built-in gRPC server interceptor to inject status codes
  (e.g., `UNAVAILABLE`, `DEADLINE_EXCEEDED`) for resilience testing.

- **Real WebRTC v2 Media & DataChannel Bridge (`videobridge`):** When launched
  with the C++ `videobridge` companion (`--webrtc`, auto-enabled when bundled),
  `android.emulation.v2.RtcService` negotiates live WebRTC `RTCPeerConnection`s
  streaming Clownfish's animated display over `file://` MMAP shared memory,
  synthetic audio, and SCTP DataChannels (`input_v2`, `sensors_v2`, `input`).
- **Integrated `grpc-web-proxy` & `aquarium-ui`:** Automatically launches and
  supervises `@goldfish//emulator/tools/grpc-web:grpc-web-proxy` (`--grpc-web`,
  default port `8888`) and `@goldfish//emulator/tools/aquarium-ui:aquarium-ui`
  (`--aquarium`, default port `8080`) when available in runfiles.

## Running with WebRTC, gRPC-Web Proxy, and Aquarium UI

Run the bundled `@goldfish//emulator/tools/aquarium-ui:clownfish` target (which
builds and attaches `videobridge`, `grpc-web-proxy`, and `aquarium-ui` +
`aquarium:dist`):

```bash
bazel run @goldfish//emulator/tools/aquarium-ui:clownfish -- --preset foldable --port 8554 --interactive
```

This starts:

- **Clownfish gRPC server** (`v1` + `v2` services): `localhost:8554`
- **C++ WebRTC `videobridge`** (proxied via `v2.RtcService` on `:8554` using
  `file://` MMAP)
- **Standalone `grpc-web-proxy`**: `http://127.0.0.1:8888`
- **Aquarium Web UI + embedded `GrpcWebServer`**: `http://localhost:8080`

You can also run the pure-Python gRPC server target directly:

```bash
bazel run @aemu//tools/clownfish:clownfish -- --preset foldable --port 8554 --interactive
```

## Embedded Usage in Python UI Tests

```python
from clownfish.server import ClownfishServer

with ClownfishServer(
    preset="foldable",
    port=0,
    fps=30,
    enable_webrtc=True,
    enable_grpc_web=True,
    enable_aquarium=True,
) as emu:
    # Connect gRPC clients to emu.address ("localhost:<port>")
    # Or open emu.aquarium_url / emu.grpc_web_url in a browser
    ...
```
