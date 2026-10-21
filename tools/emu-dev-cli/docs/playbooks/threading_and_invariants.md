# Threading, Display Pipeline, and Concurrency Invariants Playbook

**Role:** Operational Playbook  
**Location:** `hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/threading_and_invariants.md`

## 1. Primary Execution Contexts in Goldfish / QEMU

| Thread / Context                             | Responsibilities                                                                                                           | Locking & Blocking Rules                                                                                                                    |
| :------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------ |
| **QEMU Main Loop (BQL)**                     | Device emulation, QEMU DisplayChangeListener (`dcl`) callbacks (`dpy_gfx_update`, `dpy_gfx_switch`), VM state transitions. | Protected by Big QEMU Lock (`BQL`). **Never block** on slow I/O, video encoding, or network RPCs while holding BQL.                         |
| **Android Looper (`EventLoop`)**             | Asynchronous timers (`RecurrentTask`), non-blocking socket/FD watchers (`FdWatch`), UI/controller callbacks.               | Single-threaded cooperative event loop per `Looper`. Callbacks must complete quickly and never block on long locks held by worker threads.  |
| **gfxstream `RenderThread` / `FrameBuffer`** | Host GLES/Vulkan command decoding, composition, and `PostWorker` frame readback callbacks.                                 | High-frequency rendering path. Readback/post listeners must copy or hand off `DisplayFrame` handles without stalling GPU composition.       |
| **gRPC Server Threads**                      | `EmulatorController` RPC handlers (`getScreenshot`, `startRecording`, `streamScreenshot`).                                 | Runs on gRPC thread pool. Must marshal QEMU/device state mutations onto the main loop or synchronize via mutex-protected thread-safe sinks. |

---

## 2. Display Pipeline Architecture (`emulator/plugin/display`)

The display subsystem uses a reactive producer-consumer pipeline
(`@goldfish//emulator/plugin/display:base`):

```
[Producer: QemuDisplay / VirtualDisplay / Renderer]
        │  (DisplaySource::produceFrame)
        ▼
[Transform / Fan-out: DisplayPipeline / Scaling / PixelFormat Conversion]
        │  (DisplaySink::onFrame)
        ▼
[Consumers: VideoRecorder / Screenshot Service / WebRTC / UI Window]
```

### Key Invariants

1. **Shared Immutable Payloads:** `DisplayFrame` buffers passed through `DisplaySource`
   $\rightarrow$ `DisplaySink` must be treated as immutable by downstream consumers so multiple
   sinks (e.g., live UI + `VideoRecorder` + gRPC screenshot stream) can read concurrently without
   data races.
2. **Decoupled Encoding (`VideoRecorder` & `WebMEncoder`):**
   - `VideoRecorder` implements `DisplaySink` to receive frames on the producer thread, enqueues
     them into a bounded/synchronized queue, and encodes them on a dedicated background worker
     thread via `WebMEncoder`.
   - The producer thread (`dpy_gfx_update` or `PostWorker`) must **never** perform synchronous
     VP8/VP9 encoding inline.
3. **Lifecycle & Unregistration:**
   - Sinks connected to a `DisplaySource` must unregister cleanly before destruction (or use
     weak/scoped subscriptions) so late frames in flight do not dereference destroyed sink
     instances.
