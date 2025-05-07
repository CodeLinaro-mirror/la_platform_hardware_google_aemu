# TODO(gregschlom) move this to base

if(NOT DEFINED LOGGING_LIB_NAME)
   set(LOGGING_LIB_NAME logging-base)
endif()

add_library(${LOGGING_LIB_NAME}
    logging.cpp
    GfxstreamFatalError.cpp
    ../base/Metrics.cpp)
target_include_directories(
    ${LOGGING_LIB_NAME}
    PRIVATE
    include/host-common)
target_link_libraries(
    ${LOGGING_LIB_NAME}
    PUBLIC
    PRIVATE
    aemu-base.headers
    aemu-host-common.headers)

if (BUILD_SHARED_LIBS)
    set_target_properties(
        ${LOGGING_LIB_NAME}
        PROPERTIES
        VERSION ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}
        SOVERSION ${VERSION_MAJOR})
endif()

add_library(aemu-host-common.headers INTERFACE)
target_link_libraries(
    aemu-host-common.headers
    INTERFACE
    aemu-base.headers)
target_include_directories(
    aemu-host-common.headers
    INTERFACE
    include)

if (MSVC)
    target_link_libraries(
        aemu-host-common.headers
        INTERFACE
        msvc-posix-compat)
endif()
if (BUILD_STANDALONE)
    add_library(
        aemu-host-common

        # emugl glue
        empty-crash-handler.cpp
        crash_reporter.cpp
        feature_control.cpp
        window_operations.cpp

        # What used to be android-emu
        AndroidPipe.cpp
        HostmemIdMapping.cpp
        RefcountPipe.cpp
        GraphicsAgentFactory.cpp

	# SubAllocator
        ../base/SubAllocator.cpp

        hw-config.cpp
        )

    if (BUILD_SHARED_LIBS)
        set_target_properties(
            aemu-host-common
            PROPERTIES
            VERSION ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}
            SOVERSION ${VERSION_MAJOR})
    endif()

    target_include_directories(
        aemu-host-common
        PRIVATE
        include/host-common
        ${AEMU_COMMON_REPO_ROOT}/../../../external/angle/include)
    target_link_libraries(
        aemu-host-common
        PUBLIC
        aemu-base.headers
        aemu-host-common.headers
        ${GFXSTREAM_BASE_LIB}
        PRIVATE
        ${LOGGING_LIB_NAME}
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

if (GFXSTREAM_HOST_COMMON_LIB)
    target_link_libraries(
        ${GFXSTREAM_HOST_COMMON_LIB}
        PRIVATE
        aemu-host-common.product-feature-override)
endif()

if (ENABLE_VKCEREAL_TESTS)
    add_executable(
        aemu-host-common_unittests
        logging_unittest.cpp
        GfxstreamFatalError_unittest.cpp)

    target_include_directories(
        aemu-host-common_unittests
        PRIVATE
        ${AEMU_COMMON_REPO_ROOT})

    target_link_libraries(
        aemu-host-common_unittests
        PRIVATE
        aemu-base.headers
        aemu-host-common.headers
        ${GFXSTREAM_BASE_LIB}
        ${GFXSTREAM_HOST_COMMON_LIB}
        gtest_main
        gmock_main)

    gtest_discover_tests(aemu-host-common_unittests)
endif()
