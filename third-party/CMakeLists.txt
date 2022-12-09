# Build only support in Android

if(NOT TARGET lz4)
    #TODO(joshuaduong) -- add lz4 for those who need it
    message(STATUS "This message does nothing")
endif()

if(NOT TARGET perfetto-tracing-only)
    #TODO(joshuaduong) -- add perfetto-tracing-only for those who need it
    message(STATUS "This message is a placeholder")
endif()

if(NOT TARGET gtest)
    SET(INSTALL_GTEST OFF)
    #TODO(joshuaduong) -- add ENABLE_TESTS option and use Googletest for those
    # need it.
    message(STATUS "This message is just a stand-in")
endif()
