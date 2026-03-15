cmake_minimum_required(VERSION 3.20)
project(mcp_bridge VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── Max SDK ──────────────────────────────────────────────────────
if(NOT DEFINED MAXSDK_PATH)
    set(MAXSDK_PATH "C:/Program Files/Autodesk/3ds Max 2026 SDK/maxsdk"
        CACHE PATH "Path to 3ds Max SDK")
endif()

if(NOT EXISTS "${MAXSDK_PATH}/include/max.h")
    message(FATAL_ERROR "Cannot find max.h at ${MAXSDK_PATH}/include/max.h")
endif()

# ── Sources ──────────────────────────────────────────────────────
set(SOURCES
    src/dllmain.cpp
    src/bridge_gup.cpp
    src/pipe_server.cpp
    src/command_dispatcher.cpp
    src/main_thread_executor.cpp
    src/handlers/scene_handlers.cpp
    src/handlers/object_handlers.cpp
    src/handlers/modifier_handlers.cpp
    src/handlers/inspect_handlers.cpp
)

add_library(mcp_bridge SHARED ${SOURCES})

# Output as .gup
set_target_properties(mcp_bridge PROPERTIES
    SUFFIX ".gup"
    OUTPUT_NAME "mcp_bridge"
)

target_sources(mcp_bridge PRIVATE src/mcp_bridge.def)

# ── Includes ─────────────────────────────────────────────────────
target_include_directories(mcp_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/third_party"
    "${MAXSDK_PATH}/include"
)

# ── Preprocessor ─────────────────────────────────────────────────
target_compile_definitions(mcp_bridge PRIVATE
    NOMINMAX
    _UNICODE UNICODE
    WIN32 _WIN32
    _USRDLL
    _CRT_SECURE_NO_WARNINGS
    MAX_SILENCE_DEPRECATED_MATRIX_BOOL_CTOR
)

# ── Link ─────────────────────────────────────────────────────────
target_link_directories(mcp_bridge PRIVATE
    "${MAXSDK_PATH}/lib/x64/Release"
)

target_link_libraries(mcp_bridge PRIVATE
    core.lib
    maxutil.lib
    gup.lib
    Paramblk2.lib
    Maxscrpt.lib
    Geom.lib
    comctl32.lib
)

# ── MSVC ─────────────────────────────────────────────────────────
if(MSVC)
    target_compile_options(mcp_bridge PRIVATE /W3 /MP /EHsc)
    set_property(TARGET mcp_bridge PROPERTY MSVC_RUNTIME_LIBRARY
        "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
endif()

# ── Deploy ───────────────────────────────────────────────────────
set(MAX_PLUGINS_DIR "C:/Program Files/Autodesk/3ds Max 2026/plugins"
    CACHE PATH "3ds Max plugins directory")

install(TARGETS mcp_bridge
    RUNTIME DESTINATION "${MAX_PLUGINS_DIR}"
    LIBRARY DESTINATION "${MAX_PLUGINS_DIR}"
)
