"""Bazel rules for Android Meson Configurator (AMC).

These rules allow generating toolchain wrappers and pkg-config files
directly during the Bazel build phase, enabling caching and parallel builds.
"""

load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _amc_toolchain_impl(ctx):
    """Implementation of the amc_toolchain rule.

    This rule executes the gen_toolchain tool to generate compiler/linker wrappers.
    """
    output_dir = ctx.actions.declare_directory(ctx.attr.name)

    args = ctx.actions.args()
    args.add("--aosp", ".")  # AOSP root is the workspace root in execution sandbox
    args.add("--target", ctx.attr.target_platform)
    args.add("--out", output_dir.path)

    inputs = []

    tool_versions_file = ctx.file.tool_versions
    inputs.append(tool_versions_file)
    args.add("--versions", tool_versions_file.path)

    if ctx.attr.ninja:
        ninja_info = ctx.attr.ninja[DefaultInfo]
        if ninja_info.files_to_run and ninja_info.files_to_run.executable:
            ninja_exe = ninja_info.files_to_run.executable
            inputs.append(ninja_exe)
            args.add("--ninja", ninja_exe.path)

    if ctx.attr.pkg_config:
        pkg_config_info = ctx.attr.pkg_config[DefaultInfo]
        if pkg_config_info.files_to_run and pkg_config_info.files_to_run.executable:
            pkg_config_exe = pkg_config_info.files_to_run.executable
            inputs.append(pkg_config_exe)
            args.add("--pkg-config", pkg_config_exe.path)

    if ctx.attr.meson:
        meson_file = None
        for f in ctx.files.meson:
            if f.basename == "meson.py":
                meson_file = f
                break
        if meson_file:
            inputs.extend(ctx.files.meson)
            args.add("--meson", meson_file.path)

    exec_py = ctx.attr._exec_python[_AmcExecPythonInfo]
    if exec_py.interpreter:
        inputs.append(exec_py.interpreter)
        args.add("--python", exec_py.interpreter.path)
    elif exec_py.interpreter_path:
        args.add("--python", exec_py.interpreter_path)
    if exec_py.files:
        inputs.extend(exec_py.files.to_list())

    if ctx.attr.clang:
        clang_file = None
        for f in ctx.files.clang:
            parts = f.path.split("/")
            if len(parts) >= 2 and parts[-2] == "bin" and parts[-1] in ("clang", "clang.exe"):
                clang_file = f
                break
        if clang_file:
            inputs.extend(ctx.files.clang)
            args.add("--clang", clang_file.path)

    if ctx.attr.sysroot and ctx.files.sysroot:
        sysroot_files = ctx.files.sysroot
        inputs.extend(sysroot_files)
        sysroot_dir = None
        for f in sysroot_files:
            parent = f.dirname
            i = parent.find("sysroot")
            if i >= 0:
                sysroot_dir = parent[0:i + 7]
                break
        if not sysroot_dir:
            fail("sysroot target %s must output a directory." % ctx.attr.sysroot)
        args.add("--sysroot", sysroot_dir)

    compat_files = []
    if ctx.attr.compat_lib:
        # We need the archive file (.a) from the compat_lib
        cc_info = ctx.attr.compat_lib[CcInfo]
        static_libs = []
        for li in cc_info.linking_context.linker_inputs.to_list():
            for lib in li.libraries:
                if lib.static_library:
                    static_libs.append(lib.static_library)
                elif lib.pic_static_library:
                    static_libs.append(lib.pic_static_library)
        if static_libs:
            compat_file = static_libs[0]
            inputs.append(compat_file)
            compat_files.append(compat_file)
            args.add("--compat-lib", compat_file.path)

        compat_includes = depset(
            transitive = [
                cc_info.compilation_context.includes,
                cc_info.compilation_context.system_includes,
                cc_info.compilation_context.quote_includes,
                cc_info.compilation_context.external_includes,
            ],
        ).to_list()
        for inc in compat_includes:
            args.add("--compat-include", inc)
        compat_headers = cc_info.compilation_context.headers.to_list()
        inputs.extend(compat_headers)
        compat_files.extend(compat_headers)

    path_entries = []

    if ctx.attr.rustc:
        rustc_file = None
        for f in ctx.files.rustc:
            if f.basename == "rustc" or f.basename == "rustc.exe":
                rustc_file = f
                break
        if rustc_file:
            inputs.extend(ctx.files.rustc)
            path_entries.append(rustc_file.dirname)

    if ctx.attr.cargo:
        cargo_file = None
        for f in ctx.files.cargo:
            if f.basename == "cargo" or f.basename == "cargo.exe":
                cargo_file = f
                break
        if cargo_file:
            inputs.extend(ctx.files.cargo)
            cargo_dir = cargo_file.dirname
            if cargo_dir not in path_entries:
                path_entries.append(cargo_dir)

    path = ctx.configuration.default_shell_env.get("PATH", "")
    if path:
        path_entries.append(path)

    env = {}
    if path_entries:
        env["PATH"] = ctx.configuration.host_path_separator.join(path_entries)

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._gen_toolchain_tool,
        arguments = [args],
        mnemonic = "AmcToolchainGen",
        progress_message = "Generating AMC toolchain for %s" % ctx.attr.target_platform,
        env = env,
    )

    toolchain_files = [output_dir]
    if ctx.attr.clang:
        toolchain_files.extend(ctx.files.clang)
    if ctx.attr.sysroot:
        toolchain_files.extend(ctx.files.sysroot)
    if ctx.attr.meson:
        toolchain_files.extend(ctx.files.meson)
    if ctx.attr.pkg_config:
        pkg_config_info = ctx.attr.pkg_config[DefaultInfo]
        if pkg_config_info.files_to_run and pkg_config_info.files_to_run.executable:
            toolchain_files.append(pkg_config_info.files_to_run.executable)
    if ctx.attr.ninja:
        ninja_info = ctx.attr.ninja[DefaultInfo]
        if ninja_info.files_to_run and ninja_info.files_to_run.executable:
            toolchain_files.append(ninja_info.files_to_run.executable)

    if exec_py.files:
        toolchain_files.extend(exec_py.files.to_list())

    if compat_files:
        toolchain_files.extend(compat_files)

    return [DefaultInfo(files = depset(toolchain_files))]

_AmcExecPythonInfo = provider(
    doc = "Holds the exec platform Python runtime information.",
    fields = ["interpreter", "interpreter_path", "files"],
)

def _amc_exec_python_impl(ctx):
    python_toolchain = ctx.toolchains["@bazel_tools//tools/python:toolchain_type"]
    interpreter = None
    interpreter_path = None
    files = depset()
    if python_toolchain and python_toolchain.py3_runtime:
        py3_runtime = python_toolchain.py3_runtime
        interpreter = py3_runtime.interpreter
        interpreter_path = py3_runtime.interpreter_path
        if py3_runtime.files:
            files = py3_runtime.files
    return [_AmcExecPythonInfo(
        interpreter = interpreter,
        interpreter_path = interpreter_path,
        files = files,
    )]

amc_exec_python = rule(
    implementation = _amc_exec_python_impl,
    doc = "Resolves the Python toolchain in the exec configuration.",
    toolchains = ["@bazel_tools//tools/python:toolchain_type"],
)

amc_toolchain = rule(
    implementation = _amc_toolchain_impl,
    doc = "Generates the compiler/linker wrappers for the given target platform.",
    attrs = {
        "target_platform": attr.string(
            doc = "The target platform for the toolchain (e.g. 'linux-x86_64').",
            mandatory = True,
        ),
        "tool_versions": attr.label(
            doc = "The JSON file containing the compiler and tool versions.",
            allow_single_file = True,
            default = Label("@goldfish_build//toolchains:tool_versions.json"),
        ),
        "compat_lib": attr.label(
            doc = "Optional compatibility library to include in the toolchain.",
            providers = [CcInfo],
        ),
        "rustc": attr.label(
            doc = "Optional rustc compiler target (e.g. @rules_rust//rust/toolchain:current_rustc_files).",
            allow_files = True,
        ),
        "cargo": attr.label(
            doc = "Optional cargo package manager target (e.g. @rules_rust//rust/toolchain:current_cargo_files).",
            allow_files = True,
        ),
        "ninja": attr.label(
            doc = "The ninja executable target.",
            default = Label("@ninja"),
            executable = True,
            cfg = "exec",
        ),
        "pkg_config": attr.label(
            doc = "The pkg-config executable target.",
            default = Label("@pkg-config"),
            executable = True,
            cfg = "exec",
        ),
        "meson": attr.label(
            doc = "The meson target.",
            default = Label("@meson"),
            allow_files = True,
        ),
        "clang": attr.label(
            doc = "The clang target containing compiler and headers.",
            default = Label("//tools/toolchain:clang_files"),
            allow_files = True,
        ),
        "sysroot": attr.label(
            doc = "The sysroot target containing sysroot files.",
            default = Label("//tools/toolchain:gcc_files"),
            allow_files = True,
        ),
        "_exec_python": attr.label(
            default = Label("//tools/toolchain:exec_python"),
            cfg = "exec",
        ),
        "_gen_toolchain_tool": attr.label(
            default = Label("//tools/toolchain:gen_toolchain"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _amc_pkg_config_impl(ctx):
    """Implementation of the amc_pkg_config rule.

    This rule extracts archives, headers, and include paths from a cc_library
    and runs the gen_pkg_config tool to generate a .pc file and persist artifacts.
    """
    output_dir = ctx.actions.declare_directory(ctx.attr.name)

    # Extract archives (supporting both static and shared libraries)
    # Use archive_library if provided (useful when include and archive targets are separate)
    archive_dep = ctx.attr.archive_library if ctx.attr.archive_library else ctx.attr.library
    archives = []
    if archive_dep:
        cc_info_archive = archive_dep[CcInfo]
        for li in cc_info_archive.linking_context.linker_inputs.to_list():
            for lib in li.libraries:
                if lib.static_library:
                    archives.append(lib.static_library)
                elif lib.pic_static_library:
                    archives.append(lib.pic_static_library)
                elif lib.dynamic_library:
                    archives.append(lib.dynamic_library)
                elif lib.interface_library:
                    archives.append(lib.interface_library)

    # Extract headers and include paths
    headers = []
    includes = []
    if ctx.attr.library:
        cc_info = ctx.attr.library[CcInfo]
        compilation_context = cc_info.compilation_context
        headers = compilation_context.headers.to_list()

        # Collect all include paths
        for inc in compilation_context.includes.to_list():
            includes.append(inc)
        for inc in compilation_context.system_includes.to_list():
            includes.append(inc)
        for inc in compilation_context.quote_includes.to_list():
            includes.append(inc)
        for inc in compilation_context.external_includes.to_list():
            includes.append(inc)

        # Deduplicate includes
        includes = depset(includes).to_list()

    data_files = ctx.files.data
    bazel_label = archive_dep.label if archive_dep else ctx.label

    args = ctx.actions.args()
    args.add("--name", ctx.attr.package_name or ctx.attr.name)
    args.add("--bazel_label", bazel_label)
    args.add("--version", ctx.attr.version)
    args.add("--out", output_dir.path)
    if archives:
        args.add_all("--archives", archives)
    if includes:
        args.add_all("--includes", includes)
    if headers:
        args.add_all("--headers", headers)
    if data_files:
        args.add_all("--data", data_files)
    for k, v in ctx.attr.extra_vars.items():
        args.add("--extra-var=" + k + "=" + v)
    for req in ctx.attr.requires:
        args.add("--requires=" + req)
    if ctx.attr.link_flags:
        args.add("--link-flags=" + ctx.attr.link_flags)
    if ctx.attr.cflags:
        args.add("--cflags=" + ctx.attr.cflags)

    inputs = []
    inputs.extend(archives)
    inputs.extend(headers)
    inputs.extend(data_files)

    toolchain_files = ctx.files.toolchain
    inputs.extend(toolchain_files)
    toolchain_dir = None
    for f in toolchain_files:
        if f.is_directory:
            toolchain_dir = f
            break
    if not toolchain_dir:
        fail("Toolchain target %s must output a directory." % ctx.attr.toolchain)

    # Simple host path is still needed for access to bash
    path = ctx.configuration.default_shell_env.get("PATH", "")
    env = {"PATH": toolchain_dir.path + ctx.configuration.host_path_separator + path}

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._gen_pkg_config_tool,
        arguments = [args],
        mnemonic = "AmcPkgConfigGen",
        progress_message = "Generating pkg-config for %s" % ctx.attr.name,
        env = env,
    )

    return [DefaultInfo(files = depset([output_dir]))]

amc_pkg_config = rule(
    implementation = _amc_pkg_config_impl,
    doc = "Generates a pkg-config (.pc) file and persists the library artifacts inside a package directory.",
    attrs = {
        "package_name": attr.string(
            doc = "Name of the package.",
            mandatory = True,
        ),
        "library": attr.label(
            doc = "Optional cc_library target to generate the pkg-config for. Headers and include paths are extracted from this.",
            providers = [CcInfo],
        ),
        "data": attr.label_list(
            doc = "Optional list of data or tool files to persist into the package directory.",
            allow_files = True,
        ),
        "extra_vars": attr.string_dict(
            doc = "Extra variables to include in the .pc file.",
        ),
        "version": attr.string(
            doc = "The version of the package.",
            mandatory = True,
        ),
        "toolchain": attr.label(
            doc = "amc_toolchain target containing the generated wrappers.",
            providers = [DefaultInfo],
            mandatory = True,
        ),
        "archive_library": attr.label(
            doc = "Optional cc_library target to extract archives from, if different from 'library'.",
            providers = [CcInfo],
        ),
        "requires": attr.string_list(
            doc = "List of package dependencies (Requires field in .pc file).",
        ),
        "link_flags": attr.string(
            doc = "Extra link flags to add to the Libs field.",
        ),
        "cflags": attr.string(
            doc = "Extra compiler flags to add to the Cflags field.",
        ),
        "_gen_pkg_config_tool": attr.label(
            default = Label("//tools/toolchain:gen_pkg_config"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _amc_generator_impl(ctx):
    """Implementation of the amc_generator rule.

    This rule runs the gen_bazel tool to configure the Meson project using the
    generated toolchain and packages, and outputs the generated Bazel build files.
    """
    toolchain_files = ctx.files.toolchain
    toolchain_dir = None
    for f in toolchain_files:
        if f.is_directory:
            toolchain_dir = f
            break
    if not toolchain_dir:
        fail("Toolchain target %s must output a directory." % ctx.attr.toolchain)

    # Collect individual package directories
    package_dirs = []
    for pkg in ctx.attr.packages:
        files_list = pkg[DefaultInfo].files.to_list()
        if files_list:
            package_dirs.append(files_list[0])

    output_dir = ctx.actions.declare_directory(ctx.attr.name)

    args = ctx.actions.args()
    args.add("--aosp", ".")  # AOSP root is the workspace root in execution sandbox
    args.add("--target=" + ctx.attr.target_platform)
    args.add("--out=" + output_dir.path)
    args.add("--config=" + ctx.file.config.path)
    args.add("--prebuilt-toolchain=" + toolchain_dir.path)

    # We assume that meson should be run from the source workspace root (this is where it expects meson.build file to be)
    source_root = ctx.attr.srcs[0].label.workspace_root if ctx.attr.srcs else ctx.label.workspace_root
    args.add("--source-path=" + (source_root or "."))
    for pdir in package_dirs:
        args.add("--prebuilt-packages=" + pdir.path)
    args.add("-v")

    inputs = [
        ctx.file.config,
    ]
    inputs.extend(toolchain_files)
    inputs.extend(package_dirs)
    inputs.extend(ctx.files.srcs)
    if ctx.executable.ninja:
        inputs.append(ctx.executable.ninja)

    env = {
        # The meson backends call ninja directly after checking this var.
        # TODO this should be toolchain_dir / "ninja"
        "NINJA": ctx.executable.ninja.path if ctx.executable.ninja else "ninja",
        "MESON_FORCE_BACKTRACE": "1",
    }

    # Even in the sandbox, this basic PATH gives access to common tools like git and chmod
    if "PATH" in ctx.configuration.default_shell_env:
        env["PATH"] = ctx.configuration.default_shell_env["PATH"]

    is_windows = ctx.target_platform_has_constraint(
        ctx.attr._windows_constraint[platform_common.ConstraintValueInfo],
    )
    if is_windows:
        env["SystemRoot"] = "C:\\Windows"
        env["SystemDrive"] = "C:"
        env["TEMP"] = "C:\\temp"
        env["TMP"] = "C:\\temp"

        # Needed for python's platform.machine() to work on Windows.
        env["PROCESSOR_ARCHITECTURE"] = "AMD64"

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._amc_tool,
        arguments = [args],
        mnemonic = "AmcGeneratorRun",
        progress_message = "Running AMC generator: %s" % ctx.file.config.short_path,
        env = env,
    )

    return [DefaultInfo(files = depset([output_dir]))]

amc_generator = rule(
    implementation = _amc_generator_impl,
    doc = "Runs the Android Meson Configurator (AMC) generator to configure a Meson project and generate Bazel build files.",
    attrs = {
        "target_platform": attr.string(
            doc = "The target platform (e.g. 'linux-x64').",
            mandatory = True,
        ),
        "config": attr.label(
            doc = "The build config JSONC file.",
            mandatory = True,
            allow_single_file = True,
        ),
        "toolchain": attr.label(
            doc = "The amc_toolchain target containing the generated wrappers.",
            mandatory = True,
            providers = [DefaultInfo],
        ),
        "packages": attr.label_list(
            doc = "List of amc_pkg_config targets representing the prebuilt packages.",
            default = [],
        ),
        "srcs": attr.label_list(
            doc = "The source files of the Meson project.",
            default = [],
            allow_files = True,
        ),
        "ninja": attr.label(
            doc = "The ninja executable target.",
            default = Label("@ninja"),
            executable = True,
            cfg = "exec",
        ),
        "_amc_tool": attr.label(
            default = Label("//tools/toolchain:gen_bazel"),
            executable = True,
            cfg = "exec",
        ),
        "_windows_constraint": attr.label(
            default = "@platforms//os:windows",
        ),
    },
)
