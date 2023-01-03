# Build only support in Android

if(AEMU_BASE_USE_LZ4 AND NOT TARGET lz4_static)
    message(FATAL_ERROR "lz4 is not provided.")
endif()

if(AEMU_COMMON_USE_PERFETTO AND NOT TARGET perfetto-tracing-only)
    message(FATAL_ERROR "perfetto-tracing-only is not provided.")
endif()

if(ENABLE_VKCEREAL_TESTS AND NOT TARGET gtest)
    set(INSTALL_GTEST OFF)
    message(FATAL_ERROR "googletest is not provided.")
endif()
