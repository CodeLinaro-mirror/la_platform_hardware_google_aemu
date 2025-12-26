# Meson to Bazel Conversion Example

This directory contains a sample Meson-based project that demonstrates how to use the Android Meson Configurator (`amc`) to generate Bazel `BUILD` files. The project is a simple "Hello, World!" application that uses `glib` and a custom code generation step.

## Project Structure

-   `sample.cpp`: The main C++ source file.
-   `meson.build`: The Meson build script for the project.
-   `py_gen.py`: A Python script used as a custom code generator.
-   `example-build-config.jsonc`: The `amc` configuration file that defines dependencies, binaries, and other build settings.
-   `example-shim.jsonc`: A configuration file that provides shims for Bazel dependencies.
-   `meson_options.txt`: Defines the build options for the Meson project.

## How to Generate Bazel Build Files

To convert this Meson project into a set of Bazel `BUILD` files, you will use the `amc bazel` command. This command reads the Meson project and the `amc` configuration files to generate a Bazel-compatible build structure.

### Command

From the root of your workspace, run the following command:

```sh
bazel run @aemu//tools/toolchain:amc -- bazel --config <workspace>/hardware/google/aemu/tools/toolchain/example/example-build-config.jsonc --shim <workspace>/hardware/google/aemu/tools/toolchain/example/example-shim.jsonc <output_directory>
```

### Command Breakdown

-   `bazel run @aemu//tools/toolchain:amc --`: This executes the `amc` tool using Bazel.

-   `bazel`: This is the subcommand for `amc` that tells it to generate Bazel build files.

-   `--config <path/to/example-build-config.jsonc>`: This flag specifies the path to the main build configuration file. This file tells `amc` about the project's dependencies, binaries, and Meson options.

-   `--shim <path/to/example-shim.jsonc>`: This flag provides the path to the shim configuration file. This file is used to map `pkg-config` dependencies to their corresponding Bazel targets.

-   `<output_directory>`: This is the directory where the generated Bazel files will be placed. For example, you can use `/tmp/bazel`.

### Example

```sh
bazel run @aemu//tools/toolchain:amc -- bazel --config hardware/google/aemu/tools/toolchain/example/example-build-config.jsonc --shim hardware/google/aemu/tools/toolchain/example/example-shim.jsonc /tmp/bazel
```

### What Happens

When you run this command, `amc` will:

1.  Parse the `meson.build` file to understand the project structure.
2.  Read the `example-build-config.jsonc` and `example-shim.jsonc` files to resolve dependencies and other configurations.
3.  Generate a `BUILD.bazel` file (and any other necessary files) in the specified output directory.

This generated `BUILD.bazel` file will contain the necessary rules to build the `hello` binary, including the custom code generation step, all within the Bazel ecosystem.
