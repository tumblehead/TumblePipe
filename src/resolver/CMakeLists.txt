cmake_minimum_required(VERSION 3.22)
project(tumbleResolver LANGUAGES CXX)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# The caller provides $HFS pointing at a Houdini install. The USD CMake
# config is shipped under $HFS/toolkit/cmake. Pass it via -DHFS=... or
# set the environment variable.
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
#
# Builds the th_resolver_core crate via cargo into a known output dir
# and exposes it as an IMPORTED static library. Using ExternalProject_Add
# keeps us independent of third-party CMake packages (corrosion et al)
# and makes the build reproducible from nothing more than `cargo` + `cmake`.

find_program(CARGO_EXECUTABLE cargo REQUIRED)

# Native builds only: every CI worker (including the Apple Silicon macOS
# one) builds for its own architecture, so cargo's host target is the
# right one.
set(RUST_TARGET_DIR "${CMAKE_BINARY_DIR}/rust-target")
set(RUST_STATICLIB
    "${RUST_TARGET_DIR}/release/${CMAKE_STATIC_LIBRARY_PREFIX}th_resolver_core${CMAKE_STATIC_LIBRARY_SUFFIX}")

include(ExternalProject)
ExternalProject_Add(th_resolver_core_rust
    PREFIX "${CMAKE_BINARY_DIR}/rust-ext"
    SOURCE_DIR "${CMAKE_SOURCE_DIR}"
    CONFIGURE_COMMAND ""
    BUILD_COMMAND ${CARGO_EXECUTABLE} build
        --release
        --verbose
        --manifest-path "${CMAKE_SOURCE_DIR}/Cargo.toml"
        --target-dir "${RUST_TARGET_DIR}"
    INSTALL_COMMAND ""
    BUILD_BYPRODUCTS "${RUST_STATICLIB}"
    BUILD_ALWAYS TRUE
)

add_library(th_resolver_core STATIC IMPORTED GLOBAL)
set_target_properties(th_resolver_core PROPERTIES
    IMPORTED_LOCATION "${RUST_STATICLIB}")
add_dependencies(th_resolver_core th_resolver_core_rust)

# -----------------------------------------------------------------------------
# C++ shim
# -----------------------------------------------------------------------------
#
# resolver.cpp is compiled into a STATIC library, then force-linked into
# the MODULE library via /WHOLEARCHIVE (or its non-MSVC equivalent).
#
# The resolver factory is registered by a static initializer at the bottom
# of resolver.cpp (see the comment there for why it is not
# AR_DEFINE_RESOLVER). Nothing else in the DLL references that object, so
# without a whole-archive link the linker never pulls it in and the
# factory never gets registered: the TumbleResolver type still appears in
# ArResolver's derived-types list (from plugInfo.json) and 'entity' is a
# registered URI scheme, but USD fails with "Failed to manufacture asset
# resolver".

add_library(tumbleResolver_cpp STATIC cpp/resolver.cpp)
target_include_directories(tumbleResolver_cpp PUBLIC cpp)
target_link_libraries(tumbleResolver_cpp PUBLIC Houdini)

add_library(tumbleResolver MODULE cpp/module_stub.cpp)
target_link_libraries(tumbleResolver PRIVATE Houdini)

# Force-link the C++ shim AND the Rust staticlib. MSVC's /OPT:REF, GNU ld's
# --gc-sections, and macOS ld64's -dead_strip all discard sections that look
# unreferenced without this.
#
# Windows note: the Visual Studio (MSBuild) generator misclassifies
# `-WHOLEARCHIVE:...` string entries in target_link_libraries as stray
# library references and silently drops them; target_link_options with
# a LINKER: genex was rejected too. LINK_FLAGS writes the flag directly
# into MSBuild's AdditionalOptions, which reaches link.exe verbatim.
# RUST_STATICLIB is resolved at configure time; the C++ static lib is
# named by its CMake target so MSBuild adds it to the link line and
# /WHOLEARCHIVE:tumbleResolver_cpp.lib matches by basename.
if(MSVC)
    target_link_libraries(tumbleResolver PRIVATE th_resolver_core tumbleResolver_cpp)
    set_target_properties(tumbleResolver PROPERTIES
        LINK_FLAGS "/WHOLEARCHIVE:${RUST_STATICLIB} /WHOLEARCHIVE:tumbleResolver_cpp.lib")
elseif(APPLE)
    target_link_libraries(tumbleResolver PRIVATE
        "-Wl,-force_load"
        th_resolver_core
        "-Wl,-force_load"
        tumbleResolver_cpp)
else()
    target_link_libraries(tumbleResolver PRIVATE
        "-Wl,--whole-archive"
        th_resolver_core
        tumbleResolver_cpp
        "-Wl,--no-whole-archive")
endif()

# Rust's static lib can bring in platform-specific runtime deps.
if(WIN32)
    target_link_libraries(tumbleResolver PRIVATE ws2_32 userenv ntdll bcrypt advapi32)
elseif(APPLE)
    target_link_libraries(tumbleResolver PRIVATE
        "-framework CoreFoundation"
        "-framework Security")
else()
    # Linux: pthread + dl are brought in by USD, but list them explicitly
    # so the link order is stable.
    target_link_libraries(tumbleResolver PRIVATE pthread dl)
endif()

# HoudiniConfig's `Houdini` target publicly links the core HDK libs but
# does NOT list the pxr USD side-libs. On Linux the shared-module link
# tolerates the resulting undefined ArResolver / TfType / ArFilesystemAsset
# symbols — they get satisfied when Houdini loads the plugin and already
# has USD in its address space. On macOS and Windows the link step is
# strict, so we either defer symbol resolution (macOS dlopen-style) or
# link against the pxr .lib import files shipped under HFS (Windows).
if(APPLE)
    target_link_options(tumbleResolver PRIVATE -undefined dynamic_lookup)
elseif(WIN32)
    # Windows ships USD import libs alongside the DSOs under
    # `${HFS}/custom/houdini/dsolib`. Pull in the ones we reference.
    set(_hfs_usd_libdir "${HFS}/custom/houdini/dsolib")
    foreach(_usdlib ar arch sdf tf vt)
        find_library(HFS_PXR_${_usdlib}
            NAMES libpxr_${_usdlib} pxr_${_usdlib}
            HINTS "${_hfs_usd_libdir}"
            NO_DEFAULT_PATH)
        if(NOT HFS_PXR_${_usdlib})
            message(FATAL_ERROR
                "Could not find libpxr_${_usdlib} under ${_hfs_usd_libdir}; "
                "Houdini install may differ from the expected layout.")
        endif()
        target_link_libraries(tumbleResolver PRIVATE "${HFS_PXR_${_usdlib}}")
    endforeach()

    # Houdini's USD on Windows pulls in pxr_boost::python which
    # references Py_NoneStruct / Py_Dealloc. Link against Houdini's
    # bundled python so those resolve. Houdini 22 ships Python 3.13; a new
    # Houdini major with a different Python updates this name.
    find_library(HFS_PYTHON
        NAMES python313
        HINTS "${HFS}/python313/libs"
        NO_DEFAULT_PATH)
    if(NOT HFS_PYTHON)
        message(FATAL_ERROR
            "Could not find Houdini's bundled python313 import lib under "
            "${HFS}/python313/libs; Houdini install may differ from the "
            "expected layout.")
    endif()
    target_link_libraries(tumbleResolver PRIVATE "${HFS_PYTHON}")
endif()

set_target_properties(tumbleResolver PROPERTIES
    PREFIX ""   # USD expects "tumbleResolver.<ext>", not "libtumbleResolver.<ext>"
    OUTPUT_NAME "tumbleResolver"
)

# Resolve any sibling library from the plugin's own directory.
#
# The plugin loads today with no search path at all, because every one of its
# NEEDED entries is a Houdini library already in the host's address space by
# the time USD's Plug registry dlopens it. That stops being true the moment we
# ship a library of our own next to it, and the failure would be a plugin that
# silently never loads.
#
# `$ORIGIN` / `@loader_path` is the only mechanism that works here. The
# hpm.toml `[runtime]` entries for LD_LIBRARY_PATH / DYLD_LIBRARY_PATH cannot
# cover this case: Houdini applies package environment edits *after* process
# start, and both glibc and dyld read those variables once during startup, so a
# later dlopen never sees them. (The Windows PATH entry is genuinely different
# — LoadLibrary re-reads PATH at call time — which is exactly why this gap
# shows up on Linux and macOS only.)
#
# BUILD_WITH_INSTALL_RPATH makes the flag land in the binary the build tree
# produces, since that file is what gets packed; there is no `cmake --install`
# step in this pipeline.
if(NOT WIN32)
    if(APPLE)
        set(_tt_rpath "@loader_path")
    else()
        set(_tt_rpath "$ORIGIN")
    endif()
    set_target_properties(tumbleResolver PROPERTIES
        BUILD_WITH_INSTALL_RPATH TRUE
        INSTALL_RPATH "${_tt_rpath}"
    )
endif()

# Strip symbols in release to keep the plugin small. Skipped on macOS
# because `strip` invalidates the ad-hoc code signature that the linker
# attaches to arm64 binaries and dyld then refuses to load them. The
# Rust static lib is already stripped via the release profile, so the
# remaining C++ shim is small regardless.
if(CMAKE_BUILD_TYPE STREQUAL "Release" AND NOT MSVC AND NOT APPLE)
    add_custom_command(TARGET tumbleResolver POST_BUILD
        COMMAND "${CMAKE_STRIP}" "$<TARGET_FILE:tumbleResolver>"
        VERBATIM)
endif()

# -----------------------------------------------------------------------------
# plugInfo.json
# -----------------------------------------------------------------------------

# The plugInfo file needs the platform-specific library filename, not a
# generic placeholder, so USD's plugin system can load it by path. No
# CMAKE_SHARED_MODULE_PREFIX here: the target sets PREFIX "" above, and the
# module prefix is "lib" on Linux and macOS, so using it named a file that
# never exists and USD silently skipped the plugin.
set(PLUGIN_LIBRARY_FILENAME "tumbleResolver${CMAKE_SHARED_MODULE_SUFFIX}")
configure_file(cpp/plugInfo.json.in
    "${CMAKE_BINARY_DIR}/resources/plugInfo.json"
    @ONLY)

# -----------------------------------------------------------------------------
# Install layout
# -----------------------------------------------------------------------------
#
#   <prefix>/tumbleResolver/
#     lib/tumbleResolver.<ext>
#     resources/plugInfo.json
#     BUILD_INFO
#
# .ci/build_resolver.py installs with `--prefix <repo>/resolver/houdini<major>`;
# each per-platform release archive carries one platform's binaries there.

install(TARGETS tumbleResolver
    LIBRARY DESTINATION tumbleResolver/lib
    RUNTIME DESTINATION tumbleResolver/lib)

install(FILES "${CMAKE_BINARY_DIR}/resources/plugInfo.json"
    DESTINATION tumbleResolver/resources)

install(FILES "${CMAKE_BINARY_DIR}/BUILD_INFO"
    DESTINATION tumbleResolver)
