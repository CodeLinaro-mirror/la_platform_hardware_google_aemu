set(AEMU_COMMON_REPO_ROOT ${CMAKE_CURRENT_SOURCE_DIR})

add_subdirectory(base)
add_subdirectory(snapshot)
add_subdirectory(host-common)

add_library(aemu_common INTERFACE)
target_link_libraries(
    aemu_common
    INTERFACE
    gfxstream-base
    gfxstream-base.headers
    gfxstream-host-common
    gfxstream-host-common.headers)
