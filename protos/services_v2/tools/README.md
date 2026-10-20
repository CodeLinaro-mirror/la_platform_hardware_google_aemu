# AEMU v2 TypeScript Protoc Tooling

This directory contains the code generation tooling and plugin wrappers used by Bazel (`ts_proto_library`) to compile AEMU v2 Protocol Buffers into TypeScript definitions for gRPC-Web and WebRTC clients (such as [Aquarium](`emulator/ui/aquarium`)).

---

## 1. Architectural Philosophy (The "Why")

### Why a Checked-in Standalone Bundle (`protoc_gen_ts_proto.js`)?
1. **100% Hermeticity:** Protobuf compilation is on the critical build path for both host emulator tools and web clients. Relying on dynamic `npm install` or unmanaged local `node_modules` breaks hermeticity and offline builds.
2. **Cross-Platform & Windows RBE Compatibility:** On Windows Remote Build Execution (`--config rbe-win-x64`), nested npm `node_modules` with symlinks and deep virtual stores frequently cause path-length and extraction errors. A single, self-contained CommonJS file has zero external dependencies and executes reliably on all platforms.
3. **Low BCR Overhead:** Managing `ts-proto` and all its transitive dependencies as individual Bazel BCR modules would require mirroring dozens of npm tarballs to Google Cloud Storage. A pre-bundled JavaScript artifact avoids this maintenance burden.

### Why a Cross-Platform Launcher (`protoc_gen_ts_proto.py`)?
- `protoc` invokes plugins as child processes via `--plugin=protoc-gen-ts_proto=<binary>`.
- Shell scripts (`.sh`) cannot be executed by Windows `CreateProcess` without a POSIX shell environment.
- The `py_binary` target in `BUILD.bazel` automatically generates native Windows launcher stubs (`protoc_gen_ts_proto.exe`) on Windows and executable scripts on macOS/Linux.
- It dynamically resolves the hermetic Node.js runtime provided by Bazel (`@rules_nodejs` toolchain) mounted into the build sandbox via `NODE_BINARY`.

---

## 2. File Overview

| File | Purpose |
| :--- | :--- |
| **`protoc_gen_ts_proto.js`** | Self-contained CommonJS bundle containing `ts-proto`, `protobufjs`, and `ts-poet`. |
| **`protoc_gen_ts_proto.py`** | Cross-platform Python launcher wrapping Node.js execution. |
| **`update_ts_proto_bundle.sh`** | Maintenance script to rebuild `protoc_gen_ts_proto.js`. |
| **`BUILD.bazel`** | Declares the `py_binary` tool target. |

---

## 3. How to Update the Bundle (The "How")

The bundled JavaScript plugin is generated using [esbuild](https://esbuild.github.io/) in a temporary scratch directory.

### When to Regenerate
Run this script whenever:
- Upgrading to a newer version of `ts-proto`.
- Applying custom patches or generator options to the TypeScript protoc plugin.

### How to Run
From within this directory (or anywhere in the repository):

```bash
# Update to default pinned version (2.6.1)
./update_ts_proto_bundle.sh

# Or update to a specific ts-proto version
./update_ts_proto_bundle.sh 2.6.2
```

### What the Script Does
1. Installs the requested version of `ts-proto` and `esbuild` into an isolated temporary folder.
2. Creates a lightweight shim for non-native formatters (`dprint-node`) to keep the bundle pure JavaScript.
3. Bundles `ts-proto` targeting Node 20.
4. Injects static version metadata so the bundle does not attempt to read `package.json` from the filesystem at runtime.
5. Emits the standalone `protoc_gen_ts_proto.js` file ready to be committed to git.
