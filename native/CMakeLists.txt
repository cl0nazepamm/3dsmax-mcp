cmake_minimum_required(VERSION 3.20)

# Single source of truth for the package version: pyproject.toml. CMake parses
# it so the WinHTTP user agent and any other version-stamped strings stay in
# sync without a second bump site.
set(MCP_BRIDGE_VERSION "0.0.0")
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml")
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml" _pyproject)
    string(REGEX MATCH "version[ \t]*=[ \t]*\"([^\"]+)\"" _ "${_pyproject}")
    if(CMAKE_MATCH_1)
        set(MCP_BRIDGE_VERSION "${CMAKE_MATCH_1}")
    endif()
endif()
message(STATUS "mcp_bridge version: ${MCP_BRIDGE_VERSION}")

project(mcp_bridge VERSION ${MCP_BRIDGE_VERSION} LANGUAGES CXX)

# 3ds Max 2027 SDK requires C++20; earlier versions use C++17
if(MAX_VERSION GREATER_EQUAL 2027)
    set(CMAKE_CXX_STANDARD 20)
else()
    set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── Max version ──────────────────────────────────────────────────
set(MAX_VERSION "2026" CACHE STRING "Target 3ds Max version (2024, 2025, 2026, 2027)")
message(STATUS "Building for 3ds Max ${MAX_VERSION}")

# Compiler requirements per version:
#   2024/2025/2026 → Visual Studio 2022 (v143), C++17
#   2027+          → Visual Studio 2026 (v144), C++20
if(MAX_VERSION GREATER_EQUAL 2027)
    message(STATUS "Max 2027+ SDK — C++20 enabled (VS 2022 works, VS 2026 recommended by Autodesk)")
endif()

# ── Max SDK ──────────────────────────────────────────────────────
if(NOT DEFINED MAXSDK_PATH)
    set(MAXSDK_PATH "C:/Program Files/Autodesk/3ds Max ${MAX_VERSION} SDK/maxsdk"
        CACHE PATH "Path to 3ds Max SDK")
endif()

if(NOT EXISTS "${MAXSDK_PATH}/include/max.h")
    message(FATAL_ERROR "Cannot find max.h at ${MAXSDK_PATH}/include/max.h")
endif()

# ── Generated chat tool registry ─────────────────────────────────
# scripts/gen_tool_registry.py walks src/tools/*.py and emits
# native/generated/chat_tool_registry.inc — the chat tool surface for v0.7.0.
# If Python or the script is unavailable, llm_client.cpp falls back to a
# hand-coded registry so chat still works.
set(TOOL_REGISTRY_INC "${CMAKE_CURRENT_SOURCE_DIR}/generated/chat_tool_registry.inc")
find_package(Python3 QUIET COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
    file(GLOB_RECURSE PY_TOOL_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/../src/tools/*.py")
    add_custom_command(
        OUTPUT "${TOOL_REGISTRY_INC}"
        COMMAND ${Python3_EXECUTABLE}
                "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_registry.py"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_registry.py"
                ${PY_TOOL_SOURCES}
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/.."
        COMMENT "Generating chat_tool_registry.inc from src/tools/*.py"
        VERBATIM
    )
    add_custom_target(gen_tool_registry DEPENDS "${TOOL_REGISTRY_INC}")
else()
    message(WARNING "Python3 not found — chat tool registry will use hand-coded fallback in llm_client.cpp")
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
    src/handlers/orientation_handlers.cpp
    src/handlers/modifier_handlers.cpp
    src/handlers/inspect_handlers.cpp
    src/handlers/scene_manage_handlers.cpp
    src/handlers/file_handlers.cpp
    src/handlers/viewport_handlers.cpp
    src/handlers/material_handlers.cpp
    src/handlers/controller_handlers.cpp
    src/handlers/plugin_introspect_handlers.cpp
    src/handlers/organize_handlers.cpp
    src/handlers/learning_handlers.cpp
    src/handlers/effects_handlers.cpp
    src/handlers/render_handlers.cpp
    src/handlers/state_sets_handlers.cpp
    src/handlers/discovery_handlers.cpp
    src/handlers/chat_handlers.cpp
    src/chat_ui.cpp
    src/llm_client.cpp
)

add_library(mcp_bridge SHARED ${SOURCES})

# llm_client.cpp #include's generated/chat_tool_registry.inc. Make the build
# wait for the generator (when Python is available).
if(Python3_Interpreter_FOUND)
    add_dependencies(mcp_bridge gen_tool_registry)
endif()

# Output as .gup
set_target_properties(mcp_bridge PROPERTIES
    SUFFIX ".gup"
    OUTPUT_NAME "mcp_bridge"
)

target_sources(mcp_bridge PRIVATE src/mcp_bridge.def)

# ── Includes ─────────────────────────────────────────────────────
target_include_directories(mcp_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}"          # for generated/chat_tool_registry.inc
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
    MAX_SDK_VERSION=${MAX_VERSION}
    MCP_BRIDGE_VERSION="${MCP_BRIDGE_VERSION}"
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
    target_compile_options(mcp_bridge PRIVATE /W3 /MP /EHa)
    set_property(TARGET mcp_bridge PROPERTY MSVC_RUNTIME_LIBRARY
        "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
endif()

# ── Deploy ───────────────────────────────────────────────────────
set(MAX_PLUGINS_DIR "C:/Program Files/Autodesk/3ds Max ${MAX_VERSION}/plugins"
    CACHE PATH "3ds Max plugins directory")

install(TARGETS mcp_bridge
    RUNTIME DESTINATION "${MAX_PLUGINS_DIR}"
    LIBRARY DESTINATION "${MAX_PLUGINS_DIR}"
)
