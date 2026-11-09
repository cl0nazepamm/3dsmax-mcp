cmake_minimum_required(VERSION 3.20)

# Single source of truth for the package version: pyproject.toml. CMake parses
# it so the native project version stays in sync without a second bump site.
set(MCP_BRIDGE_VERSION "0.0.0")
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml")
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml" _pyproject)
    string(REGEX MATCH "version[ \t]*=[ \t]*\"([^\"]+)\"" _ "${_pyproject}")
    if(CMAKE_MATCH_1)
        set(MCP_BRIDGE_VERSION "${CMAKE_MATCH_1}")
    endif()
endif()
message(STATUS "mcp_bridge version: ${MCP_BRIDGE_VERSION}")

# ── Max version ──────────────────────────────────────────────────
set(MAX_VERSION "2026" CACHE STRING "Target 3ds Max version (2023, 2024, 2025, 2026, 2027)")
message(STATUS "Building for 3ds Max ${MAX_VERSION}")

project(mcp_bridge VERSION ${MCP_BRIDGE_VERSION} LANGUAGES CXX)

# 3ds Max 2027 SDK requires C++20; earlier versions use C++17.
if(MAX_VERSION GREATER_EQUAL 2027)
    set(CMAKE_CXX_STANDARD 20)
else()
    set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Compiler requirements per version:
#   2023-2026 -> C++17
#   2027+     -> C++20
if(MAX_VERSION GREATER_EQUAL 2027)
    message(STATUS "Max 2027+ SDK: C++20 enabled")
endif()

# ── Max SDK ──────────────────────────────────────────────────────
if(NOT DEFINED MAXSDK_PATH)
    set(MAXSDK_PATH "C:/Program Files/Autodesk/3ds Max ${MAX_VERSION} SDK/maxsdk"
        CACHE PATH "Path to 3ds Max SDK")
endif()

if(NOT EXISTS "${MAXSDK_PATH}/include/max.h")
    message(FATAL_ERROR "Cannot find max.h at ${MAXSDK_PATH}/include/max.h")
endif()

# ── Generated native diagnostic registry ─────────────────────────
# This registry serves invoke_tool and the native smoke harness only. External
# MCP exposes the Python tool schemas and performs their orchestration.
set(TOOL_REGISTRY_INC "${CMAKE_CURRENT_SOURCE_DIR}/generated/native_tool_registry.inc")
set(TOOL_SMOKE_INC "${CMAKE_CURRENT_SOURCE_DIR}/generated/tool_smoke_cases.inc")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
file(GLOB_RECURSE PY_TOOL_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../maxmcp/tools/*.py")
add_custom_command(
    OUTPUT "${TOOL_REGISTRY_INC}"
    COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_registry.py"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_registry.py" ${PY_TOOL_SOURCES}
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/.."
    COMMENT "Generating native_tool_registry.inc from maxmcp/tools/*.py"
    VERBATIM
)
add_custom_target(gen_tool_registry DEPENDS "${TOOL_REGISTRY_INC}")
add_custom_command(
    OUTPUT "${TOOL_SMOKE_INC}"
    COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_smoke.py"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_smoke.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/gen_tool_registry.py" ${PY_TOOL_SOURCES}
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/.."
    COMMENT "Generating tool_smoke_cases.inc from maxmcp/tools/*.py"
    VERBATIM
)
add_custom_target(gen_tool_smoke DEPENDS "${TOOL_SMOKE_INC}")

# ── Shell / core partition ───────────────────────────────────────
# SHELL: what Max loads and keeps, and everything Max holds a pointer to:
# plugin registration, named pipe, main-thread executor, dialog monitor and its
# Qt reader, scene journal, GDI+ lifetime, the agent viewport, render_start's
# armed job and notifications, and undo records. It answers ping, health,
# native:max_dialogs and native:core itself and reaches the core only through
# McpCoreApi, behind the drain gate (core_control.cpp, core_host.cpp).
set(MCP_SHELL_SOURCES
    src/dllmain.cpp
    src/bridge_gup.cpp
    src/pipe_server.cpp
    src/main_thread_executor.cpp
    src/dialog_watch.cpp
    src/bridge_health.cpp
    src/qt_dialogs.cpp
    src/gdiplus_runtime.cpp
    src/scene_journal.cpp
    src/agent_viewport.cpp
    src/render_host.cpp
    src/undo_records.cpp
    src/core_host.cpp
    src/core_control.cpp
    src/python_binding.cpp
)

# SHARED: stateless code both sides call (agent viewport and handlers). Linked
# once into the .gup; with MCP_HOT_CORE, into the shell and the core DLL alike.
set(MCP_SHARED_SOURCES
    src/spatial_snapshot.cpp
)

# CORE: command dispatch and the handlers, exported as mcp_core_api. It
# reaches the shell only through McpHostApi (host_api.h, core_bridge.cpp).
set(MCP_CORE_SOURCES
    src/core_api.cpp
    src/core_bridge.cpp
    src/command_dispatcher.cpp
    src/handlers/scene_handlers.cpp
    src/handlers/scene_patch_handlers.cpp
    src/handlers/scene_qa_handlers.cpp
    src/handlers/object_handlers.cpp
    src/handlers/orientation_handlers.cpp
    src/handlers/modifier_handlers.cpp
    src/handlers/mesh_handlers.cpp
    src/handlers/mcg_handlers.cpp
    src/handlers/inspect_handlers.cpp
    src/handlers/scene_manage_handlers.cpp
    src/handlers/file_handlers.cpp
    src/handlers/viewport_handlers.cpp
    src/handlers/job_handlers.cpp
    src/handlers/identify_handlers.cpp
    src/handlers/material_handlers.cpp
    src/handlers/material_network_handlers.cpp
    src/handlers/material_library_handlers.cpp
    src/handlers/controller_handlers.cpp
    src/handlers/keyframe_handlers.cpp
    src/handlers/tyflow_handlers.cpp
    src/handlers/plugin_introspect_handlers.cpp
    src/plugin_access.cpp
    src/handlers/plugin_access_handlers.cpp
    src/handlers/organize_handlers.cpp
    src/handlers/learning_handlers.cpp
    src/handlers/effects_handlers.cpp
    src/handlers/render_handlers.cpp
    src/handlers/state_sets_handlers.cpp
    src/handlers/mainthread_handlers.cpp
    src/handlers/discovery_handlers.cpp
    src/handlers/capabilities_handlers.cpp
    src/handlers/tool_test_handlers.cpp
    src/native_tool_registry.cpp
    src/window_capture.cpp
)

# OFF (default): the core objects are linked into mcp_bridge.gup and the shell
# binds mcp_core_api directly. ON: the same objects form core/mcp_core.dll,
# which the shell maps from a shadow copy and swaps on native:core reload.
option(MCP_HOT_CORE "Build the reloadable core as a separate DLL (dev only)" OFF)

# ── Shared build settings ────────────────────────────────────────
# One definition for the shell, the core objects and the core DLL, so the
# settings that shape the ABI across the boundary cannot diverge.
add_library(mcp_bridge_settings INTERFACE)
target_include_directories(mcp_bridge_settings INTERFACE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/third_party"
    "${MAXSDK_PATH}/include"
)
target_compile_definitions(mcp_bridge_settings INTERFACE
    NOMINMAX
    _UNICODE UNICODE
    WIN32 _WIN32
    _USRDLL
    _CRT_SECURE_NO_WARNINGS
    MAX_SILENCE_DEPRECATED_MATRIX_BOOL_CTOR
    MAX_SDK_VERSION=${MAX_VERSION}
    MCP_BRIDGE_VERSION="${MCP_BRIDGE_VERSION}"
)
target_link_directories(mcp_bridge_settings INTERFACE
    "${MAXSDK_PATH}/lib/x64/Release"
)
target_link_libraries(mcp_bridge_settings INTERFACE
    core.lib
    maxutil.lib
    gup.lib
    Paramblk2.lib
    Maxscrpt.lib
    Geom.lib
    mesh.lib
    ViewSystem.lib
    comctl32.lib
)
if(MSVC)
    target_compile_options(mcp_bridge_settings INTERFACE /W3 /MP /EHa)
endif()

# The runtime library is a target property, not a usage requirement.
function(mcp_bridge_apply_settings target)
    target_link_libraries(${target} PRIVATE mcp_bridge_settings)
    if(MSVC)
        set_property(TARGET ${target} PROPERTY MSVC_RUNTIME_LIBRARY
            "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
    endif()
endfunction()

# ── Qt dialogs ───────────────────────────────────────────────────
# qt_dialogs.cpp (shell) reads Qt dialogs through the Qt DLLs Max already loads;
# no Qt binary is shipped. It compiles against vendored headers of the oldest Qt
# of Max's major version and links import libraries generated from the vendored
# symbol lists. Regenerate both with scripts/vendor_qt.py.
if(MAX_VERSION GREATER_EQUAL 2025)
    set(MCP_QT_VERSION "6.5.3")
else()
    set(MCP_QT_VERSION "5.15.1")
endif()
set(MCP_QT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/qt/${MCP_QT_VERSION}")
file(GLOB MCP_QT_DEFS CONFIGURE_DEPENDS "${MCP_QT_DIR}/*.def")
if(NOT MCP_QT_DEFS)
    message(FATAL_ERROR "Missing Qt symbol lists in ${MCP_QT_DIR}; run scripts/vendor_qt.py")
endif()
set(MCP_QT_IMPORT_LIBS)
foreach(def ${MCP_QT_DEFS})
    get_filename_component(module "${def}" NAME_WE)
    set(import_lib "${CMAKE_CURRENT_BINARY_DIR}/qt/${module}.lib")
    add_custom_command(
        OUTPUT "${import_lib}"
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/qt"
        COMMAND "${CMAKE_AR}" /nologo "/def:${def}" "/out:${import_lib}" /machine:x64
        DEPENDS "${def}"
        COMMENT "Generating ${module} import library"
        VERBATIM)
    list(APPEND MCP_QT_IMPORT_LIBS "${import_lib}")
endforeach()
add_custom_target(qt_import_libs DEPENDS ${MCP_QT_IMPORT_LIBS})
set_source_files_properties(src/qt_dialogs.cpp PROPERTIES
    INCLUDE_DIRECTORIES "${MCP_QT_DIR}/include"
    # QT_NO_DEBUG compiles out Q_ASSERT in inline Qt code: NDEBUG does not,
    # and a failing assert would call qFatal and abort 3ds Max.
    COMPILE_DEFINITIONS "QT_NO_VERSION_TAGGING;QT_NO_DEBUG"
    COMPILE_OPTIONS "/Zc:__cplusplus;/permissive-")

# ── Object libraries ─────────────────────────────────────────────
add_library(mcp_shared_objs OBJECT ${MCP_SHARED_SOURCES})
mcp_bridge_apply_settings(mcp_shared_objs)

add_library(mcp_core_objs OBJECT ${MCP_CORE_SOURCES})
mcp_bridge_apply_settings(mcp_core_objs)
# The generated native tool registry and smoke cases are core-only.
target_include_directories(mcp_core_objs PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"          # for generated/native_tool_registry.inc
)
add_dependencies(mcp_core_objs gen_tool_registry gen_tool_smoke)

# ── Plugin ───────────────────────────────────────────────────────
add_library(mcp_bridge SHARED ${MCP_SHELL_SOURCES} src/mcp_bridge.def)
mcp_bridge_apply_settings(mcp_bridge)
target_sources(mcp_bridge PRIVATE $<TARGET_OBJECTS:mcp_shared_objs>)
target_link_libraries(mcp_bridge PRIVATE ${MCP_QT_IMPORT_LIBS})
add_dependencies(mcp_bridge mcp_shared_objs qt_import_libs)

# Output as .gup
set_target_properties(mcp_bridge PROPERTIES
    SUFFIX ".gup"
    OUTPUT_NAME "mcp_bridge"
)

if(MCP_HOT_CORE)
    # Development layout: no install rule; the shell loads core/mcp_core.dll
    # beside it (or MCP_CORE_DLL, or the path in mcp_core.path).
    message(STATUS "MCP_HOT_CORE: building core/mcp_core.dll and a shell that reloads it (dev only)")
    target_sources(mcp_bridge PRIVATE src/core_loader.cpp)
    target_compile_definitions(mcp_core_objs PRIVATE MCP_CORE_DLL)
    add_library(mcp_core SHARED $<TARGET_OBJECTS:mcp_core_objs> $<TARGET_OBJECTS:mcp_shared_objs>)
    mcp_bridge_apply_settings(mcp_core)
    add_dependencies(mcp_core mcp_core_objs mcp_shared_objs)
    set_target_properties(mcp_core PROPERTIES
        SUFFIX ".dll"
        OUTPUT_NAME "mcp_core"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/core"
        ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/core"
        PDB_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/core"
    )
    target_compile_definitions(mcp_bridge PRIVATE MCP_HOT_CORE=1)
    add_dependencies(mcp_bridge mcp_core)
else()
    target_sources(mcp_bridge PRIVATE $<TARGET_OBJECTS:mcp_core_objs>)
    add_dependencies(mcp_bridge mcp_core_objs)
endif()

# ── Python module (_mx) ──────────────────────────────────────────
# The agent-facing Python API, bound from the SDK with nanobind (vendored in
# third_party/nanobind). It loads into 3ds Max's embedded Python, so it is built
# against that Python's minor version. Python_ROOT_DIR picks the CPython whose
# headers and import library are used.
option(MCP_MX_PYTHON "Build the _mx Python module" OFF)
option(MCP_MX_DEV_RELOAD "Tag _mx's binding types per build, so a rebuilt module loads beside the old one (dev only)" OFF)
if(MCP_MX_PYTHON)
    set(MCP_MX_PYTHON_2027 3.13)
    set(MCP_MX_PYTHON_VERSION "${MCP_MX_PYTHON_${MAX_VERSION}}")
    if(NOT MCP_MX_PYTHON_VERSION)
        message(FATAL_ERROR "_mx: no Python version recorded for 3ds Max ${MAX_VERSION}")
    endif()
    find_package(Python ${MCP_MX_PYTHON_VERSION} EXACT REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(nanobind CONFIG REQUIRED PATHS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/nanobind/cmake" NO_DEFAULT_PATH)

    nanobind_add_module(_mx NB_STATIC NB_DOMAIN mcp_mx
        # core: shared rules, values, MAXScript and the bridge's handlers
        python/mx_module.cpp
        python/mx_core.cpp
        python/mx_maxscript.cpp
        python/mx_native.cpp
        python/mx_construction.cpp
        # object model
        python/mx_params.cpp
        python/mx_nodes.cpp
        python/mx_modifiers.cpp
        python/mx_materials.cpp
        python/mx_tracks.cpp
        python/mx_controllers.cpp
        # domains
        python/mx_scene.cpp
        python/mx_organize.cpp
        python/mx_viewport.cpp
        python/mx_files.cpp
        python/mx_mesh.cpp
        python/mx_solids.cpp
        python/mx_curves.cpp
        python/mx_lighting.cpp
        python/mx_plugins.cpp)
    mcp_bridge_apply_settings(_mx)
    target_link_libraries(_mx PRIVATE psapi.lib)

    set(MCP_MX_TAG_DIR "${CMAKE_CURRENT_BINARY_DIR}/mx_generated")
    set(MCP_MX_TAG_HEADER "${MCP_MX_TAG_DIR}/mx_build_tag.h")
    if(MCP_MX_DEV_RELOAD)
        add_custom_target(mx_build_tag
            COMMAND "${CMAKE_COMMAND}" "-DOUT=${MCP_MX_TAG_HEADER}" -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/mx_build_tag.cmake"
            BYPRODUCTS "${MCP_MX_TAG_HEADER}"
            COMMENT "Tagging _mx build")
        add_dependencies(_mx mx_build_tag)
    else()
        string(MAKE_C_IDENTIFIER "v${MCP_BRIDGE_VERSION}" MCP_MX_RELEASE_TAG)
        execute_process(COMMAND "${CMAKE_COMMAND}" "-DOUT=${MCP_MX_TAG_HEADER}" "-DTAG=${MCP_MX_RELEASE_TAG}"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/mx_build_tag.cmake")
    endif()
    target_include_directories(_mx PRIVATE "${MCP_MX_TAG_DIR}")
    set_target_properties(_mx PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/python"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/python")
endif()

# ── Deploy ───────────────────────────────────────────────────────
option(MCP_BUILD_TRANSACTION_POLICY_TESTS "Build SDK-independent dispatcher transaction policy tests" OFF)
if(MCP_BUILD_TRANSACTION_POLICY_TESTS)
    enable_testing()
    add_executable(transaction_policy_tests tests/transaction_policy_tests.cpp)
    target_include_directories(transaction_policy_tests PRIVATE include third_party)
    add_test(NAME transaction_policy COMMAND transaction_policy_tests)
    add_executable(capture_region_tests tests/capture_region_tests.cpp)
    target_include_directories(capture_region_tests PRIVATE include third_party)
    add_test(NAME capture_region COMMAND capture_region_tests)
    add_executable(color_value_tests tests/color_value_tests.cpp)
    target_include_directories(color_value_tests PRIVATE include)
    add_test(NAME color_value COMMAND color_value_tests)
    add_executable(dialog_sweeper_tests tests/dialog_sweeper_tests.cpp src/dialog_watch.cpp)
    target_include_directories(dialog_sweeper_tests PRIVATE include third_party)
    target_compile_definitions(dialog_sweeper_tests PRIVATE NOMINMAX _UNICODE UNICODE)
    add_test(NAME dialog_sweeper COMMAND dialog_sweeper_tests)
    # Max's exit: idle pipe clients and queued requests must not hold GUP::Stop.
    add_executable(shutdown_tests tests/shutdown_tests.cpp src/pipe_server.cpp
        src/main_thread_executor.cpp src/dialog_watch.cpp src/bridge_health.cpp)
    target_include_directories(shutdown_tests PRIVATE include third_party)
    target_compile_definitions(shutdown_tests PRIVATE NOMINMAX _UNICODE UNICODE)
    add_test(NAME shutdown COMMAND shutdown_tests)
    # MAXScript from a direct handler still reaches the executor's thread.
    add_executable(core_bridge_tests tests/core_bridge_tests.cpp src/core_bridge.cpp
        src/main_thread_executor.cpp src/dialog_watch.cpp)
    target_include_directories(core_bridge_tests PRIVATE include third_party)
    target_compile_definitions(core_bridge_tests PRIVATE NOMINMAX _UNICODE UNICODE)
    add_test(NAME core_bridge COMMAND core_bridge_tests)

    # Core lifecycle: drain gate, swap sequence, version gate, shell control
    # protocol, envelope and source resolution, with fake cores in process.
    add_executable(core_reload_tests tests/core_reload_tests.cpp)
    target_include_directories(core_reload_tests PRIVATE include third_party)
    target_compile_definitions(core_reload_tests PRIVATE NOMINMAX _UNICODE UNICODE)
    target_compile_options(core_reload_tests PRIVATE /EHa)
    add_test(NAME core_reload COMMAND core_reload_tests)

    # The shadow-copy loader against fake core DLLs: a matching one, an older
    # API version, one without the export, and one importing from a DLL that
    # does not exist (its import library comes from a generated .def).
    set(MCP_MISSING_DEF "${CMAKE_CURRENT_BINARY_DIR}/mcp_missing_dependency.def")
    set(MCP_MISSING_LIB "${CMAKE_CURRENT_BINARY_DIR}/mcp_missing_dependency.lib")
    file(WRITE "${MCP_MISSING_DEF}" "LIBRARY mcp_missing_dependency\nEXPORTS\n    mcp_missing_function\n")
    add_custom_command(
        OUTPUT "${MCP_MISSING_LIB}"
        COMMAND "${CMAKE_AR}" /nologo "/def:${MCP_MISSING_DEF}" "/out:${MCP_MISSING_LIB}" /machine:x64
        DEPENDS "${MCP_MISSING_DEF}"
        COMMENT "Generating the import library of a DLL that does not exist"
        VERBATIM)
    add_custom_target(mcp_missing_dependency_lib DEPENDS "${MCP_MISSING_LIB}")
    foreach(fake fake_core fake_core_v1 fake_core_noexport fake_core_badimport)
        add_library(${fake} SHARED tests/fake_core.cpp)
        target_include_directories(${fake} PRIVATE include)
        target_compile_definitions(${fake} PRIVATE MCP_CORE_DLL MAX_SDK_VERSION=${MAX_VERSION})
    endforeach()
    target_compile_definitions(fake_core_v1 PRIVATE FAKE_CORE_API_VERSION=1)
    target_compile_definitions(fake_core_noexport PRIVATE FAKE_CORE_NO_EXPORT)
    target_compile_definitions(fake_core_badimport PRIVATE FAKE_CORE_MISSING_IMPORT)
    target_link_libraries(fake_core_badimport PRIVATE "${MCP_MISSING_LIB}")
    add_dependencies(fake_core_badimport mcp_missing_dependency_lib)
    add_executable(core_loader_tests tests/core_loader_tests.cpp src/core_loader.cpp)
    target_include_directories(core_loader_tests PRIVATE include)
    target_compile_definitions(core_loader_tests PRIVATE NOMINMAX _UNICODE UNICODE MAX_SDK_VERSION=${MAX_VERSION}
        "FAKE_CORE=\"$<TARGET_FILE:fake_core>\""
        "FAKE_CORE_V1=\"$<TARGET_FILE:fake_core_v1>\""
        "FAKE_CORE_NOEXPORT=\"$<TARGET_FILE:fake_core_noexport>\""
        "FAKE_CORE_BADIMPORT=\"$<TARGET_FILE:fake_core_badimport>\"")
    add_dependencies(core_loader_tests fake_core fake_core_v1 fake_core_noexport fake_core_badimport)
    add_test(NAME core_loader COMMAND core_loader_tests)
endif()

option(MCP_BUILD_AGENT_VIEWPORT_PROBE "Build optional live agent viewport SDK test harness" OFF)
if(MCP_BUILD_AGENT_VIEWPORT_PROBE)
    add_library(agent_viewport_probe SHARED tests/agent_viewport_probe.cpp src/agent_viewport.cpp src/spatial_snapshot.cpp src/scene_journal.cpp src/handlers/viewport_handlers.cpp src/handlers/job_handlers.cpp src/gdiplus_runtime.cpp src/main_thread_executor.cpp src/dialog_watch.cpp
        src/core_bridge.cpp src/core_host.cpp src/render_host.cpp src/undo_records.cpp)
    target_include_directories(agent_viewport_probe PRIVATE include third_party "${MAXSDK_PATH}/include")
    target_compile_definitions(agent_viewport_probe PRIVATE NOMINMAX _UNICODE UNICODE MAX_SDK_VERSION=${MAX_VERSION} MAX_SILENCE_DEPRECATED_MATRIX_BOOL_CTOR)
    target_link_directories(agent_viewport_probe PRIVATE "${MAXSDK_PATH}/lib/x64/Release")
    target_link_libraries(agent_viewport_probe PRIVATE core.lib maxutil.lib Maxscrpt.lib Geom.lib mesh.lib Paramblk2.lib ViewSystem.lib)
    target_compile_options(agent_viewport_probe PRIVATE /EHa)
    target_sources(agent_viewport_probe PRIVATE src/window_capture.cpp)
endif()

set(MAX_PLUGINS_DIR "C:/Program Files/Autodesk/3ds Max ${MAX_VERSION}/plugins"
    CACHE PATH "3ds Max plugins directory")

if(NOT MCP_HOT_CORE)
    install(TARGETS mcp_bridge
        RUNTIME DESTINATION "${MAX_PLUGINS_DIR}"
        LIBRARY DESTINATION "${MAX_PLUGINS_DIR}"
    )
endif()
