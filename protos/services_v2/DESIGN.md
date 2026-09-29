# Architecture & Design: Android Emulator Services v2 (`//protos/services_v2`)

**Location:** `hardware/google/aemu/protos/services_v2`
**Bazel Package:** `@aemu//protos/services_v2`
**Protobuf Package Root:** `android.emulation.v2.*`

---

## 1. Purpose & Core Philosophy

`services_v2` defines the next-generation gRPC and Protocol Buffers API surface
for controlling, observing, and streaming media/telemetry from Android devices.

### 1.1 The Problem: One Client, Four Deployments

The API exists so that a single client — an Electron UI, an HTML5 UI (via
gRPC-Web), or an automated AI agent (via MCP / `grpcurl`) — can drive an Android
device without knowing how that device is realized or where it lives. There are
four target deployments:

|              | **Local**                       | **Remote**                        |
| :----------- | :------------------------------ | :-------------------------------- |
| **Physical** | Device on USB, reached over ADB | Device in a lab, reached over ADB |
| **Emulated** | Emulator on the same host       | Emulator in a datacenter          |

These are **two orthogonal axes**, and keeping them separate is the central
insight the schema is built on:

- **Physical vs. emulated** determines _what is possible_. A physical device has
  no hypervisor, so there is nothing for `HypervisorService` to talk to. This is a
  **capability** question, it is knowable server-side, and it is therefore
  answered in the schema — by `GetDeviceCapabilities` / `StreamDeviceCapabilities`.
- **Local vs. remote** determines _how well it performs_. It changes latency and
  bandwidth budgets, never the set of available operations. A server behind
  `adb forward` cannot distinguish loopback from a tunnel, whereas the client
  always knows how it connected. Locality is therefore a **client-side** concern
  and is deliberately absent from the schema.

The governing rule that falls out of this:

> Everything that **observes or drives** the device works everywhere. Only what
> **simulates fake hardware state** is emulator-only.

Three consequences recur throughout this document: capability messages are
authoritative and clients must not branch on device kind (§2.9); `RtcService` is
the default media path because encoded video is the only thing that fits a
remote link (§2.8); and `DeviceMetadata.device_kind` exists for presentation
rather than dispatch (§2.9).

### 1.2 Implementation Status & Master Service Reference (12 Domain Services + `common.proto`)

| Service | `-proto` Path (relative to `services_v2/`) | Full gRPC Service Name | Server Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `CapabilitiesService` | `capabilities_service.proto` | `android.emulation.v2.CapabilitiesService` | **Served** | Registered unconditionally (`GetDeviceCapabilities`, `StreamDeviceCapabilities`). |
| `HypervisorService` | `hypervisor_service.proto` | `android.emulation.v2.HypervisorService` | **Served (Snapshots)** | VM lifecycle (`PauseVm`, `ResumeVm`, `ResetVm`, `HardResetVm`, `StopVm`) + snapshot CRUD & LROs (`CreateSnapshot`, `RestoreSnapshot`, `UpdateSnapshot`, `DeleteSnapshot`, `SetQuickbootTarget`, `ExportSnapshot`, `ImportSnapshot`). |
| `Operations` | `google/longrunning/operations.proto` | `google.longrunning.Operations` | **Served** | Registered alongside `HypervisorService` snapshot LROs. |
| `RtcService` | `webrtc_service.proto` | `android.emulation.v2.RtcService` | **Served** | WebRTC session lifecycle (`RequestRtcStream`, `UpdateRtcStream`, `CloseRtcStream`) and strongly-typed JSEP SDP/ICE signaling. |
| `InputService` | `input_service.proto` | `android.emulation.v2.InputService` | Schema + Core C++ | Core C++ `InputSession` logic implemented in `plugin/grpc/input`. |
| `DisplayService` | `display_service.proto` | `android.emulation.v2.DisplayService` | Schema only (`UNIMPLEMENTED`) | Multi-display lifecycle (`ListDisplays`, `GetDisplay`, `StreamDisplays`, `UpdateDisplay`, `CreateDisplay`, `DeleteDisplay`) and screenshots (`GetScreenshot`, `StreamScreenshots`). |
| `AudioService` | `audio_service.proto` | `android.emulation.v2.AudioService` | Schema only (`UNIMPLEMENTED`) | Raw PCM speaker capture (`StreamSpeakerAudio`), unary clip injection (`InjectAudioClip`), and streaming mic injection (`StreamMicrophoneAudio`). |
| `BatteryService` | `battery_service.proto` | `android.emulation.v2.BatteryService` | Schema only (`UNIMPLEMENTED`) | Charge level, charger source, and thermal health (`GetBatteryState`, `StreamBatteryState`, `UpdateBatteryState`). |
| `CameraService` | `camera_service.proto` | `android.emulation.v2.CameraService` | Schema only (`UNIMPLEMENTED`) | Camera sensor state observation (`GetCameraState`, `StreamCameraState`) and 2D still/video frame injection (`InjectFrame`, `StreamCameraFrames`, `ResetCamera`). |
| `LocationService` | `location_service.proto` | `android.emulation.v2.LocationService` | Schema only (`UNIMPLEMENTED`) | WGS84 GNSS fixes (`GetLocationState`, `StreamLocationState`, `UpdateLocationState`) and server-paced route playback (`PlayRoute`). |
| `SensorsService` | `sensors_service.proto` | `android.emulation.v2.SensorsService` | Schema only (`UNIMPLEMENTED`) | 3D rigid-body pose (`DevicePose`), ambient environment (`AmbientEnvironment`), live synthesized IMU telemetry (`ImuTelemetry`), raw HAL overrides (`RawSensorOverrides`), and multi-hinge/rollable posture (`Get/Update/StreamPostureState`). |
| `TelephonyService` | `telephony_service.proto` | `android.emulation.v2.TelephonyService` | Schema only (`UNIMPLEMENTED`) | Cellular RAT, signal bars, active calls, SMS, and voice call transitions (`Get/Stream/UpdateTelephonyState`, `SendSms`, `TransitionCall`). |
| `SystemService` | `system_service.proto` | `android.emulation.v2.SystemService` | Schema only (`UNIMPLEMENTED`) | Boot lifecycle, ADB connection, keyguard, reboot (`RebootDevice`), clipboard (`Get/Update/StreamClipboardState`), and `InjectFingerprint`. |

### 1.3 Physical-Device-over-ADB Discovery & Transport Architecture

On physical Android devices (USB locally or remote Firebase Test Lab devices),
the `services_v2` daemon runs on the device and binds to an **abstract Unix
domain socket**:

```text
localabstract:aemu_v2
```

Host clients (Electron UI, MCP server, or gRPC-Web bridge) forward a dynamic
host TCP port to the device's abstract socket via standard ADB forwarding:

```bash
adb -s <serial> forward tcp:0 localabstract:aemu_v2
# Prints the allocated host port, e.g. 54321 -> connect gRPC channel to 127.0.0.1:54321
```

Because communication rides over the ADB transport:
- `SystemState.connection_state` reports `CONNECTED` whenever the on-device
  service is reachable; if ADB disconnects, the RPC fails at the gRPC transport
  layer with `UNAVAILABLE`.
- Subsystems backed by real physical sensors without a writeable HAL (e.g.,
  `SensorsService` physical IMU sensors) advertise `ACCESS_MODE_READ_ONLY` +
  `FIDELITY_HARDWARE`.
- Subsystems overridden via Android framework commands (`dumpsys battery set`,
  `AppOpsManager` mock location) advertise `ACCESS_MODE_READ_WRITE` +
  `FIDELITY_SOFTWARE_MOCK`.

---

## 2. Key Architectural Decisions & Rationale

### 2.1 Authoritative Partial Updates via Flat `proto3 optional` Request Fields

All state-mutating RPCs (`UpdateBatteryState`, `UpdateDisplay`,
`UpdateLocationState`, `UpdatePostureState`, `UpdateSensorsState`,
`UpdateTelephonyState`, `UpdateClipboardState`) take a flat `Update*Request`
whose mutable fields are marked `optional`.

- **Native Wire Presence (`has_*()`):** Setting an `optional` field to its zero
  value (e.g., `level_percent = 0`, `satellite_count = 0`, `radio_powered = false`)
  serializes the field tag on the wire so the server updates the property.
  Omitting a field leaves `has_*() == false` so existing device state remains
  untouched.
- **Dedicated Response Types:** Every RPC returns either the updated state
  resource or a dedicated `*Response` message (never `google.protobuf.Empty`),
  allowing future response metadata to be added without breaking wire or source
  compatibility.

### 2.2 Enumeration Design (AIP-126)

1. **Nested vs. Package-Level Prefixing:**
   - **Package-level enums** (`ImageFormat`, `SampleFormat`, `Rotation`,
     `TrackType`, `AccessMode`, `Fidelity`, `DeviceKind`, `ChargerSource`,
     `SensorType`, `PostureType`, `FoldAxis`, `PointerAction`, etc.) prefix every
     value with the enum name (`IMAGE_FORMAT_PNG`, `ROTATION_DEG0`).
   - **Nested enums** (`BatteryState.Health`, `TelephonyState.Technology`,
     `TransitionCallRequest.Action`, `SessionDescription.Type`) prefix **only**
     the zero value (`HEALTH_UNSPECIFIED = 0`, `ACTION_UNSPECIFIED = 0`) and
     leave non-zero values unprefixed (`GOOD`, `RING`, `DISCONNECT`) per AIP-126.
2. **Enum Placement Rule:**
   - Only enums referenced by two or more services live in `common.proto`
     (`ImageFormat`, `SampleFormat`, `Rotation`, `TrackType`, `AccessMode`,
     `Fidelity`, `DeviceKind`).
   - Domain-specific enums (`ChargerSource` in `battery_service.proto`;
     `SensorType`, `PostureType`, `FoldAxis` in `sensors_service.proto`) live in
     their owning service `.proto`.

### 2.3 Normalized 2D Geometry (`NormalizedRect` & `Pointer.size`)

- All 2D display coordinates (`x`, `y`) use normalized `[0.0, 1.0]` floating-point
  coordinates relative to logical display dimensions (`x = pixel_x / width`,
  `y = pixel_y / height`).
- Coordinates outside `[0.0, 1.0]` (e.g., `-0.05` or `1.05`) are explicitly
  permitted and never clamped to support bezel-originating system gestures.
- `Pointer.size` is normalized in `[0.0, 1.0]` relative to the **shorter display
  edge** (`min(width, height)`).

### 2.4 Display Orientation Divergence Model (`rotation` vs. `user_rotation`)

- `Display.user_rotation` (mutable): Physical orientation of the device chassis
  set by the user in the host UI (`ROTATION_DEG0`, `DEG90`, `DEG180`, `DEG270`).
- `Display.rotation` (`OUTPUT_ONLY`): Logical orientation reported by Android
  `WindowManager` (`DisplayInfo.rotation`) reflecting the rendered video stream.

```
degrees(r) = (r - 1) * 90        // ROTATION_UNSPECIFIED (0) is treated as 0

outer device bezel angle  = degrees(user_rotation)
inner video canvas offset = (degrees(user_rotation) - degrees(rotation) + 360) % 360
```

### 2.5 Multi-Modal Clock Synchronization (`CLOCK_MONOTONIC`)

All high-frequency telemetry and media payloads carry a monotonic nanosecond
timestamp (`timestamp_nanos` / `presentation_time_nanos` on `CLOCK_MONOTONIC`).
`SendInputEventsResponse.last_event_dispatched_at_nanos` shares this clock with
`Screenshot.presentation_time_nanos`, providing an exact completion barrier for
observe-act automation loops.

### 2.6 Capability Model: 12 Top-Level Capabilities Matching 12 Services

`DeviceCapabilities` mirrors the 12 domain services with nested sub-capabilities:
- `DisplayCapabilities` embeds `ScreenCaptureCapabilities screen_capture = 6`.
- `SystemCapabilities` embeds `BiometricsCapabilities biometrics = 4` and
  `ClipboardCapabilities clipboard = 5`.
- `SensorsCapabilities` embeds `PostureCapabilities posture = 4` (including
  multi-hinge `HingeHardware` and rollable `RollableHardware` descriptors).

Each nested capability message preserves its own independent `access_mode` and
`fidelity` fields so mixed-permission devices (e.g., readable display with
restricted screen capture, or readable system state with read-only clipboard)
are represented without ambiguity.

### 2.7 Two-Layer Sensor & Physics Simulation Architecture (`SensorsService`)

`SensorsService` models the emulator's rigid-body physics engine (`PhysicalModel`)
explicitly rather than exposing a flat, disconnected bag of raw sensor floats:

1. **Layer 1: Physical World Simulation (`DevicePose` + `AmbientEnvironment` $\rightarrow$ `ImuTelemetry`)**
   - `SensorsState.pose` (`rotation_degrees`, `position_meters`,
     `velocity_meters_per_second`, `ambient_motion`) drives the emulator's
     3D `InertialModel`.
   - `SensorsState.environment` (`light_lux`, `rgbc_light`, `proximity_centimeters`,
     `ambient_temperature_celsius`, `pressure_hpa`, `relative_humidity_percent`,
     `magnetic_field_ut`, `heart_rate_bpm`, `wrist_tilt`) drives the
     `AmbientEnvironment` and `BodyModel`.
   - When `pose` or `environment` changes, the server **automatically synthesizes**
     frame-transformed, physically consistent `ImuTelemetry` readings for the guest OS:
     $$\vec{a}_{\text{accel}} = q^{-1} \cdot (\vec{a}_{\text{linear}} - \vec{g}_{\text{world}}), \quad \vec{\omega}_{\text{gyro}} = q^{-1} \cdot \vec{\omega}_{\text{angular}}, \quad \vec{B}_{\text{mag}} = q^{-1} \cdot \vec{B}_{\text{world}}$$
   - Rotating the phone via `pose.rotation_degrees` naturally updates the gravity
     vector on `accelerometer` (triggering Android `WindowOrientationListener` screen
     auto-rotation) and generates realistic transient angular velocity on `gyroscope`
     when `TransitionMode.TRANSITION_MODE_SMOOTH` is used.
2. **Layer 2: Raw HAL Channel Overrides (`RawSensorOverrides`)**
   - For low-level fault injection (e.g., testing $0g$ freefall detection with
     `accelerometer = {0, 0, 0}` or sensor fusion divergence), callers populate
     `UpdateSensorsStateRequest.raw_overrides` to pin individual HAL channels.
   - `ImuTelemetry.active_overrides` reports any channels currently pinned by
     `RawSensorOverrides`. Setting `clear_raw_overrides = true` (or mutating a
     physical target parameter) restores physics synthesis.
3. **Multi-Hinge & Rollable Mechanics (`PostureState`)**
   - `PostureState` supports multi-hinge foldables (`repeated HingeState hinges`
     indexed by `hinge_id = 0, 1, 2`) and rollable/slidable displays
     (`repeated RollableState rollables` indexed by `rollable_id = 0, 1, 2`),
     with automatic bidirectional coupling between discrete `PostureType` and
     hinge angles.

---

## 3. Practical `grpcurl` Cookbook

### 1. Lossless Screenshot Inspection (`DisplayService/GetScreenshot`)
```bash
grpcurl -plaintext -emit-defaults \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto display_service.proto \
  localhost:8554 \
  android.emulation.v2.DisplayService/GetScreenshot \
  | tee >(jq -r '.imageData' | base64 -d > /tmp/screen.png) \
  | jq 'del(.imageData)'
```

### 2. Input Gestures (`InputService/SendInputEvents`)
```bash
grpcurl -plaintext -emit-defaults \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto input_service.proto \
  -d '{
    "events": [
      {"timestamp_nanos": 0,        "input_source_id": 1, "pointer": {"action": "POINTER_ACTION_DOWN", "pointers": [{"x": 0.5, "y": 0.5}]}},
      {"timestamp_nanos": 50000000, "input_source_id": 1, "pointer": {"action": "POINTER_ACTION_UP",   "pointers": [{"x": 0.5, "y": 0.5}]}}
    ]
  }' \
  localhost:8554 \
  android.emulation.v2.InputService/SendInputEvents
```

### 3. Server-Paced GNSS Route Playback (`LocationService/PlayRoute`)
```bash
grpcurl -plaintext -emit-defaults \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto location_service.proto \
  -d '{
    "waypoints": [
      {"location": {"latitude": 37.4220, "longitude": -122.0841}, "delay_ms": 0},
      {"location": {"latitude": 37.4225, "longitude": -122.0835}, "delay_ms": 1000}
    ],
    "playback_speed_multiplier": 1.0
  }' \
  localhost:8554 \
  android.emulation.v2.LocationService/PlayRoute
```

### 4. Snapshot Long-Running Operations (`HypervisorService/CreateSnapshot`)
```bash
grpcurl -plaintext \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto google/longrunning/operations.proto \
  -proto hypervisor_service.proto \
  -d '{"name": "operations/snapshots/snap-123"}' \
  localhost:8554 \
  google.longrunning.Operations/GetOperation
```

### 5. 3D Device Pose, Ambient Environment & Foldable Posture (`SensorsService`)
```bash
# Smoothly rotate device 90 degrees into landscape and set dark-room environment
grpcurl -plaintext -emit-defaults \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto sensors_service.proto \
  -d '{
    "pose": {
      "rotation_degrees": {"x": 0.0, "y": 0.0, "z": 90.0}
    },
    "environment": {
      "light_lux": 5.0,
      "proximity_centimeters": 0.0
    },
    "transition": "TRANSITION_MODE_SMOOTH"
  }' \
  localhost:8554 \
  android.emulation.v2.SensorsService/UpdateSensorsState

# Set foldable posture or individual multi-hinge angles
grpcurl -plaintext -emit-defaults \
  -import-path hardware/google/aemu/protos/services_v2 \
  -proto sensors_service.proto \
  -d '{
    "hinges": [
      {"hinge_id": 0, "angle_degrees": 90.0}
    ],
    "transition": "TRANSITION_MODE_SMOOTH"
  }' \
  localhost:8554 \
  android.emulation.v2.SensorsService/UpdatePostureState
```
