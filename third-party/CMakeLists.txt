# Build only support in Android

# TODO(joshuaduong): Should copy/move these third-party dependencies from vulkan-cereal to here.
set(VULKAN_CEREAL_PATH ${PROJECT_SOURCE_DIR}/../../../device/generic/vulkan-cereal)
if(NOT TARGET lz4)
    if(EXISTS ${VULKAN_CEREAL_PATH})
        add_subdirectory(${VULKAN_CEREAL_PATH}/third-party/lz4 lz4)
    endif()
endif()

if(NOT TARGET perfetto-tracing-only)
    if(EXISTS ${VULKAN_CEREAL_PATH})
        add_subdirectory(${VULKAN_CEREAL_PATH}/third-party/perfetto-tracing-only
                         perfetto-tracing-only)
    endif()
endif()

if(NOT TARGET gtest)
    if(EXISTS ${VULKAN_CEREAL_PATH})
        SET(INSTALL_GTEST OFF)
        add_subdirectory(${VULKAN_CEREAL_PATH}/third-party/googletest
                         googletest)
    endif()
endif()
