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

"""Build definitions and macros for Goldfish Protobuf and gRPC services."""

load("@grpc//bazel:cc_grpc_library.bzl", "cc_grpc_library")
load("@grpc//bazel:python_rules.bzl", "py_proto_library")
load("@grpc-java//:java_grpc_library.bzl", "java_grpc_library")
load("@protobuf//bazel:cc_proto_library.bzl", "cc_proto_library")
load("@protobuf//bazel:java_proto_library.bzl", "java_proto_library")
load("@protobuf//bazel:proto_library.bzl", "proto_library")
load("@rules_cc//cc:defs.bzl", "cc_library")
load("@rules_java//java:defs.bzl", "java_library")

def aemu_service_v2(
        name,
        srcs = [],
        deps = [],
        has_grpc = True,
        generate_mocks = True,
        visibility = ["//visibility:public"]):
    """Generates proto, cc, java, and python library targets for a service.

    Variants generated:
      - <name>_proto (proto_library)
      - <name>_cc_proto (cc_proto_library)
      - <name>_cc_grpc (cc_grpc_library, if has_grpc=True)
      - <name>_java_proto (java_proto_library)
      - <name>_java_grpc (java_grpc_library, if has_grpc=True)
      - <name>_py_proto (py_proto_library)

    Args:
      name: Base name for generated target family.
      srcs: List of .proto source files.
      deps: List of proto_library dependencies.
      has_grpc: Whether to generate cc_grpc_library and java_grpc_library.
      generate_mocks: Whether to generate gRPC test mocks in cc_grpc_library.
      visibility: Visibility of generated targets.
    """
    proto_name = name + "_proto"
    cc_proto_name = name + "_cc_proto"
    cc_grpc_name = name + "_cc_grpc"
    java_proto_name = name + "_java_proto"
    java_grpc_name = name + "_java_grpc"
    py_proto_name = name + "_py_proto"

    proto_library(
        name = proto_name,
        srcs = srcs,
        strip_import_prefix = "/protos/services_v2",
        deps = deps,
        visibility = visibility,
    )

    cc_proto_library(
        name = cc_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )

    if has_grpc:
        cc_grpc_library(
            name = cc_grpc_name,
            srcs = [":" + proto_name],
            generate_mocks = generate_mocks,
            grpc_only = True,
            deps = [":" + cc_proto_name],
            visibility = visibility,
        )

    java_proto_library(
        name = java_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )

    if has_grpc:
        java_grpc_library(
            name = java_grpc_name,
            srcs = [":" + proto_name],
            deps = [":" + java_proto_name],
            visibility = visibility,
        )

    py_proto_library(
        name = py_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )

def aemu_service_group_v2(
        name,
        proto_deps = [],
        grpc_cc_deps = [],
        grpc_java_deps = [],
        visibility = ["//visibility:public"]):
    """Aggregates multiple services into grouped targets.

    Variants generated:
      - <name>_proto (proto_library)
      - <name>_cc_proto (cc_proto_library)
      - <name>_cc_grpc (cc_library aggregating grpc cc targets)
      - <name>_java_proto (java_proto_library)
      - <name>_java_grpc (java_library exporting grpc java targets)
      - <name>_py_proto (py_proto_library)

    Args:
      name: Base name for generated target family.
      proto_deps: List of proto_library targets to aggregate.
      grpc_cc_deps: List of cc_grpc targets to aggregate.
      grpc_java_deps: List of java_grpc targets to aggregate.
      visibility: Visibility of generated targets.
    """
    proto_name = name + "_proto"
    cc_proto_name = name + "_cc_proto"
    cc_grpc_name = name + "_cc_grpc"
    java_proto_name = name + "_java_proto"
    java_grpc_name = name + "_java_grpc"
    py_proto_name = name + "_py_proto"

    proto_library(
        name = proto_name,
        deps = proto_deps,
        visibility = visibility,
    )

    cc_proto_library(
        name = cc_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )

    if grpc_cc_deps:
        cc_library(
            name = cc_grpc_name,
            deps = grpc_cc_deps,
            visibility = visibility,
        )

    java_proto_library(
        name = java_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )

    if grpc_java_deps:
        java_library(
            name = java_grpc_name,
            exports = grpc_java_deps,
            visibility = visibility,
        )

    py_proto_library(
        name = py_proto_name,
        deps = [":" + proto_name],
        visibility = visibility,
    )
