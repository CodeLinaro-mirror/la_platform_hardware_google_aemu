load("@rules_cc//cc:defs.bzl", "cc_binary", "cc_library", "cc_test")

package(default_applicable_licenses = ["//:license"])

cc_library(
    name = "compat-hdrs",
    hdrs = glob(["includes/**/*.h"]),
    # bits/socket.h, msvc-posix.h, strings.h, sys/cdefs.h, sys/stat.h,
    # sys/time.h, sys/types.h, time.h and unistd.h are GPL-2.0.
    applicable_licenses = [
        "//:license",
        "//:license_gpl2",
    ],
    defines = ["AEMU_WIN_COMPAT"],
    includes = [
        "includes",
        "includes/dirent",
    ],
    target_compatible_with = [
        "@platforms//os:windows",
    ],
    visibility = ["//visibility:public"],
)

cc_library(
    name = "compat",
    srcs = [
        "src/dirent/dirent.cpp",
    ] + glob([
        "src/*.c",
        "src/*.h",
        "src/*.cpp",
    ]),
    # GPL-2.0: msvc-posix.c and pread.cpp. BSD-2-Clause and ISC: getopt.c.
    applicable_licenses = [
        "//:license",
        "//:license_bsd2",
        "//:license_gpl2",
        "//:license_isc",
    ],
    defines = [
        "WIN32_LEAN_AND_MEAN",
    ],
    includes = [
        "src",
    ],
    linkopts = [
        "-DEFAULTLIB:ws2_32.lib",
        "-DEFAULTLIB:Pathcch.lib",
        "-DEFAULTLIB:ole32.lib",
        "-DEFAULTLIB:dxguid.lib",
        "-DEFAULTLIB:Winmm.lib",
    ],
    linkstatic = True,
    target_compatible_with = [
        "@platforms//os:windows",
    ],
    visibility = ["//visibility:public"],
    deps = [":compat-hdrs"],
)

cc_test(
    name = "dirent_test",
    srcs = [
        "tests/dirent_test.cpp",
    ],
    target_compatible_with = [
        "@platforms//os:windows",
    ],
    deps = [
        ":compat",
        "@googletest//:gtest_main",
    ],
)

cc_binary(
    name = "proc-killer",
    srcs = ["src/proc-killer.cpp"],
    linkopts = [
        "/SUBSYSTEM:CONSOLE",
        "/ENTRY:wmainCRTStartup",
    ],
    target_compatible_with = [
        "@platforms//os:windows",
    ],
    visibility = ["//visibility:public"],
)
