---
trigger: model_decision
description: "Consult when writing, refactoring, or reviewing C++ code or unit tests in goldfish (emu-main-next) to reuse //emulator/libs/* targets instead of custom helpers."
---

# Goldfish (`emu-main-next`) Mandatory Library Reuse Catalog (`//emulator/libs/*`)

Before introducing any new helper class, test fixture, server, socket wrapper, or parser in `platform/hardware/generic/goldfish`, check whether `//emulator/libs/*` already provides the capability:

## 1. File I/O & UTF-8 Paths (`//emulator/libs/file`)
- **Header**: `#include "goldfish/file/file.h"`
- **Namespace**: `android::base::file`
- **When to use**: All file reads, writes, directory creation, and path manipulation. Never pass raw `std::string` paths to `std::ifstream`, `std::ofstream`, or `fopen` on Windows.

## 2. INI Configuration Parsing (`//emulator/libs/ini_file`)
- **Header**: `#include "android/goldfish/ini_file.h"`
- **Class**: `android::goldfish::IniFile`
- **When to use**: Reading or writing `.ini` files (`config.ini`, `hardware-qemu.ini`, AVD `.ini` files). Never write custom `key=value` line splitters.

## 3. Embedded HTTP Server (`//emulator/libs/http`)
- **Header**: `#include "goldfish/http/web_server.h"`
- **API**: `goldfish::http::WebServer` / `goldfish::http::CreateWebServer`
- **When to use**: Both production HTTP endpoints and hermetic C++ unit test servers. Never spin up raw socket `accept()` loops in unit tests to mock an HTTP server.

## 4. HTTP Client & Endpoint Resolution (`//emulator/libs/network`)
- **HTTP Client Target**: `//emulator/libs/network:http_client` (`#include "goldfish/network/http_client.h"`, `goldfish::network::HttpClient`)
- **Endpoint Resolution Target**: `//emulator/libs/network` (`#include "goldfish/network/resolve.h"`, `goldfish::network::ResolveEndpoints`)
- **When to use**: Performing HTTP/HTTPS requests or DNS/socket endpoint resolution.

## 5. Async Event Loops & Sockets (`//emulator/libs/async`)
- **Headers**: `goldfish/async/libuv_event_loop.h`, `goldfish/async/threaded_event_loop.h`, `goldfish/async/async_socket.h`
- **APIs**: `goldfish::async::LibuvEventLoop`, `ThreadedEventLoop`, `AsyncSocket`, `UvErrToAbslStatus`
- **When to use**: Asynchronous I/O, timers, sockets, and background event loops instead of raw `uv_loop_t` boilerplate.

## 6. Environment & System Fakes (`//emulator/libs/system`)
- **Production Target**: `//emulator/libs/system` (`#include "android/base/system.h"`, `android::base::System::Get()->EnvGet(...)`)
- **Test Target**: `//emulator/libs/system:test-headers` (`#include "android/base/testing/test_system.h"`, `android::base::TestSystem`)
- **When to use**: Querying environment variables or system paths in production code and overriding them hermetically in unit tests (never call `std::getenv` / `setenv` directly).
