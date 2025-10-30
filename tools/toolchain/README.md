# Android Meson Configurator (amc)

The Android Meson Configurator (`amc`) is a Python-based command-line tool designed to simplify the configuration and compilation of Meson-based projects within the Android Open Source Project (AOSP) build environment. It automates the setup of toolchains and dependencies, bridging the gap between the Meson build system and the Bazel-based dependency management used in AOSP.

## Core Concepts

`amc` works by reading a central configuration file and performing a series of setup steps before invoking Meson.

### 1. Build Configuration File

The tool is driven by a `JSONC` (JSON with Comments) file, typically named `build-config.jsonc`. This file defines all the necessary parameters for the build, including:

-   **Dependencies**: A list of libraries required by the project.
-   **Meson Options**: Feature flags and settings passed to the `meson setup` command.
-   **Generated Files**: Any additional configuration files that need to be created for the build.
-   **Platform-Specific Settings**: Overrides and additions for different host platforms (e.g., `linux-x64`, `mac-aarch64`, `windows-x64`).

### 2. Toolchain Generation

`amc` generates a set of wrapper scripts for the compiler, linker, and other toolchain utilities (e.g., `cc`, `c++`, `ar`, `nm`). These wrappers ensure that Meson uses the correct Clang toolchain and sysroots that are provided within the AOSP source tree, along with the necessary flags for the target platform.

### 3. Dependency Management via pkg-config

Meson relies on the `pkg-config` utility to discover libraries and their required compiler/linker flags. Since many dependencies in the AOSP environment are built with Bazel, `amc` bridges this gap by:

1.  Reading the `dependencies` section of the configuration file.
2.  Querying Bazel to get the locations of required library archives (`.a`, `.lib`) and header files.
3.  Generating `.pc` (pkg-config) files that point to these Bazel-built artifacts.

This allows Meson to seamlessly find and link against dependencies without needing to know that they were built by Bazel.

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
