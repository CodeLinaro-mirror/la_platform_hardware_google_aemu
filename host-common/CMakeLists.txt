if (APPLE)
    set(aemu-host-common-opengles-platform-sources
        opengl/macTouchOpengl.m
        opengl/NativeGpuInfo_darwin.cpp)
elseif (WIN32)
    set(aemu-host-common-opengles-platform-sources
        opengl/NativeGpuInfo_windows.cpp)
else()
    set(aemu-host-common-opengles-platform-sources
        opengl/NativeGpuInfo_linux.cpp)
endif()

# TODO(gregschlom) move this to base
add_library(logging-base
    STATIC
    logging.cpp
    GfxstreamFatalError.cpp
    ../base/Metrics.cpp)
target_include_directories(
    logging-base
    PRIVATE
    include/host-common
    ${GFXSTREAM_REPO_ROOT}/include)
target_link_libraries(
    logging-base
    PUBLIC
    PRIVATE
    aemu-base.headers
    aemu-host-common.headers)

add_library(aemu-host-common.headers INTERFACE)
target_link_libraries(
    aemu-host-common.headers
    INTERFACE
    gfxstream-snapshot.headers)
target_include_directories(
    aemu-host-common.headers
    INTERFACE
    include
    ${GFXSTREAM_REPO_ROOT}/include)

if (BUILD_STANDALONE)
    add_library(
        aemu-host-common
        STATIC

        # emugl glue
        crash_reporter.cpp
        vm_operations.cpp
        feature_control.cpp
        dma_device.cpp
        sync_device.cpp
        misc.cpp
        window_operations.cpp

        # What used to be android-emu
        AndroidPipe.cpp
        HostmemIdMapping.cpp
        RefcountPipe.cpp
        GraphicsAgentFactory.cpp

        # goldfish sync
        GoldfishSyncCommandQueue.cpp
        goldfish_sync.cpp

        # goldfish dma
        DmaMap.cpp
        GoldfishDma.cpp

        # Address space device
        address_space_device_control_ops.cpp
        address_space_device.cpp
        address_space_host_memory_allocator.cpp
        address_space_shared_slots_host_memory_allocator.cpp
        address_space_graphics.cpp
        address_space_host_media.cpp

        hw-config.cpp

        # general opengles host stuff, incl process pipe
        # and opengl es pipe
        opengles.cpp
        opengl/EmuglBackendList.cpp
        # opengl/EmuglBackendList_unittest.cpp
        opengl/emugl_config.cpp
        # opengl/emugl_config_unittest.cpp
        opengl/GLProcessPipe.cpp
        opengl/GpuFrameBridge.cpp
        # opengl/GpuFrameBridge_unittest.cpp
        opengl/gpuinfo.cpp
        # opengl/gpuinfo_unittest.cpp
        opengl/logger.cpp
        opengl/OpenglEsPipe.cpp
        ${aemu-host-common-opengles-platform-sources}
        )

    target_include_directories(
        aemu-host-common
        PRIVATE
        include/host-common
        include/host-common/opengl
        ${GFXSTREAM_REPO_ROOT}/include
        ${GFXSTREAM_REPO_ROOT}/third-party/angle/include)
    target_link_libraries(
        aemu-host-common
        PUBLIC
        aemu-base.headers
        aemu-host-common.headers
        ${GFXSTREAM_BASE_LIB}
        PRIVATE
        logging-base
    )
endif()

if(NOT TARGET aemu-host-common.product-feature-override)
    add_library(aemu-host-common.product-feature-override OBJECT FeatureControlOverride.cpp)
    target_link_libraries(
        aemu-host-common.product-feature-override
        PRIVATE
        aemu-host-common.headers
        aemu-base.headers)
endif()

target_link_libraries(
    ${GFXSTREAM_HOST_COMMON_LIB}
    PRIVATE
    aemu-host-common.product-feature-override)

# Tests
add_library(
    aemu-host-common-testing-support
    testing/HostAddressSpace.cpp
    testing/MockGraphicsAgentFactory.cpp
    testing/MockAndroidEmulatorWindowAgent.cpp
    testing/MockAndroidMultiDisplayAgent.cpp
    testing/MockAndroidVmOperations.cpp)
target_include_directories(
    aemu-host-common-testing-support
    PUBLIC
    ${AEMU_COMMON_REPO_ROOT})
target_link_libraries(
    aemu-host-common-testing-support
    PUBLIC
    PRIVATE
    aemu-base.headers
    aemu-host-common.headers
    gtest
    gmock)

if (ENABLE_VKCEREAL_TESTS)
    add_executable(
        aemu-host-common_unittests
        address_space_graphics_unittests.cpp
        address_space_host_memory_allocator_unittests.cpp
        address_space_shared_slots_host_memory_allocator_unittests.cpp
        HostAddressSpace_unittest.cpp
        HostmemIdMapping_unittest.cpp
        logging_unittest.cpp
        GfxstreamFatalError_unittest.cpp)

    target_include_directories(
        aemu-host-common_unittests
        PRIVATE
        ${AEMU_COMMON_REPO_ROOT}
        ${GFXSTREAM_REPO_ROOT}/include)

    target_link_libraries(
        aemu-host-common_unittests
        PRIVATE
        aemu-base.headers
        aemu-host-common.headers
        ${GFXSTREAM_BASE_LIB}
        ${GFXSTREAM_HOST_COMMON_LIB}
        aemu-host-common-testing-support
        gtest_main
        gmock_main)

    gtest_discover_tests(aemu-host-common_unittests)
endif()
