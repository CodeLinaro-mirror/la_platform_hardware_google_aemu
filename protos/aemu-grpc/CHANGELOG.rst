=========
Changelog
=========

Version 0.2 (AOSP Shared Library)
=================================

- **Bazel & Schema Deduplication**: Moved to `@aemu//protos/aemu-grpc`, directly referencing source-of-truth service `.proto` definitions (`:emulator_controller_py_proto`, etc.) without schema duplication.
- **Managed Client Sessions**: Introduced `EmulatorClient` and `AsyncEmulatorClient` context managers with lazily instantiated typed property stubs for all 13 gRPC services.
- **Connection Ready Checks**: Added `.wait_for_ready(timeout=10.0)` for synchronous and asynchronous client sessions to eliminate startup race conditions.
- **Asynchronous Process Shutdown**: Added non-blocking cooperative `EmulatorDescription.ashutdown()` alongside synchronous `shutdown()`.
- **Security & Tink Resiliency**: Added JWT vs Token authentication fallback logic (`_select_security`) and explicit endpoint address precedence (`grpc.address` over `grpc.port`).
