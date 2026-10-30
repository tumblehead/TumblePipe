cmake_minimum_required(VERSION 3.22)
project(hdThPlayblast LANGUAGES CXX)

# hdThPlayblast: the playblast Hydra render delegate husk loads. Same shape as
# src/resolver (Rust staticlib core + force-linked C++ shim in a MODULE); see
# src/resolver/CMakeLists.txt for the reasoning behind each platform branch.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

if(NOT DEFINED HFS AND DEFINED ENV{HFS})
    set(HFS $ENV{HFS})
endif()
if(NOT DEFINED HFS)
    message(FATAL_ERROR
        "HFS not set. Pass -DHFS=/path/to/hfs<ver> or export HFS=... so "
        "CMake can locate Houdini's bundled USD.")
endif()

list(APPEND CMAKE_PREFIX_PATH "${HFS}/toolkit/cmake")
find_package(Houdini REQUIRED)

file(WRITE "${CMAKE_BINARY_DIR}/BUILD_INFO"
    "hfs=${HFS}\nhoudini_version=${Houdini_VERSION}\n")

# -----------------------------------------------------------------------------
# Rust core (staticlib)
# -----------------------------------------------------------------------------

find_program(CARGO_EXECUTABLE cargo REQUIRED)

# The opt-in wgpu path (src/gpu.rs, used when TH_PLAYBLAST_GPU=1). On by
# default where it has been verified (Windows: Vulkan/DX12); elsewhere until
# a Linux or macOS build has been checked.
if(WIN32)
    option(TH_DELEGATE_GPU "Build the wgpu GPU path" ON)
else()
    option(TH_DELEGATE_GPU "Build the wgpu GPU path" OFF)
endif()
set(RUST_FEATURES "")
if(TH_DELEGATE_GPU)
    set(RUST_FEATURES --features gpu)
endif()

set(RUST_TARGET_DIR "${CMAKE_BINARY_DIR}/rust-target")
set(RUST_STATICLIB
    "${RUST_TARGET_DIR}/release/${CMAKE_STATIC_LIBRARY_PREFIX}th_delegate_core${CMAKE_STATIC_LIBRARY_SUFFIX}")

include(ExternalProject)
ExternalProject_Add(th_delegate_core_rust
    PREFIX "${CMAKE_BINARY_DIR}/rust-ext"
    SOURCE_DIR "${CMAKE_SOURCE_DIR}"
    CONFIGURE_COMMAND ""
    BUILD_COMMAND ${CARGO_EXECUTABLE} build
        --release
        ${RUST_FEATURES}
        --manifest-path "${CMAKE_SOURCE_DIR}/Cargo.toml"
        --target-dir "${RUST_TARGET_DIR}"
    INSTALL_COMMAND ""
    BUILD_BYPRODUCTS "${RUST_STATICLIB}"
    BUILD_ALWAYS TRUE
)

add_library(th_delegate_core STATIC IMPORTED GLOBAL)
set_target_properties(th_delegate_core PROPERTIES
    IMPORTED_LOCATION "${RUST_STATICLIB}")
add_dependencies(th_delegate_core th_delegate_core_rust)

# -----------------------------------------------------------------------------
# C++ shim, force-linked (its registrar is referenced by nothing)
# -----------------------------------------------------------------------------

add_library(hdThPlayblast_cpp STATIC cpp/delegate.cpp)
target_link_libraries(hdThPlayblast_cpp PUBLIC Houdini)

add_library(hdThPlayblast MODULE cpp/module_stub.cpp)
target_link_libraries(hdThPlayblast PRIVATE Houdini)

if(MSVC)
    target_link_libraries(hdThPlayblast PRIVATE th_delegate_core hdThPlayblast_cpp)
    set_target_properties(hdThPlayblast PROPERTIES
        LINK_FLAGS "/WHOLEARCHIVE:hdThPlayblast_cpp.lib")
elseif(APPLE)
    target_link_libraries(hdThPlayblast PRIVATE
        th_delegate_core
        "-Wl,-force_load"
        hdThPlayblast_cpp)
else()
    target_link_libraries(hdThPlayblast PRIVATE
        "-Wl,--whole-archive"
        hdThPlayblast_cpp
        "-Wl,--no-whole-archive"
        th_delegate_core)
endif()

if(WIN32)
    # opengl32 / d3dcompiler: wgpu-hal's GL (WGL) and DX12 (FXC) backends.
    target_link_libraries(hdThPlayblast PRIVATE ws2_32 userenv ntdll bcrypt advapi32
        opengl32 d3dcompiler user32 gdi32 ole32)
elseif(APPLE)
    target_link_libraries(hdThPlayblast PRIVATE "-framework CoreFoundation")
else()
    target_link_libraries(hdThPlayblast PRIVATE pthread dl)
endif()

# The pxr libraries the shim references (see src/resolver for why Windows
# links the import libs and macOS defers lookup).
if(APPLE)
    target_link_options(hdThPlayblast PRIVATE -undefined dynamic_lookup)
elseif(WIN32)
    set(_hfs_usd_libdir "${HFS}/custom/houdini/dsolib")
    foreach(_usdlib arch tf gf vt sdf hf hd hdsi hio trace work plug python)
        find_library(HFS_PXR_${_usdlib}
            NAMES libpxr_${_usdlib} pxr_${_usdlib}
            HINTS "${_hfs_usd_libdir}"
            NO_DEFAULT_PATH)
        if(NOT HFS_PXR_${_usdlib})
            message(FATAL_ERROR
                "Could not find libpxr_${_usdlib} under ${_hfs_usd_libdir}; "
                "Houdini install may differ from the expected layout.")
        endif()
        target_link_libraries(hdThPlayblast PRIVATE "${HFS_PXR_${_usdlib}}")
    endforeach()

    find_library(HFS_PYTHON
        NAMES python313
        HINTS "${HFS}/python313/libs"
        NO_DEFAULT_PATH)
    if(NOT HFS_PYTHON)
        message(FATAL_ERROR
            "Could not find Houdini's bundled python313 import lib under "
            "${HFS}/python313/libs.")
    endif()
    target_link_libraries(hdThPlayblast PRIVATE "${HFS_PYTHON}")
endif()

set_target_properties(hdThPlayblast PROPERTIES
    PREFIX ""
    OUTPUT_NAME "hdThPlayblast"
)

if(NOT WIN32)
    if(APPLE)
        set(_tt_rpath "@loader_path")
    else()
        set(_tt_rpath "$ORIGIN")
    endif()
    set_target_properties(hdThPlayblast PROPERTIES
        BUILD_WITH_INSTALL_RPATH TRUE
        INSTALL_RPATH "${_tt_rpath}"
    )
endif()

# -----------------------------------------------------------------------------
# plugInfo.json + install layout
# -----------------------------------------------------------------------------
#
#   <prefix>/hdThPlayblast/
#     lib/hdThPlayblast.<ext>
#     resources/plugInfo.json
#     BUILD_INFO

set(PLUGIN_LIBRARY_FILENAME "hdThPlayblast${CMAKE_SHARED_MODULE_SUFFIX}")
configure_file(cpp/plugInfo.json.in
    "${CMAKE_BINARY_DIR}/resources/plugInfo.json"
    @ONLY)

install(TARGETS hdThPlayblast
    LIBRARY DESTINATION hdThPlayblast/lib
    RUNTIME DESTINATION hdThPlayblast/lib)
install(FILES "${CMAKE_BINARY_DIR}/resources/plugInfo.json"
    DESTINATION hdThPlayblast/resources)
install(FILES "${CMAKE_BINARY_DIR}/BUILD_INFO"
    DESTINATION hdThPlayblast)
