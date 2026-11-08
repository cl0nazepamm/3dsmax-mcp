# Writes the _mx build tag header: cmake -DOUT=<header> [-DTAG=<tag>] -P mx_build_tag.cmake
#
# The tag names the inline namespace that holds _mx's binding types. nanobind
# keys registered types by their C++ type, so a rebuilt module loaded beside an
# older one in the same 3ds Max session would otherwise be handed the older
# module's classes. A new tag per build gives every build its own types.
# Without TAG (dev reload builds) the tag is the current time.
if(NOT DEFINED TAG OR TAG STREQUAL "")
    string(TIMESTAMP TAG "b%Y%m%d%H%M%S")
endif()
set(text "#pragma once\n#define MX_ABI_TAG ${TAG}\n#define MX_BUILD_ID \"${TAG}\"\n")
if(EXISTS "${OUT}")
    file(READ "${OUT}" previous)
    if(previous STREQUAL text)
        return()
    endif()
endif()
file(WRITE "${OUT}" "${text}")
