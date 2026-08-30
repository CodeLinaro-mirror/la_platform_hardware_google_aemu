# Copyright (C) 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Starlark rules for compiling Protocol Buffers to TypeScript for gRPC-Web."""

load("@rules_proto//proto:defs.bzl", "ProtoInfo")

def _ts_proto_library_impl(ctx):
    proto_info = ctx.attr.proto[ProtoInfo] if ctx.attr.proto else None
    if not proto_info:
        for dep in ctx.attr.deps:
            if ProtoInfo in dep:
                proto_info = dep[ProtoInfo]
                break

    if not proto_info:
        fail("ts_proto_library %s requires a proto_library target via 'proto' or 'deps'" % ctx.label)

    out_files = []
    for src in proto_info.direct_sources:
        if not src.basename.endswith(".proto"):
            continue
        base = src.basename[:-len(".proto")]
        out = ctx.actions.declare_file(base + ".ts")
        out_files.append(out)

    if not out_files:
        return [DefaultInfo(files = depset())]

    all_inputs = proto_info.transitive_sources

    # Resolve node from hermetic toolchain
    node_toolchain = ctx.toolchains["@rules_nodejs//nodejs:toolchain_type"]
    node_tool_path = node_toolchain.nodeinfo.target_tool_path
    node_files = node_toolchain.nodeinfo.tool_files

    tools_list = [ctx.executable.protoc, ctx.executable._ts_proto_plugin]
    if hasattr(node_files, "to_list"):
        tools_list.extend(node_files.to_list())
    elif type(node_files) == "list":
        tools_list.extend(node_files)
    all_tools = depset(tools_list)

    # Output directory for ts-proto
    # Package is e.g. "protos/services_v2/media"
    package_dir = out_files[0].dirname
    package_name = ctx.label.package.split("/")[-1]
    if ctx.label.package != "protos/services_v2" and package_dir.endswith("/" + package_name):
        out_dir = package_dir[:-len("/" + package_name)]
    else:
        out_dir = package_dir

    ts_proto_opt = [
        "outputServices=generic-definitions",
        "outputServices=default",
        "returnObservable=false",
        "useAsyncIterable=true",
        "env=browser",
        "esModuleInterop=true",
        "useOptionals=messages",
        "exportCommonAnnotations=true",
    ]

    # Build protoc arguments
    args = ctx.actions.args()
    args.add("--plugin=protoc-gen-ts_proto=" + ctx.executable._ts_proto_plugin.path)
    args.add("--ts_proto_out=" + out_dir)
    args.add("--ts_proto_opt=" + ",".join(ts_proto_opt))

    # Transitive include paths from ProtoInfo
    for p in proto_info.transitive_proto_path.to_list():
        args.add("-I" + p)
    args.add("-I.")

    # Direct source proto files
    for src in proto_info.direct_sources:
        args.add(src.path)

    ctx.actions.run(
        inputs = all_inputs,
        tools = all_tools,
        outputs = out_files,
        mnemonic = "TsProtoGen",
        executable = ctx.executable.protoc,
        arguments = [args],
        env = {
            "NODE_BINARY": node_tool_path,
        },
        progress_message = "Generating TypeScript proto %s" % ctx.label,
    )

    return [
        DefaultInfo(files = depset(out_files)),
    ]

ts_proto_library = rule(
    implementation = _ts_proto_library_impl,
    attrs = {
        "proto": attr.label(
            providers = [ProtoInfo],
            doc = "The proto_library target to compile.",
        ),
        "deps": attr.label_list(
            doc = "Dependencies on other proto libraries.",
        ),
        "has_grpc": attr.bool(
            default = True,
            doc = "Whether to generate gRPC-Web service definitions.",
        ),
        "protoc": attr.label(
            default = "@protobuf//:protoc",
            executable = True,
            cfg = "exec",
            doc = "The protoc compiler binary.",
        ),
        "_ts_proto_plugin": attr.label(
            default = "//protos/services_v2/tools:protoc_gen_ts_proto",
            executable = True,
            cfg = "exec",
        ),
    },
    toolchains = ["@rules_nodejs//nodejs:toolchain_type"],
)

def _ts_proto_group_impl(ctx):
    transitive = []
    for dep in ctx.attr.deps:
        transitive.append(dep[DefaultInfo].files)
    return [
        DefaultInfo(files = depset(transitive = transitive)),
    ]

ts_proto_group = rule(
    implementation = _ts_proto_group_impl,
    attrs = {
        "deps": attr.label_list(
            doc = "List of ts_proto_library or ts_proto_group targets.",
        ),
    },
)
