set(AEMU_COMMON_REPO_ROOT ${CMAKE_CURRENT_SOURCE_DIR})

# Set AEMU_BUILD_CONFIG_NAME to use a custom cmake build script located in
# build-config/$AEMU_BUILD_CONFIG_NAME. If AEMU_BUILD_CONFIG_NAME is unset,
# it will default to building everything. See build-config/gfxstream/CMakeLists.txt
# for an example.
if (NOT ${AEMU_COMMON_BUILD_CONFIG} STREQUAL "")
    if (NOT EXISTS "${AEMU_COMMON_REPO_ROOT}/build-config/${AEMU_COMMON_BUILD_CONFIG}")
        message(FATAL_ERROR "build-config/${AEMU_COMMON_BUILD_CONFIG} does not exist")
    endif()
    message(STATUS "aemu-common: Using custom build script in \
${AEMU_COMMON_REPO_ROOT}/build-config/${AEMU_COMMON_BUILD_CONFIG}")
    add_subdirectory(build-config/${AEMU_COMMON_BUILD_CONFIG})
endif()

add_subdirectory(base)
add_subdirectory(snapshot)
add_subdirectory(host-common)

add_library(aemu_common INTERFACE)
target_link_libraries(
    aemu_common
    INTERFACE
    aemu-base
    aemu-base.headers
    aemu-host-common
    aemu-host-common.headers)
