load("@rules_cc//cc:defs.bzl", "cc_library", "cc_test")

cc_library(
    name = "compat-hdrs",
    hdrs = glob(["includes/**/*.h"]),
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
        "@com_google_googletest//:gtest_main",
    ],
)
