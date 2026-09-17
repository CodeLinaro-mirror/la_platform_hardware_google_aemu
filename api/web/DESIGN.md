# Component Design: Android Web Client (`@aemu//api/web`)

**Location:** `hardware/google/aemu/api/web`  
**Bazel Package:** `@aemu//api/web`  
**Package Name:** `@android/web`  
**Target:** React 18/19 Component Library & Headless TypeScript SDK for Android
Devices & Emulators  
**Backend:** Android v2 gRPC Services (`//protos/services_v2`) over `grpcweb` &
WebRTC `Switchboard`

---

## 1. Executive Summary & Philosophy

**`@android/web`** is the official modern React component library and TypeScript
SDK for interacting with Android devices (both physical hardware devices and
virtual emulators) in web browsers.

### Core Design Principles:

1. **Hardware Agnostic (Physical & Virtual):** Interacts transparently with both
   physical Android hardware devices (via on-device daemon/bridge) and remote
   virtual emulators (AEMU/Goldfish/Cuttlefish). Features specific to virtual
   environments (such as VM snapshots or QEMU monitor commands) degrade
   gracefully when communicating with physical hardware.
2. **Single UI Component Footprint:** The library provides **exactly one visual
   UI component**: `<DeviceView />` (which mounts the WebRTC `<video>` display
   and handles mouse, multi-touch, stylus, and keyboard I/O).
3. **Zero External Sidecars:** Connects directly to embedded `grpcweb` (HTTP/1.1
   CORS) and WebRTC `Switchboard`. No Envoy or custom WebSocket gateways
   required.
4. **Headless Service APIs & React Hooks:** All device features (Sensors,
   Posture, Location/GPS, Snapshots, VM lifecycle, Clipboard, Capabilities) are
   exposed as typed, framework-agnostic TypeScript API clients (`DeviceClient`)
   and lightweight React hooks (`useSensors`, `useLocation`, `useVm`,
   `useCapabilities`, `useDevice`). Developers have complete freedom to design
   their own surrounding UI controls.
5. **Rich v2 Multi-Modal Input:** Supports multi-touch gestures (up to 10
   pointers), desktop mouse capture (relative deltas & button masks), stylus pen
   (pressure, tilt, orientation), and DOM/Android keycodes directly over WebRTC
   SCTP DataChannels.

---

## 2. Component Architecture & Data Flow

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   React Application Layer                                   │
│                                                                                             │
│  <DeviceProvider uri="http://localhost:8080" token="...">                                   │
│    ├── <DeviceView displayId={0} />         ──> WebRTC <video> + Multi-Touch/Mouse Overlay  │
│    ├── <DeveloperControlBar />              ──> Home, Back, Recents, Power, Volume buttons  │
│    ├── <DeveloperMultiDisplayTabs />        ──> Dynamic primary/secondary display switcher  │
│    └── <DeveloperSensorsPanel />            ──> Fold angle, GPS, battery controls           │
└──────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                               │
                          React Context / Hooks (useDevice)
                                               │
┌──────────────────────────────────────────────▼──────────────────────────────────────────────┐
│                               DeviceCore (TypeScript SDK)                                   │
│                                                                                             │
│  ├── RtcController          ──> Manages RTCPeerConnection, JSEP negotiation, audio tracks   │
│  ├── InputChannel           ──> Serializes v2 InputEvents over "input_v2" SCTP DataChannel  │
│  ├── CapabilitiesClient     ──> Queries displays, hardware features, and codecs             │
│  ├── ServiceClients         ──> VmService, SystemService, ScreenCaptureService              │
│  └── GrpcWebTransport       ──> Standard HTTP/1.1 @grpc/grpc-web transport via proxy        │
└──────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                               │
                          HTTP/1.1 gRPC-Web (CORS) & WebRTC RTP/SCTP
                                               │
┌──────────────────────────────────────────────▼──────────────────────────────────────────────┐
│                         Android Device / Host (Physical or Virtual)                         │
│                                                                                             │
│  ├── grpcweb::GrpcWebServer            ──> In-process HTTP/1.1 to gRPC translator           │
│  ├── grpc::v2::* Services              ──> RtcService, CapabilitiesService, InputService    │
│  └── videobridge                       ──> Switchboard (H.264/VP8 video + SCTP DataChannel) │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. UI Component & React API Design

### 3.1 The Single UI Component: `<DeviceView />`

`<DeviceView />` is the only visual UI element provided by the library. It
embeds the WebRTC `<video>` element, manages the canvas layout/aspect ratio, and
attaches an event handler overlay that translates mouse, multi-touch, stylus,
and keyboard events to the device:

```tsx
import React from "react";
import { DeviceProvider, DeviceView } from "@android/web";

export function SimpleDeviceStream() {
  return (
    <DeviceProvider uri="http://localhost:8080">
      <div style={{ width: 360, height: 740, background: "#000" }}>
        <DeviceView
          displayId={0}
          onStateChange={(state) => console.log("WebRTC Connection:", state)}
          onError={(err) => console.error("Device Error:", err)}
        />
      </div>
    </DeviceProvider>
  );
}
```

---

### 3.2 Sensor API & Headless Controls

All environmental controls and device services are decoupled from the UI,
available either via framework-agnostic TypeScript objects (`DeviceClient`) or
idiomatic React hooks (`useSensors`, `useLocation`, `useVm`, `useDevice`).

#### Example: Interacting with Sensors in React (`useSensors`)

```tsx
import React from "react";
import { DeviceView, DeviceProvider, useSensors } from "@android/web";

function App() {
  return (
    <DeviceProvider uri="http://localhost:8080">
      <div style={{ display: "flex", gap: "24px" }}>
        {/* The single UI component */}
        <DeviceView displayId={0} />

        {/* Custom developer-built sensor control panel */}
        <SensorControlPanel />
      </div>
    </DeviceProvider>
  );
}

function SensorControlPanel() {
  const {
    sensors, // Reactive live sensor state { accelerometer, orientationDegrees, lightLux, ... }
    setAccelerometer, // (vec: { x: number; y: number; z: number }) => Promise<void>
    setOrientation, // (rot: { azimuth: number; pitch: number; roll: number }) => Promise<void>
    setAmbientLight, // (lux: number) => Promise<void>
    setProximity, // (distanceCm: number) => Promise<void>
    setTemperature, // (celsius: number) => Promise<void>
  } = useSensors();

  return (
    <div className="panel">
      <h3>Device Sensors & Orientation</h3>

      {/* Orientation controls */}
      <div>
        <label>
          Pitch: {sensors.orientationDegrees?.pitch?.toFixed(1) ?? 0}°
        </label>
        <button
          onClick={() => setOrientation({ azimuth: 0, pitch: 90, roll: 0 })}
        >
          Landscape (90°)
        </button>
        <button
          onClick={() => setOrientation({ azimuth: 0, pitch: 0, roll: 0 })}
        >
          Portrait (0°)
        </button>
      </div>

      {/* Accelerometer */}
      <div>
        <label>Accelerometer (Z-axis gravity):</label>
        <button onClick={() => setAccelerometer({ x: 0, y: 0, z: 9.81 })}>
          Face Up (Z = 9.81 m/s²)
        </button>
        <button onClick={() => setAccelerometer({ x: 0, y: 0, z: -9.81 })}>
          Face Down (Z = -9.81 m/s²)
        </button>
      </div>

      {/* Ambient Light Sensor */}
      <div>
        <label>Ambient Light: {sensors.lightLux ?? 100} Lux</label>
        <input
          type="range"
          min="0"
          max="2000"
          value={sensors.lightLux ?? 100}
          onChange={(e) => setAmbientLight(Number(e.target.value))}
        />
      </div>
    </div>
  );
}
```

---

#### Example: Pure TypeScript / Headless SDK Usage (No React Required)

The sensor API can also be used in plain TypeScript, Vue, Svelte, or automated
test scripts:

```typescript
import { DeviceClient } from "@android/web";

// 1. Initialize client with gRPC-Web proxy endpoint
const client = new DeviceClient({ endpoint: "http://localhost:8080" });
await client.connect();

// 2. Query current sensor state
const state = await client.sensors.getSensorsState();
console.log("Current Accelerometer:", state.acceleration); // { x: 0, y: 9.81, z: 0 }
console.log("Current Orientation:", state.orientationDegrees);

// 3. Update individual sensors with partial updates (using field masks under the hood)
await client.sensors.updateSensorsState({
  acceleration: { x: 0, y: 9.81, z: 0 },
  orientationDegrees: { x: 0, y: 45, z: 0 },
  lightLux: 500, // 500 lux
  proximityCentimeters: 0, // 0 cm (object touching sensor)
  ambientTemperatureCelsius: 22.5, // 22.5 °C
});

// 4. Stream real-time sensor updates from the device
const unsubscribe = client.sensors.streamSensors((updatedState) => {
  console.log("Live sensor update from guest OS:", updatedState);
});

// Later: unsubscribe when done
unsubscribe();
```

---

## 4. Subsystems & Capabilities API

### 4.1 Display & Input Subsystem (`InputChannel`)

Keyboard, mouse, scroll, and touch interactions are delivered directly over the
**WebRTC DataChannel** (`"input_v2"` SCTP stream), completely bypassing the gRPC
HTTP layer. This avoids HTTP encapsulation overhead and enables sub-millisecond,
low-latency input dispatch directly to the host's evdev pipeline.

DOM events captured by `<DeviceView />` are serialized into binary
`android.emulation.v2.input.InputEvent` protobuf messages and dispatched via
`InputChannel.sendOrQueue()`:

| Input Modality    | DOM Event Mapping                                          | Generated v2 Protobuf Event                                                                       | Key Advantage                                                            |
| :---------------- | :--------------------------------------------------------- | :------------------------------------------------------------------------------------------------ | :----------------------------------------------------------------------- |
| **Multi-Touch**   | `pointerdown`, `pointermove`, `pointerup`, `pointercancel` | `PointerEvent` with `tool_type = FINGER`, normalized $(x, y) \in [0.0, 1.0]$, `pointer_id` (0..9) | Automatic multi-finger slot allocation via host `SlotRegistry`.          |
| **Mouse Motion**  | `mousemove`, `pointermove`                                 | `PointerEvent` with `tool_type = MOUSE`, sub-pixel $(x, y)$, relative `delta_x`, `delta_y`        | Supports relative mouse capture in Android Desktop Mode (`pointerlock`). |
| **Mouse Buttons** | `mousedown`, `mouseup`                                     | `PointerEvent` with `button_mask` (`BUTTON_MASK_PRIMARY`, `SECONDARY`, `TERTIARY`)                | Native right-click and middle-click handling.                            |
| **Mouse Wheel**   | `wheel`                                                    | `PointerEvent` with `scroll_x`, `scroll_y`                                                        | Smooth, hardware-scaled vertical and horizontal scrolling.               |
| **Stylus / Pen**  | `pointerdown` / `pointermove` (`pointerType === 'pen'`)    | `PointerEvent` with `tool_type = STYLUS`, `pressure`, `tilt_radians`, `orientation_radians`       | Accurate drawing in Android graphics apps with tilt and pressure.        |
| **Keyboard**      | `keydown`, `keyup`                                         | `KeyEvent` with `dom_code` (e.g. `"KeyA"`, `"Enter"`, `"ArrowUp"`), `action = DOWN / UP`          | Seamless mapping from web DOM `event.code` to Linux evdev keys.          |
| **Text Commit**   | `input`, `compositionend`                                  | `TextEvent` with UTF-8 `text`                                                                     | Fast typing and IME support without keycode loss.                        |

---

### 4.2 WebRTC JSEP Signaling Subsystem (`RtcController`)

Signaling is performed directly over gRPC-Web using `RtcService`:

1. **Session Setup:** Calls `RtcService.RequestRtcStream(requested_tracks)`
   $\to$ receives `RtcSession handle` + `IceServer` list.
2. **Streaming JSEP Stream:** Opens
   `RtcService.ReceiveJsepMessageStream(handle)` to listen for asynchronous SDP
   Answers and ICE candidates.
3. **Offer Submission:** Browser generates `RTCSessionDescriptionInit` Offer and
   sends it via unary `RtcService.SendJsepMessage(handle, offer_json)`.
4. **Data Channels:** Automatically creates and binds the `"input_v2"` SCTP
   DataChannel to `InputChannel` as soon as the WebRTC peer connection
   initializes.
5. **Media Streams:** Dispatches incoming `MediaStreamTrack` (primary video,
   secondary video, audio) directly to `<video>` elements and Web Audio
   contexts.

---

### 4.3 Discovery & Capabilities Subsystem (`CapabilitiesClient`)

Automatically calls `CapabilitiesService.GetCapabilities()` upon connection to
discover:

- **Displays:** Total active displays, logical widths, heights, DPIs, refresh
  rates, and primary flag.
- **Hardware Features:** Cellular network, GPS, multi-touch support, physical
  keyboard, biometric scanner, hinge sensor.
- **Audio Devices:** Speaker output and microphone capture capability.
- **Codecs:** Supported video decoders (H.264, VP8, VP9, AV1).

---

### 4.4 Environmental & Virtual Device Control

Exposes gRPC-Web convenience clients for:

- **Foldables & Posture (`SystemService` / `SensorsService`):**
  - `setHingeAngle(degrees: number)` (0° = closed, 90° = tabletop, 180° = flat
    open).
  - `setDevicePosture(posture: 'CLOSED' | 'HALF_OPENED' | 'OPENED' | 'FLIPPED')`.
- **Geolocation (`LocationService`):**
  - `setGpsLocation({ latitude, longitude, altitude, speed, heading })`.
- **Virtual Power & Battery (`BatteryService`):**
  - `setBatteryLevel(percentage: number)`,
    `setChargingState('CHARGING' | 'DISCHARGING')`.
- **VM Snapshots & Lifecycle (`VmService`):**
  - `createSnapshot(name: string)`, `restoreSnapshot(name: string)`,
    `pauseVm()`, `resumeVm()`.
- **Android Clipboard (`ClipboardService`):**
  - `setClipboardText(text: string)`, `getClipboardText(): Promise<string>`.

---

## 5. Authentication & Security Architecture

### 5.1 The Protocol Standard (`Authorization: Bearer <JWT>`)

All gRPC-Web communication over HTTP/1.1 requires the standard HTTP
authentication header:

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

- **Proxy Forwarding:**
  [`goldfish::grpcweb::GrpcWebServer`](../../../../generic/goldfish/emulator/tools/grpc-web/ARCHITECTURE.md)
  automatically propagates the `Authorization` header directly into the backend
  `grpc::ClientContext` metadata.
- **Server-Side Validation:** Emulator authentication interceptors validate the
  JWT signature, issuer (`iss`), audience (`aud`), and expiration (`exp`).
  Unauthenticated or expired tokens return `grpc::StatusCode::UNAUTHENTICATED`
  (HTTP 401).

---

### 5.2 Dynamic Token Providers & Auto-Refresh

JWTs in production web environments expire and rotate. The SDK accepts either a
static token string or a dynamic, asynchronous token provider function:

```typescript
export type AuthProvider =
  | string // Static JWT string
  | (() => string | Promise<string>) // Dynamic async token supplier
  | {
      getToken: () => Promise<string>; // Asynchronous token supplier
      onUnauthorized?: (err: Error) => void; // Callback triggered when 401 UNAUTHENTICATED is returned
    };
```

---

### 5.3 gRPC-Web Client Interceptors

The `GrpcWebTransport` layer utilizes client-side interceptors to inject fresh
tokens and handle expiration:

```typescript
class JwtAuthInterceptor {
  constructor(private tokenProvider: () => Promise<string>) {}

  async intercept(request: any, invoker: any) {
    const metadata = request.getMetadata();
    const token = await this.tokenProvider();
    if (token) {
      metadata["Authorization"] = `Bearer ${token}`;
    }
    return invoker(request);
  }
}
```

---

### 5.4 React Usage Examples

#### Static Token (Simple / Local Dev)

```tsx
<DeviceProvider uri="http://localhost:8080" token="eyJhbGciOi...">
  <DeviceView displayId={0} />
</DeviceProvider>
```

#### Dynamic / Corporate SSO / Firebase / OAuth Provider

```tsx
import { useAuth } from "./auth-context"; // e.g. Firebase, Auth0, Google Identity

function App() {
  const { getIdToken, refreshToken, logout } = useAuth();

  return (
    <DeviceProvider
      uri="http://localhost:8080"
      auth={{
        getToken: async () => await getIdToken(),
        onUnauthorized: () => {
          console.warn("JWT expired, refreshing session...");
          refreshToken().catch(() => logout());
        },
      }}
    >
      <DeviceView displayId={0} />
    </DeviceProvider>
  );
}
```

---

### 5.5 WebRTC Security Handshake

1. **Signaling Authentication:** The WebRTC session initialization
   (`RtcService.RequestRtcStream` and `ReceiveJsepMessageStream`) is
   authenticated via gRPC-Web with the `Bearer <JWT>`.
2. **Ephemeral Session Isolation:** The host generates a cryptographically
   random GUID (`RtcSession.session_id`) bound to that authenticated participant
   session.
3. **Transport Security:** All real-time media tracks (H.264/VP8) and SCTP
   DataChannels (`"input_v2"`) are encrypted end-to-end using standard
   **DTLS-SRTP**.

---

## 6. Build System Integration (`Bazel`)

1. **AEMU Library Package (`@aemu//api/web`):**
   - Directly compiles proto TypeScript definitions from local
     `//protos/services_v2:services_v2_ts_proto`.
   - Generates typed ESM bundles and `.d.ts` declaration files via
     `ts_project(name = "web")`.
   - Exposes `@android/web` package distribution via
     `copy_to_directory(name = "pkg")`.
2. **Goldfish Consumption & Web Distribution
   (`@goldfish//emulator/ui/aquarium`):**
   - Links `@aemu//api/web:pkg` into local `node_modules/@android/web`.
   - Compiles the example demo app via `ts_project(name = "example")`.
   - Produces the optimized production bundle via native Go-compiled
     `esbuild(name = "bundle")`.
   - Generates the standalone `dist/` web directory via
     `copy_to_directory(name = "dist")`, containing `index.html` and assets.
   - The compiled `dist/` directory can be packaged as a data dependency of
     `goldfish::grpcweb`, allowing `grpc-web-proxy` or `emulator` to host the
     full UI at `http://localhost:8080/`.
3. **NPM Distribution:**
   - Packaged with standard ESM exports (`src/index.js`) and TypeScript type
     definitions (`types/src/index.d.ts`) under the `@android/web` package name.
