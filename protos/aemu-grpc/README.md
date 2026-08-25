# AEMU gRPC Python Client SDK (`aemu-grpc`)

`aemu-grpc` is the canonical Python client library for discovering, inspecting, controlling, and communicating with locally running or remote Android Emulators (both classic AEMU and next-gen `qemu-next` / Goldfish emulators) over gRPC.

---

## Installation & Build Modes

`aemu-grpc` supports both AOSP Bazel builds (de-duplicating service schemas against `//protos/services`) and standard Python `pyproject.toml` / `setup.py` non-Bazel packaging (automatically compiling `.proto` files from `../services`).

### Mode A: AOSP / Bazel Build & Test

In Bazel workspaces, reference `@aemu//protos/aemu-grpc:aemu_grpc_lib`:

```bash
# Build the Bazel Python library
bazel build @aemu//protos/aemu-grpc:aemu_grpc_lib

# Run the 40-test Bazel test suite
bazel test @aemu//protos/aemu-grpc:aemu_grpc_test
```

### Mode B: Standard Pip / `pyproject.toml` (Non-Bazel Environments)

For classic emulator environments, virtual environments, or building wheels, you can install directly using `pip`. During installation, `setup.py` automatically discovers `../services/` and compiles all `*_pb2.py` and `*_pb2_grpc.py` stubs into `src/aemu/proto/`:

```bash
# Editable install into your virtual environment for development
pip install -e .

# Build a binary wheel (.whl) or source distribution
pip wheel .
# or
python -m build
```

---

## 1. Emulator Discovery

Local emulators write discovery files (`pid_<PID>.ini`) to standard temporary directories (`~/.android/avd/running`, OS temporary folders, `XDG_RUNTIME_DIR`, etc.).

### Discovering Local Emulators

```python
from aemu.discovery.emulator_discovery import EmulatorDiscovery

disc = EmulatorDiscovery()
print(f"Found {disc.available()} running emulators.")

# Iterate over all active emulators
for emu in disc.emulators():
    print(f"Name: {emu.name()} | PID: {emu.pid()} | Status: {emu}")

# Get the first running emulator (raises EmulatorNotFound if empty)
first_emu = disc.first()

# Find by specific PID or property
emu = disc.find_by_pid(12345)
emu = disc.find_emulator("port.serial", "5554")
```

### Remote / Custom Connections

To connect to a remote emulator endpoint without local discovery `.ini` files:

```python
remote_emu = EmulatorDiscovery.connection("emulator.example.com:8554", token="secret-bearer-token")
```

---

## 2. Managed Client Sessions & gRPC Services

`EmulatorDescription` provides managed context managers for both synchronous (`.client()`) and asynchronous (`.async_client()`) gRPC communication.

### Synchronous Client

```python
from aemu.proto.emulator_controller_pb2 import SmsMessage

with disc.first().client() as client:
    # Wait up to 10 seconds for the gRPC server to bind and become ready
    if client.wait_for_ready(timeout=10.0):
        # Access typed service stubs directly as properties
        client.controller.sendSms(
            SmsMessage(srcAddress="(650) 555-0100", text="Hello from aemu-grpc!")
        )
```

### Asynchronous Client (`asyncio`)

```python
async def manipulate_emulator(emu):
    async with emu.async_client() as client:
        if await client.wait_for_ready(timeout=10.0):
            # Asynchronously query VM status or capture screenshots
            status = await client.controller.getStatus()
            print("VM Status:", status)
```

### Available Service Stubs

The client session (`client`) exposes lazily instantiated properties for all AEMU gRPC services:

- `client.controller`: `EmulatorControllerStub` (VM state, keyboard, mouse, touch, screenshot, clipboard, SMS)
- `client.snapshot`: `SnapshotServiceStub` (save, load, list, push, pull snapshots)
- `client.ui_controller`: `UiControllerStub` (emulator UI window styling & pane management)
- `client.screen_recording`: `ScreenRecordingStub` (start/stop video recording)
- `client.sensor`: `SensorServiceStub` (GPS, accelerometer, gyroscope, ambient light, battery state)
- `client.modem`: `ModemStub` (cellular signal strength, SIM state, phone calls)
- `client.rtc`: `RtcStub` (WebRTC v1 media stream control)
- `client.rtc_v2`: `RtcV2Stub` (WebRTC v2 interactive signaling & ICE configuration)
- `client.avd`: `AvdServiceStub` (AVD configuration metadata)
- `client.car`: `CarServiceStub` (automotive rotary controller & sensor inputs)
- `client.bluetooth`: `EmulatedBluetoothServiceStub` (Bluetooth GATT & device simulation)
- `client.virtual_scene`: `VirtualSceneServiceStub` (virtual scene camera control)
- `client.waterfall`: `WaterfallStub` (high-throughput host-to-guest execution/forwarding)

---

## 3. Process Lifecycle & Graceful Shutdown

You can inspect OS process liveness or gracefully terminate emulator processes:

```python
# Check liveness and psutil.Process
if emu.is_alive():
    proc = emu.process()
    print("CPU usage:", proc.cpu_percent())

# Gracefully shut down via gRPC (with fallback to SIGTERM/SIGKILL if unresponsive)
# Synchronous shutdown:
emu.shutdown(timeout=30)

# Asynchronous non-blocking shutdown:
await emu.ashutdown(timeout=30)
```

To clean up stale discovery `.ini` files left behind by crashed emulators:
```python
remaining = disc.cleanup_stale()
```

---

## 4. Authentication & Security

`aemu-grpc` automatically negotiates the strongest available authentication based on discovery metadata:

1. **JWT Security (`EmulatorSecurity.Jwt`)**: Automatically signs request metadata tokens using Tink ECDSA P-521 keys whenever `grpc.jwks` and `grpc.jwk_active` are present and Tink is available.
2. **Token Security (`EmulatorSecurity.Token`)**: Uses Bearer token headers when `grpc.token` is provided.
3. **No Security (`EmulatorSecurity.Nothing`)**: Connects without authentication headers when security is disabled or unconfigured.

You can explicitly override security mode via channel arguments:
```python
client = emu.client([("emulator.security", "token")])
```
