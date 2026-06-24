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
            args.add("--compat-lib", compat_file.path)

    env = {}
    path = ctx.configuration.default_shell_env.get("PATH", "")
    if ctx.attr.rustc:
        rustc_file = None
        for f in ctx.files.rustc:
            if f.basename == "rustc" or f.basename == "rustc.exe":
                rustc_file = f
                break
        if rustc_file:
            inputs.extend(ctx.files.rustc)
            rustc_dir = rustc_file.dirname
            if path:
                path = rustc_dir + ctx.configuration.host_path_separator + path
            else:
                path = rustc_dir
    if path:
        env["PATH"] = path

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._gen_toolchain_tool,
        arguments = [args],
        mnemonic = "AmcToolchainGen",
        progress_message = "Generating AMC toolchain for %s" % ctx.attr.target_platform,
        env = env,
        execution_requirements = {
            "no-sandbox": "1",
            "local": "1",
        },
    )

    return [DefaultInfo(files = depset([output_dir]))]

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
    cc_info_archive = archive_dep[CcInfo]

    archives = []
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
    cc_info = ctx.attr.library[CcInfo]
    compilation_context = cc_info.compilation_context
    headers = compilation_context.headers.to_list()

    # Collect all include paths
    includes = []
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

    args = ctx.actions.args()
    args.add("--name", ctx.attr.package_name or ctx.attr.name)
    args.add("--bazel_label", archive_dep.label)
    args.add("--version", ctx.attr.version)
    args.add("--out", output_dir.path)
    if archives:
        args.add_all("--archives", archives)
    if includes:
        args.add_all("--includes", includes)
    if headers:
        args.add_all("--headers", headers)
    for req in ctx.attr.requires:
        args.add("--requires=" + req)
    if ctx.attr.link_flags:
        args.add("--link-flags=" + ctx.attr.link_flags)
    if ctx.attr.cflags:
        args.add("--cflags=" + ctx.attr.cflags)

    # objdump is needed on Linux.
    # TODO fix this by passing in bazel targets.
    env = {}
    if "PATH" in ctx.configuration.default_shell_env:
        env["PATH"] = ctx.configuration.default_shell_env["PATH"]

    inputs = []
    inputs.extend(archives)
    inputs.extend(headers)

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._gen_pkg_config_tool,
        arguments = [args],
        mnemonic = "AmcPkgConfigGen",
        progress_message = "Generating pkg-config for %s" % ctx.attr.name,
        env = env,
        execution_requirements = {
            "no-sandbox": "1",
            "local": "1",
        },
    )

    return [DefaultInfo(files = depset([output_dir]))]

amc_pkg_config = rule(
    implementation = _amc_pkg_config_impl,
    doc = "Generates a pkg-config (.pc) file and persists the library artifacts inside a package directory.",
    attrs = {
        "library": attr.label(
            doc = "The cc_library target to generate the pkg-config for. Headers and include paths are extracted from this.",
            mandatory = True,
            providers = [CcInfo],
        ),
        "archive_library": attr.label(
            doc = "Optional cc_library target to extract archives from, if different from 'library'.",
            providers = [CcInfo],
        ),
        "version": attr.string(
            doc = "The version of the package.",
            mandatory = True,
        ),
        "package_name": attr.string(
            doc = "Optional name of the package. Defaults to the target name.",
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
    toolchain_files = ctx.attr.toolchain[DefaultInfo].files.to_list()
    if not toolchain_files:
        fail("Toolchain target %s must output a directory." % ctx.attr.toolchain)
    toolchain_dir = toolchain_files[0]

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
    for pdir in package_dirs:
        args.add("--prebuilt-packages=" + pdir.path)
    args.add("-v")

    inputs = [
        ctx.file.config,
        toolchain_dir,
    ]
    inputs.extend(package_dirs)
    inputs.extend(ctx.files.srcs)
    if ctx.executable.ninja:
        inputs.append(ctx.executable.ninja)

    is_windows = ctx.target_platform_has_constraint(
        ctx.attr._windows_constraint[platform_common.ConstraintValueInfo],
    )
    env = {
        # The meson backends call ninja directly after checking this var.
        "NINJA": ctx.executable.ninja.path if ctx.executable.ninja else "ninja",
        "MESON_FORCE_BACKTRACE": "1",
    }
    if is_windows:
        env["SystemRoot"] = "C:\\Windows"
        env["SystemDrive"] = "C:"
        env["TEMP"] = "C:\\temp"
        env["TMP"] = "C:\\temp"

        # Needed for python's platform.machine() to work on Windows.
        env["PROCESSOR_ARCHITECTURE"] = "AMD64"
        if "PATH" in ctx.configuration.default_shell_env:
            env["PATH"] = ctx.configuration.default_shell_env["PATH"]

    ctx.actions.run(
        outputs = [output_dir],
        inputs = inputs,
        executable = ctx.executable._amc_tool,
        arguments = [args],
        mnemonic = "AmcGeneratorRun",
        progress_message = "Running AMC generator: %s" % ctx.file.config.short_path,
        env = env,
        execution_requirements = {
            "no-sandbox": "1",
            "local": "1",
        },
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
