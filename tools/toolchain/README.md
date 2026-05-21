# Android Meson Configurator (amc)

The Android Meson Configurator (`amc`) is a Python-based command-line tool designed to simplify the configuration and compilation of Meson-based projects within the Android Open Source Project (AOSP) build environment. It automates the setup of toolchains and dependencies, bridging the gap between the Meson build system and the Bazel-based dependency management used in AOSP.

## Core Concepts

`amc` works by reading a central configuration file and performing a series of setup steps before invoking Meson.

### 1. Build Configuration File

The tool is driven by a `JSONC` (JSON with Comments) file, typically named `build-config.jsonc`. This file defines all the necessary parameters for the build, including:

-   **Dependencies**: A list of libraries required by the project.
-   **Binaries**: A dictionary where each key is the name of an executable and the value is the corresponding Bazel target. `amc` generates wrapper scripts for these binaries, making them available to Meson's `find_program`. This is particularly useful for build tools and custom code generators.
-   **Meson Options**: Feature flags and settings passed to the `meson setup` command.
-   **Generated Files**: Any additional configuration files that need to be created for the build.
-   **Platform-Specific Settings**: Overrides and additions for different host platforms (e.g., `linux-x64`, `mac-aarch64`, `windows-x64`).

### 2. Toolchain Generation

`amc` generates a set of wrapper scripts for a wide range of toolchain utilities, including:

-   **Compilers**: `cc`, `c++`, `objc`, `rustc`.
-   **Linkers**: `ld`, `lld`, `ld.lld`, `ld64.lld`, `wasm-ld`.
-   **Binary Analysis**: `nm`, `objdump`, `strings`, `size`, `readelf`, `readobj`.
-   **Archive & Library Tools**: `ar`, `ranlib`, `lib`, `dlltool`.
-   **Analysis & Debugging**: `clang-tidy`, `clang-format`, `clang-check`, `lldb`, `llvm-symbolizer`, `dsymutil`.
-   **LLVM Utilities**: `llvm-as`, `llvm-dis`, `llvm-objcopy`, `llvm-strip`, `llvm-cov`, `llvm-profdata`.

These wrappers ensure that Meson (or any other build system) uses the correct Clang toolchain and sysroots provided within the AOSP source tree, along with the necessary flags for the target platform. This process fully supports cross-compilation, allowing you to build for a target platform that is different from your host machine.

### 3. Dependency Management via pkg-config

Meson relies on the `pkg-config` utility to discover libraries and their required compiler/linker flags. Since many dependencies in the AOSP environment are built with Bazel, `amc` bridges this gap by:

1.  Reading the `dependencies` section of the configuration file.
2.  Querying Bazel to get the locations of required library archives (`.a`, `.lib`) and header files.
3.  Generating `.pc` (pkg-config) files that point to these Bazel-built artifacts.

This allows Meson to seamlessly find and link against dependencies without needing to know that they were built by Bazel.

**Note:** For `amc` to resolve dependencies correctly, your Bazel workspace must include the `goldfish_build` module. This is typically done by adding the following to your `MODULE.bazel` file:

```bzl
bazel_dep(name = "goldfish_build")
```

This module provides the necessary platform definitions and toolchains that `amc` relies on.

### 4. Toolchain Version Configuration

By default, `amc` uses the toolchain versions specified in the global `tool_versions.json` file located in `build/bazel/toolchains/`. However, you can override these versions for a specific project by adding a `tool_versions` object to your `build-config.jsonc` file.

This object can be placed in the `common` section to apply to all platforms, or in a platform-specific section for more granular control.

**Example:**

To use a specific version of Clang and Rust for all platforms, add the following to the `common` section of your `build-config.jsonc`:

```json
"common": {
  "tool_versions": {
    "clang": "clang-r522817",
    "rust": "1.72.0"
  },
  ...
}
```

This provides a convenient way to pin toolchain versions for a project, ensuring reproducible builds without modifying global configuration files.

### 5. Artifact Persistence

To ensure that the generated toolchain remains functional even after a `bazel clean` or when Bazel recycles its sandbox, `amc` persists all required dependency artifacts (library archives and header files) into a stable `packages` directory within the toolchain.

These artifacts are organized into the following structure:

-   `<toolchain_dir>/packages/<package_name>/lib/`: Contains the library archives.
-   `<toolchain_dir>/packages/<package_name>/include/<index>/`: Contains the header files.

The persistence mechanism primarily uses **hard links**, which is highly efficient as it avoids consuming additional disk space when the destination is on the same filesystem. If hard-linking fails (e.g., when the toolchain is generated on a different filesystem than the Bazel output base), `amc` automatically falls back to copying the files. The generated `.pc` files are updated to point to these stable, persistent paths.

## 6. Enhanced Dependency Resolution (Opt-in)

For complex libraries with transitive dependencies, `amc` provides an advanced resolution mechanism that ensures all required archives are correctly linked in the Meson build.

To enable these features, add a `features` array to the root of your `build-config.jsonc`:

```jsonc
{
  "project_name": "aemu",
  "features": ["transitive_dependencies"],
  ...
}
```

### Manual Bundling (`extra_targets`)

The `extra_targets` shim allows you to manually specify additional Bazel targets that should be bundled into a single `pkg-config` package. This is useful when a library is composed of multiple internal Bazel targets that don't need their own standalone `.pc` files.

```jsonc
"libuuid": {
  "lib_type": "bazel",
  "bazel_target": "//external/qemu:libuuid",
  "shim": {
    "extra_targets": ["@libuuid//:common"]
  }
}
```
Archives from `extra_targets` are automatically collected and added to the `Libs:` line of the generated `.pc` file.

### Smart Dependency Mapping

When `transitive_dependencies` is enabled, `amc` performs an automated traversal of the Bazel dependency graph and intelligently decides how to handle each dependency:

1.  **Requirement Mapping**: If a dependency target is already defined as a top-level entry in your `build-config.jsonc` (e.g., `zlib`), it is added to the `Requires:` field of the generated `.pc` file. This prevents duplicate symbol linking and respects shared dependencies.
2.  **Internal Bundling**: If a dependency target is NOT found in your configuration, `amc` treats it as an internal implementation detail and **bundles** its static archive directly into the `Libs:` field of the current package.

This automation significantly reduces the need for manual shims and ensures that complex Bazel libraries work "out of the box" within Meson.

### Shimming (Rule Overrides)

`amc` supports overriding or modifying generated Bazel rules using a `shims` configuration. This allows you to customize the build for specific targets without modifying the Meson build files.

Shims can be specified in the `common` section or platform-specific sections of `build-config.jsonc`. They support regex matching on target names and can modify attributes like `srcs`, `deps`, `copts`, etc.

## Commands

`amc` provides several commands to manage the build lifecycle.

| Command     | Description                                                                                              |
| :---------- | :------------------------------------------------------------------------------------------------------- |
| `setup`     | Configures a Meson project. It generates the toolchain and pkg-config files, then runs `meson setup`.    |
| `compile`   | Compiles the project using `meson compile`. Must be run after `setup`.                                   |
| `test`      | Runs the project's test suite using `meson test`.                                                        |
| `release`   | Installs the project artifacts into a release directory and packages them into a `.zip` archive.         |
| `toolchain` | A specialized command that only generates the toolchain and (optionally) the pkg-config files.           |
| `bazel`     | Generates Bazel `BUILD` files from the Meson project, enabling integration into the broader Bazel build. |

## Configuration File (`build-config.jsonc`)

The configuration file has two main sections: `common` for settings shared across all platforms, and `platforms` for platform-specific overrides.

-   `project_name`: The name of the Meson project.
-   `source_path`: The relative path to the project's source code, relative to the repository root.
-   `dependencies`: A dictionary of libraries. Each entry specifies the `lib_type` (e.g., "bazel"), the `bazel_target`, and an optional `shim` object to customize the generated `.pc` file (e.g., to add extra linker flags).
-   `binaries`: A dictionary of executables. Each entry specifies the name of the binary and the `bazel_target` that produces it.
-   `meson_options`: A dictionary of Meson feature flags (e.g., `-Dalsa=enabled`).
-   `generated_files`: A list of files to be generated from templates, such as QEMU's `config-host.mak`.

### Example Dependency

This example defines the `glib` dependency, which is built from the `@glib//glib` Bazel target. The `shim` is used to customize the generated `glib-2.0.pc` file, adding a `Requires` field and a `-pthread` linker flag.

```json
"glib": {
  "lib_type": "bazel",
  "bazel_target": "@glib//glib",
  "version": "2.77.2",
  "shim": {
    "name": "glib-2.0",
    "Requires": "pcre2, gmodule-export-2.0",
    "link_flags": "-pthread"
  }
}
```

### Example Binary

This example defines a binary named `my_generator` that is built from the `//tools:my_generator` Bazel target. Meson can then find this tool using `find_program('my_generator')`.

```json
"binaries": {
  "my_generator": "//tools:my_generator"
}
```

## Example Workflow

Here is a typical workflow for building a Meson project using `amc`.

**1. Configure the project:**

This command reads the configuration file, generates the necessary toolchains and pkg-config files, and sets up the Meson build directory in `out/build`.

```sh
python3 amc.py setup --config path/to/qemu-build-config.jsonc out
```

**2. Compile the source code:**

This invokes `meson compile` in the build directory.

```sh
python3 amc.py compile out
```

**3. Run tests:**

This runs the project's tests using `meson test`.

```sh
python3 amc.py test out
```

**4. Create a release package:**

This installs the build artifacts and packages them into a zip file.

```sh
python3 amc.py release out qemu-release.zip
```

## Using with Other Build Systems (e.g., CMake)

While `amc` is primarily designed for Meson, its toolchain and dependency generation capabilities can be used independently to create a hermetic build environment for other systems like CMake. This is useful when you have a standard CMake project that needs to consume dependencies built by Bazel within the AOSP ecosystem.

The `toolchain` command is key to this workflow. When used with a `--config` file, it generates both the AOSP compiler wrappers and the `pkg-config` (`.pc`) files for all the dependencies listed in the configuration.

**1. Generate the Toolchain and pkg-config Files:**

Run the `toolchain` command, providing your build configuration and an output directory.

```sh
python3 amc.py toolchain --config path/to/your-build-config.jsonc my-build-environment
```

This command creates the following structure:

-   `my-build-environment/toolchain/`: Contains the compiler wrappers (`cc`, `c++`, etc.).
-   `my-build-environment/toolchain/pkgconfig/`: Contains the generated `.pc` files for your Bazel-built dependencies.

**2. Configure the CMake Project:**

To make the CMake project use this generated environment, you must set a few environment variables before running the `cmake` command. These variables point CMake to the correct compilers and tell `pkg-config` where to find the dependency definitions.

```sh
# Set the variables to point to the generated toolchain
export PKG_CONFIG_PATH=$(pwd)/my-build-environment/toolchain/pkgconfig
export CC=$(pwd)/my-build-environment/toolchain/cc
export CXX=$(pwd)/my-build-environment/toolchain/c++

# Now, configure your CMake project as usual
cd path/to/your/cmake/project
cmake .
```

With this environment configured, CMake's `find_package(PkgConfig)` module and `pkg_check_modules()` commands will work seamlessly, discovering and linking against the Bazel dependencies as if they were standard system libraries.
