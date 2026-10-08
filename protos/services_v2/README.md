# Android Device & Emulator Services v2 — Developer Guide & Recipe Book

`@aemu//protos/services_v2` (`package android.emulation.v2`) is the unified gRPC
and Protocol Buffers API for observing and controlling Android devices across
four deployment targets using a single client codebase:

|                    | **Local**                                              | **Remote**                                             |
| :----------------- | :----------------------------------------------------- | :----------------------------------------------------- |
| **Emulated**       | Local Android Emulator (`localhost:8554`)              | Cloud / Web Emulator (gRPC / gRPC-Web)                 |
| **Physical (ADB)** | USB device (`adb forward tcp:0 localabstract:aemu_v2`) | Device farm / Firebase Test Lab (`adb forward` tunnel) |

After reading this guide, you will know how to build:

1. **A full-featured UI client** (HTML5, Electron, React, or native desktop)
   with live WebRTC video/audio, multi-display bezels, touch forwarding, and
   hardware control panels.
2. **A CLI tool, test harness, or MCP agent server** that connects to emulators
   or physical phones over ADB, inspects capabilities, injects gestures,
   captures frame-accurate screenshots, and manages snapshots.

For deep architectural rationale and AIP design rules, see
[`DESIGN.md`](./DESIGN.md).

---

## 1. Core Concepts & Wire Rules (Read First)

Every service in `services_v2` follows four rules. Once you know them, every RPC
behaves predictably:

### Rule 1: Capability-Driven Dispatch (`AccessMode` + `Fidelity`)

Never branch on whether you think you are talking to an emulator or a physical
phone. Call `CapabilitiesService.GetDeviceCapabilities` (or subscribe to
`StreamDeviceCapabilities`) and inspect each feature's capability block:

- **`access_mode`** (`AccessMode`):
  - `ACCESS_MODE_READ_WRITE`: You can read state and mutate/inject state.
  - `ACCESS_MODE_READ_ONLY`: You can observe state (`Get*` / `Stream*`), but
    `Update*` RPCs will return `PERMISSION_DENIED` or `FAILED_PRECONDITION`.
  - `ACCESS_MODE_UNAVAILABLE`: The feature does not exist on this target.
- **`fidelity`** (`Fidelity`):
  - `FIDELITY_HARDWARE`: Backed by real physical silicon (e.g., physical phone
    IMU).
  - `FIDELITY_VIRTUAL_HARDWARE`: Emulated hardware device model (e.g.,
    Goldfish/Cuttlefish).
  - `FIDELITY_SOFTWARE_MOCK`: Injected via Android OS framework APIs
    (`dumpsys battery set`, mock location provider).

### Rule 2: Partial Updates Use Flat `proto3 optional` Fields

All state mutation RPCs (`UpdateBatteryState`, `UpdateDisplay`,
`UpdateLocationState`, `UpdatePostureState`, `UpdateSensorsState`,
`UpdateTelephonyState`, `UpdateClipboardState`) take a flat request message
where every mutable field is marked `optional`:

- **Set a field** (even to `0`, `false`, or `""`): the field is serialized on
  the wire (`has_field() == true`) and updated on the device.
- **Omit a field**: the field is not sent (`has_field() == false`) and the
  device keeps its current value.
- No `FieldMask` or wrapper messages are ever needed.

### Rule 3: Normalized 2D Geometry (`[0.0, 1.0]`)

All touch coordinates (`Pointer.x`, `Pointer.y`) and display regions
(`NormalizedRect`) use normalized floating-point coordinates relative to the
active logical display dimensions:

- `(0.0, 0.0)` is top-left; `(1.0, 1.0)` is bottom-right.
- Values outside `[0.0, 1.0]` (e.g. `x = -0.02` or `y = 1.03`) are **never
  clamped** by the server so clients can perform bezel-originating edge swipes
  (system back gesture, notification shade pull-down).
- `Pointer.size` is normalized relative to `min(display.width, display.height)`.

### Rule 4: Shared Monotonic Clock (`CLOCK_MONOTONIC` Nanoseconds)

All timestamps (`timestamp_nanos`, `presentation_time_nanos`,
`last_event_dispatched_at_nanos`) are nanoseconds on the device's monotonic
clock (`CLOCK_MONOTONIC`). Because input dispatch responses and screenshot
frames share this clock, automation tools can guarantee frame-accurate
post-action screenshots without sleeping (`sleep()`).

---

## 2. Service Directory (12 Services + `common.proto`)

| Schema File                                                  | gRPC Service          | Key RPCs                                                                                                                                        | Emulator | Physical (ADB) |
| :----------------------------------------------------------- | :-------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------- | :------: | :------------: |
| [`common.proto`](./common.proto)                             | —                     | Shared enums & geometry (`NormalizedRect`, `ImageFormat`, `Rotation`, `AccessMode`, `Fidelity`, `DeviceKind`)                                   |   Yes    |      Yes       |
| [`capabilities_service.proto`](./capabilities_service.proto) | `CapabilitiesService` | `GetDeviceCapabilities`, `StreamDeviceCapabilities`                                                                                             |   Yes    |      Yes       |
| [`webrtc_service.proto`](./webrtc_service.proto)             | `RtcService`          | `RequestRtcStream`, `UpdateRtcStream`, `CloseRtcStream`, `SendJsepMessage`, `ReceiveJsepMessageStream`                                          |   Yes    |      Yes       |
| [`display_service.proto`](./display_service.proto)           | `DisplayService`      | `ListDisplays`, `GetDisplay`, `StreamDisplays`, `UpdateDisplay`, `CreateDisplay`, `DeleteDisplay`, `GetScreenshot`, `StreamScreenshots`         |   Yes    |      Yes       |
| [`input_service.proto`](./input_service.proto)               | `InputService`        | `SendInputEvents`, `StreamInputEvents`, `StreamInputActivity`                                                                                   |   Yes    |      Yes       |
| [`system_service.proto`](./system_service.proto)             | `SystemService`       | `Get/StreamSystemState`, `RebootDevice`, `Get/Stream/UpdateClipboardState`, `InjectFingerprint`                                                 |   Yes    |      Yes       |
| [`battery_service.proto`](./battery_service.proto)           | `BatteryService`      | `Get/Stream/UpdateBatteryState`                                                                                                                 |   Yes    |      Yes       |
| [`sensors_service.proto`](./sensors_service.proto)           | `SensorsService`      | `Get/Stream/UpdateSensorsState`, `StreamSensorsUpdates`, `Get/Stream/UpdatePostureState`                                                        |   Yes    |   Read/Mock    |
| [`location_service.proto`](./location_service.proto)         | `LocationService`     | `Get/Stream/UpdateLocationState`, `StreamLocationUpdates`, `PlayRoute`                                                                          |   Yes    |      Mock      |
| [`telephony_service.proto`](./telephony_service.proto)       | `TelephonyService`    | `Get/Stream/UpdateTelephonyState`, `SendSms`, `TransitionCall`                                                                                  |   Yes    |   Read-only    |
| [`camera_service.proto`](./camera_service.proto)             | `CameraService`       | `Get/StreamCameraState`, `InjectFrame`, `StreamCameraFrames`, `ResetCamera`                                                                     |   Yes    |   Read-only    |
| [`audio_service.proto`](./audio_service.proto)               | `AudioService`        | `StreamSpeakerAudio`, `InjectAudioClip`, `StreamMicrophoneAudio`                                                                                |   Yes    |      Yes       |
| [`hypervisor_service.proto`](./hypervisor_service.proto)     | `HypervisorService`   | `Get/StreamVmState`, `Pause/Resume/Reset/HardReset/StopVm`, `List/Get/Create/Restore/Update/Delete/Export/ImportSnapshot`, `SetQuickbootTarget` |   Yes    | `UNAVAILABLE`  |

---

## 3. Part A: Building a UI Client (HTML5 / Electron / Desktop)

This section walks through the five building blocks of an interactive device UI
(such as the `@android/web` SDK or an Electron companion app).

### Recipe U1: Connect & Gate UI Panels with `CapabilitiesService`

On startup, open `StreamDeviceCapabilities`. Whenever capabilities change (for
example, ADB finishes booting or a foldable posture changes), dynamically
enable/disable UI controls:

```ts
// TypeScript (using @android/web generated ts-proto client)
const capsStream = capabilitiesClient.streamDeviceCapabilities({});

for await (const caps of capsStream) {
  // Device header badge (for display only — never branch feature logic on this!)
  setHeaderTitle(`${caps.metadata?.model} (API ${caps.metadata?.apiLevel})`);

  // Enable/disable UI tabs based on authoritative AccessMode
  setBatteryEditable(
    caps.battery?.accessMode === AccessMode.ACCESS_MODE_READ_WRITE,
  );
  setSnapshotsVisible(
    caps.hypervisor?.accessMode !== AccessMode.ACCESS_MODE_UNAVAILABLE,
  );
  setFoldableControlsVisible(
    caps.sensors?.posture?.accessMode === AccessMode.ACCESS_MODE_READ_WRITE,
  );
}
```

### Recipe U2: Multi-Display Viewport & Bezel Rotation Math

An Android device can expose multiple displays (primary screen, foldable outer
cover screen, Android Auto cluster, XR headset displays). Subscribe to
`DisplayService.StreamDisplays` to render each display's bezel and cutout:

```ts
for await (const { displays } of displayClient.streamDisplays({})) {
  for (const d of displays) {
    // d.userRotation: physical chassis orientation chosen by the user in the UI
    // d.rotation: logical OS orientation rendered by Android WindowManager
    const toDeg = (r: Rotation) =>
      r <= Rotation.ROTATION_DEG0 ? 0 : (r - 1) * 90;

    const chassisAngleDeg = toDeg(d.userRotation);
    const videoCounterRotationDeg =
      (toDeg(d.userRotation) - toDeg(d.rotation) + 360) % 360;

    renderDeviceChassis({
      displayId: d.displayId,
      width: d.hardware?.width ?? 1080,
      height: d.hardware?.height ?? 2400,
      cornerRadiusPx: d.hardware?.cornerRadiusPx ?? 0,
      cutouts: d.hardware?.cutouts ?? [], // NormalizedRect [0..1] cutouts/notches
      chassisAngleDeg,
      videoCounterRotationDeg,
    });
  }
}
```

To rotate the device when the user clicks "Rotate Left / Right" in your UI:

```ts
await displayClient.updateDisplay({
  displayId: 0,
  userRotation: Rotation.ROTATION_DEG90, // Only sets userRotation; leaves density/size untouched
});
```

### Recipe U3: Live Video & Audio via WebRTC (`RtcService`)

For interactive UIs, always use `RtcService` rather than polling screenshots.
WebRTC negotiates hardware-accelerated H.264/VP8/VP9 video, Opus audio, and an
ordered SCTP `"input_v2"` `RTCDataChannel` over UDP/ICE:

```ts
// 1. Request a WebRTC session and ICE server configuration
const response = await rtcClient.RequestRtcStream({
  requestedTracks: [
    TrackType.TRACK_TYPE_SCREEN_PRIMARY,
    TrackType.TRACK_TYPE_AUDIO_SPEAKER,
  ],
  secondaryDisplayIds: [],
});
const sessionId = response.handle?.sessionId ?? "";

// 2. Create RTCPeerConnection and open the "input_v2" DataChannel BEFORE offering
const pc = new RTCPeerConnection({ iceServers: response.iceServers });
const inputDataChannel = pc.createDataChannel("input_v2", { ordered: true });
inputDataChannel.binaryType = "arraybuffer";

pc.ontrack = (ev) => attachMediaStreamToVideoElement(ev.streams[0]);
pc.onicecandidate = ({ candidate }) => {
  if (candidate) {
    rtcClient.SendJsepMessage({
      handle: { sessionId },
      candidate: {
        candidate: candidate.candidate,
        sdpMid: candidate.sdpMid ?? "",
        sdpMLineIndex: candidate.sdpMLineIndex ?? 0,
      },
    });
  }
};

// 3. Subscribe to incoming SDP answer & ICE candidates from the device
const jsepStream = rtcClient.ReceiveJsepMessageStream({
  handle: { sessionId },
});

// 4. Send the client SDP Offer
const offer = await pc.createOffer({
  offerToReceiveVideo: true,
  offerToReceiveAudio: true,
});
await pc.setLocalDescription(offer);
await rtcClient.SendJsepMessage({
  handle: { sessionId },
  sdp: { type: SessionDescription_Type.OFFER, sdp: offer.sdp ?? "" },
});

for await (const msg of jsepStream) {
  if (msg.sdp) {
    await pc.setRemoteDescription({ type: "answer", sdp: msg.sdp.sdp });
  } else if (msg.candidate) {
    await pc.addIceCandidate(msg.candidate);
  }
}
```

### Recipe U4: Interactive Touch, Multi-Touch & Keyboard Forwarding (WebRTC `"input_v2"` DataChannel)

When building an interactive UI over WebRTC, **always forward touch, mouse, and
keyboard events over the WebRTC `"input_v2"` `RTCDataChannel`** rather than
over gRPC.

On the server side (`videobridge/core/participant.cc`), the emulator binds an
`EventForwarder` directly to the `"input_v2"` SCTP channel
(`DataChannelLabel::kInputV2`). Sending serialized `android.emulation.v2.InputEvent`
protobuf bytes over `"input_v2"` travels over the existing peer-to-peer UDP/SCTP
transport, avoiding HTTP/2 / gRPC-Web head-of-line blocking and delivering
sub-frame input latency:

```ts
import {
  InputEvent,
  PointerAction,
  ToolType,
} from "@android/emulator-services/input_service";

// Send a binary-encoded v2 InputEvent directly over the open "input_v2" RTCDataChannel
function sendTouchDown(
  inputDataChannel: RTCDataChannel,
  canvas: HTMLCanvasElement,
  e: PointerEvent,
) {
  const bytes: Uint8Array = InputEvent.encode({
    inputSourceId: 0, // 0 = human operator driving the UI
    pointer: {
      displayId: 0,
      action: PointerAction.POINTER_ACTION_DOWN,
      pointers: [
        {
          pointerId: e.pointerId,
          toolType: ToolType.TOOL_TYPE_FINGER,
          x: e.offsetX / canvas.clientWidth, // Normalized [0.0, 1.0]
          y: e.offsetY / canvas.clientHeight, // Normalized [0.0, 1.0]
          pressure: e.pressure || 1.0,
        },
      ],
    },
  }).finish();

  if (inputDataChannel.readyState === "open") {
    inputDataChannel.send(bytes);
  }
}
```

> **Tip:** The `@android/web` SDK provides `InputChannel`
> (`api/web/src/core/input_channel.ts`), which wraps `RTCDataChannel("input_v2")`
> with automatic pre-open buffering, backpressure (`bufferedAmount`), and helper
> methods (`sendPointer`, `sendScroll`, `sendKey`). Reserve the gRPC
> `InputService.SendInputEvents` / `StreamInputEvents` RPCs for CLI tools, MCP
> agents, and headless test runners that do not establish a WebRTC session.

---

## 4. Part B: Building a CLI Tool, Test Harness, or MCP Agent

This section shows how to build a standalone CLI tool or AI agent server (e.g.,
in Python, Go, Rust, or C++) that works identically against local emulators and
physical USB/lab phones.

### Recipe C1: Connecting to Emulators vs. Physical Phones over ADB

On physical Android devices, the `services_v2` server listens on the abstract
Unix socket `localabstract:aemu_v2`. Forward a dynamic host port with `adb`:

```python
import subprocess
import grpc

def connect_device(target: str) -> grpc.Channel:
    """Connects to either 'localhost:8554' (emulator) or 'adb:<serial>' (physical)."""
    if target.startswith("adb:"):
        serial = target.removeprefix("adb:")
        port = subprocess.check_output(
            ["adb", "-s", serial, "forward", "tcp:0", "localabstract:aemu_v2"],
            text=True,
        ).strip()
        endpoint = f"127.0.0.1:{port}"
    else:
        endpoint = target
    return grpc.insecure_channel(endpoint)
```

### Recipe C2: Frame-Accurate "Act + Observe" Loop (No `sleep()` Needed)

A classic bug in UI test frameworks and AI agents is taking a screenshot too
soon after tapping a button. `services_v2` solves this via monotonic clock
synchronization:

1. `SendInputEvents` returns `last_event_dispatched_at_nanos` (the exact
   `CLOCK_MONOTONIC` timestamp when the final event entered the OS input queue).
2. `GetScreenshot` returns `presentation_time_nanos` on the same
   `CLOCK_MONOTONIC` clock.

```python
def tap_and_capture(input_stub, display_stub, x: float, y: float) -> bytes:
    """Taps (x, y) and returns a PNG guaranteed to be rendered after the tap."""
    # 1. Dispatch a 50ms tap gesture paced by the server
    resp = input_stub.SendInputEvents(
        input_service_pb2.SendInputEventsRequest(
            events=[
                input_service_pb2.InputEvent(
                    timestamp_nanos=0,
                    input_source_id=1,  # Non-zero ID identifies an automated agent
                    pointer=input_service_pb2.PointerEvent(
                        action=input_service_pb2.POINTER_ACTION_DOWN,
                        pointers=[input_service_pb2.Pointer(x=x, y=y)],
                    ),
                ),
                input_service_pb2.InputEvent(
                    timestamp_nanos=50_000_000,  # +50ms
                    input_source_id=1,
                    pointer=input_service_pb2.PointerEvent(
                        action=input_service_pb2.POINTER_ACTION_UP,
                        pointers=[input_service_pb2.Pointer(x=x, y=y)],
                    ),
                ),
            ]
        )
    )
    barrier_nanos = resp.last_event_dispatched_at_nanos

    # 2. Fetch screenshots until presentation_time_nanos >= barrier_nanos
    while True:
        shot = display_stub.GetScreenshot(
            display_service_pb2.GetScreenshotRequest(
                display_id=0,
                format=common_pb2.IMAGE_FORMAT_PNG,
                max_dimension=1080,  # Server downscales preserving aspect ratio
            )
        )
        if shot.presentation_time_nanos >= barrier_nanos:
            return shot.image_data
```

### Recipe C3: Recording Human Demonstrations (`StreamInputActivity`)

If you are building a macro recorder or recording human demonstrations to train
an AI agent, subscribe to `InputService.StreamInputActivity`. Every input event
carries its `input_source_id`:

- `input_source_id == 0`: Human interacting via the host UI window.
- `input_source_id > 0`: Automated agent or CLI script.

```python
for event in input_stub.StreamInputActivity(
    input_service_pb2.StreamInputActivityRequest()
):
    actor = "HUMAN" if event.input_source_id == 0 else f"AGENT#{event.input_source_id}"
    print(f"[{event.timestamp_nanos} ns] {actor}: {event.WhichOneof('event')}")
```

### Recipe C4: Driving 3D Pose, Environment, Foldables/Rollables, Battery, GPS & Telephony

`SensorsService` uses a **two-layer physics & override architecture**:
- **Layer 1 (`pose` + `environment` $\rightarrow$ synthesized `imu`):** Setting 3D pose (`rotation_degrees`, `position_meters`, `velocity_meters_per_second`, `ambient_motion`) or ambient environment (`light_lux`, `proximity_centimeters`, `ambient_temperature_celsius`, etc.) drives the emulator's rigid-body physics model. The server automatically synthesizes frame-consistent `imu` readings (`accelerometer`, `gyroscope`, `magnetometer`, `orientation_degrees`).
- **Layer 2 (`raw_overrides`):** For low-level fault injection (e.g., 0g freefall or sensor fusion divergence), callers pin individual HAL channels via `raw_overrides` and later restore physics synthesis with `clear_raw_overrides = True`.

```python
# 1. Rotate the phone 90° into landscape and step into a dark room.
#    The server automatically synthesizes gravity on `accelerometer` (triggering
#    screen auto-rotation), angular velocity on `gyroscope`, and rotated
#    geomagnetic vectors on `magnetometer`.
sensors_stub.UpdateSensorsState(
    sensors_service_pb2.UpdateSensorsStateRequest(
        pose=sensors_service_pb2.DevicePose(
            rotation_degrees=sensors_service_pb2.Vector3f(x=0.0, y=0.0, z=90.0),
            ambient_motion=0.1,
        ),
        environment=sensors_service_pb2.AmbientEnvironment(
            light_lux=5.0,
            proximity_centimeters=0.0,
        ),
        transition=sensors_service_pb2.TRANSITION_MODE_SMOOTH,
    )
)

# 2. Configure multi-hinge / tri-fold angles or rollable display extension
sensors_stub.UpdatePostureState(
    sensors_service_pb2.UpdatePostureStateRequest(
        hinges=[
            sensors_service_pb2.HingeState(hinge_id=0, angle_degrees=180.0),
            sensors_service_pb2.HingeState(hinge_id=1, angle_degrees=90.0),
        ],
        rollables=[
            sensors_service_pb2.RollableState(rollable_id=0, extension_percent=75.0),
        ],
        transition=sensors_service_pb2.TRANSITION_MODE_SMOOTH,
    )
)

# 3. Advanced fault injection: pin raw accelerometer to 0g freefall, then restore physics
sensors_stub.UpdateSensorsState(
    sensors_service_pb2.UpdateSensorsStateRequest(
        raw_overrides=sensors_service_pb2.RawSensorOverrides(
            accelerometer=sensors_service_pb2.Vector3f(x=0.0, y=0.0, z=0.0),
        )
    )
)
sensors_stub.UpdateSensorsState(
    sensors_service_pb2.UpdateSensorsStateRequest(clear_raw_overrides=True)
)

# 4. Set battery to 15% discharging without changing battery health or temperature
battery_stub.UpdateBatteryState(
    battery_service_pb2.UpdateBatteryStateRequest(
        level_percent=15,
        charger_source=battery_service_pb2.CHARGER_SOURCE_NONE,
    )
)

# 5. Play a multi-waypoint GPS route at 2x speed on the server
location_stub.PlayRoute(
    location_service_pb2.PlayRouteRequest(
        playback_speed_multiplier=2.0,
        waypoints=[
            location_service_pb2.RouteWaypoint(
                location=location_service_pb2.LocationState(
                    latitude=37.4220, longitude=-122.0841
                ),
                delay_ms=0,
            ),
            location_service_pb2.RouteWaypoint(
                location=location_service_pb2.LocationState(
                    latitude=37.4280, longitude=-122.0800
                ),
                delay_ms=5000,
            ),
        ],
    )
)

# 6. Deliver an incoming SMS and ring a phone call
telephony_stub.SendSms(
    telephony_service_pb2.SendSmsRequest(
        sender_number="+16505550199",
        message_body="Your verification code is 482910",
    )
)
telephony_stub.TransitionCall(
    telephony_service_pb2.TransitionCallRequest(
        phone_number="+16505550199",
        action=telephony_service_pb2.TransitionCallRequest.RING,
    )
)
```

### Recipe C5: VM Lifecycle & Snapshots with Long-Running Operations (AIP-151)

Because saving, restoring, importing, and exporting multi-gigabyte VM snapshots
can take seconds, those RPCs return a `google.longrunning.Operation`:

```python
from google.longrunning import operations_pb2, operations_pb2_grpc

ops_stub = operations_pb2_grpc.OperationsStub(channel)

# 1. Start saving a snapshot
op = hypervisor_stub.CreateSnapshot(
    hypervisor_service_pb2.CreateSnapshotRequest(
        snapshot_id="clean-boot-state",
        label="Clean Boot",
        description="Freshly booted home screen before test suite",
    )
)

# 2. Wait for the Long-Running Operation to finish
if not op.done:
    op = ops_stub.WaitOperation(
        operations_pb2.WaitOperationRequest(name=op.name)
    )

if op.HasField("error"):
    raise RuntimeError(f"Snapshot failed: {op.error.message}")

# 3. Unpack the strongly-typed Snapshot result
snapshot = hypervisor_service_pb2.Snapshot()
op.response.Unpack(snapshot)
print(f"Saved snapshot {snapshot.snapshot_id} ({snapshot.disk_size_bytes} bytes)")
```

---

## 5. gRPC Status Code Cheat Sheet

All services use standard gRPC status codes with consistent semantics so clients
can implement a single error handler:

| gRPC Status Code      | Meaning in `services_v2`                                                                                                                                   | Recommended Client Action                                            |
| :-------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------- |
| `UNIMPLEMENTED`       | The server binary does not implement this RPC/service yet.                                                                                                 | Hide/disable the feature cleanly.                                    |
| `FAILED_PRECONDITION` | Operation is invalid in the current device state (e.g., `HypervisorService` on a physical device, or restoring a snapshot while one is already in flight). | Show a non-fatal status message or check `GetDeviceCapabilities`.    |
| `PERMISSION_DENIED`   | Attempted to mutate a capability whose `access_mode` is `ACCESS_MODE_READ_ONLY` (e.g., writing hardware IMU sensors on a physical phone).                  | Disable write controls when `access_mode != ACCESS_MODE_READ_WRITE`. |
| `INVALID_ARGUMENT`    | Malformed request field (e.g., `level_percent > 100` or empty required identifier).                                                                        | Fix client request validation.                                       |
| `NOT_FOUND`           | Requested `display_id`, `snapshot_id`, or `session_id` does not exist.                                                                                     | Refresh list via `ListDisplays` / `ListSnapshots`.                   |
| `UNAVAILABLE`         | Device disconnected (e.g., USB cable unplugged, ADB tunnel dropped, or emulator shut down).                                                                | Re-run ADB port forward / reconnect backoff loop.                    |
