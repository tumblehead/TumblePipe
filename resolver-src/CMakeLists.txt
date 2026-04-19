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

set(RUST_TARGET_DIR "${CMAKE_BINARY_DIR}/rust-target")

# Force cargo onto the same target triple the C++ side is building for.
# Matters most on macOS where CI workers are typically x86_64 but we
# cross-compile to arm64 via -DCMAKE_OSX_ARCHITECTURES=arm64; without an
# explicit --target cargo follows the host and the linker silently
# discards every Rust object as "wrong architecture". The CI worker must
# have the matching rustup target installed (e.g. `rustup target add
# aarch64-apple-darwin`).
if(APPLE)
    if("${CMAKE_OSX_ARCHITECTURES}" STREQUAL "arm64")
        set(RUST_TARGET_TRIPLE "aarch64-apple-darwin")
    elseif("${CMAKE_OSX_ARCHITECTURES}" STREQUAL "x86_64")
        set(RUST_TARGET_TRIPLE "x86_64-apple-darwin")
    endif()
endif()

if(DEFINED RUST_TARGET_TRIPLE)
    set(_cargo_target_args --target ${RUST_TARGET_TRIPLE})
    set(RUST_PROFILE_DIR "${RUST_TARGET_DIR}/${RUST_TARGET_TRIPLE}/release")
else()
    set(_cargo_target_args "")
    set(RUST_PROFILE_DIR "${RUST_TARGET_DIR}/release")
endif()

set(RUST_STATICLIB
    "${RUST_PROFILE_DIR}/${CMAKE_STATIC_LIBRARY_PREFIX}th_resolver_core${CMAKE_STATIC_LIBRARY_SUFFIX}")

include(ExternalProject)
ExternalProject_Add(th_resolver_core_rust
    PREFIX "${CMAKE_BINARY_DIR}/rust-ext"
    SOURCE_DIR "${CMAKE_SOURCE_DIR}"
    CONFIGURE_COMMAND ""
    BUILD_COMMAND ${CARGO_EXECUTABLE} build
        --release
        --verbose
        ${_cargo_target_args}
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

add_library(tumbleResolver MODULE
    cpp/resolver.cpp
)

target_include_directories(tumbleResolver PRIVATE cpp)

target_link_libraries(tumbleResolver PRIVATE Houdini)

# Force-link the full Rust staticlib so the linker can't drop static
# constructors (panic handlers, TLS init, any future registration code).
# MSVC's /OPT:REF, GNU ld's --gc-sections, and macOS ld64's -dead_strip
# all discard sections that look unreferenced without this.
#
# Windows note: the Visual Studio (MSBuild) generator misclassifies
# `-WHOLEARCHIVE:...` string entries in target_link_libraries as stray
# library references and silently drops them; target_link_options with
# a LINKER: genex was rejected too. LINK_FLAGS writes the flag directly
# into MSBuild's AdditionalOptions, which reaches link.exe verbatim.
# Since RUST_STATICLIB is resolved at configure time, no genex is needed.
if(MSVC)
    target_link_libraries(tumbleResolver PRIVATE th_resolver_core)
    set_target_properties(tumbleResolver PROPERTIES
        LINK_FLAGS "/WHOLEARCHIVE:${RUST_STATICLIB}")
elseif(APPLE)
    target_link_libraries(tumbleResolver PRIVATE
        "-Wl,-force_load"
        th_resolver_core)
else()
    target_link_libraries(tumbleResolver PRIVATE
        "-Wl,--whole-archive"
        th_resolver_core
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
    # bundled python (currently 3.11 for Houdini 21) so those resolve.
    file(GLOB _hfs_python_dirs LIST_DIRECTORIES true "${HFS}/python3*")
    set(HFS_PYTHON HFS_PYTHON-NOTFOUND)
    foreach(_pydir ${_hfs_python_dirs})
        find_library(HFS_PYTHON
            NAMES python312 python311 python310 python39
            HINTS "${_pydir}/libs"
            NO_DEFAULT_PATH)
        if(HFS_PYTHON)
            break()
        endif()
    endforeach()
    if(NOT HFS_PYTHON)
        message(FATAL_ERROR
            "Could not find Houdini's bundled python import lib under "
            "${HFS}/python3*/libs; Houdini install may differ from the "
            "expected layout.")
    endif()
    target_link_libraries(tumbleResolver PRIVATE "${HFS_PYTHON}")
endif()

set_target_properties(tumbleResolver PROPERTIES
    PREFIX ""   # USD expects "tumbleResolver.<ext>", not "libtumbleResolver.<ext>"
    OUTPUT_NAME "tumbleResolver"
)

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
# generic placeholder, so USD's plugin system can load it by path.
set(PLUGIN_LIBRARY_FILENAME
    "${CMAKE_SHARED_MODULE_PREFIX}tumbleResolver${CMAKE_SHARED_MODULE_SUFFIX}")

# On windows CMake's module prefix is empty, so the filename collapses
# to `tumbleResolver.dll`, which is what we want.
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
# Callers point -DCMAKE_INSTALL_PREFIX at
# houdini/TumblePipe/resolver/<platform>/houdini<major>/.

install(TARGETS tumbleResolver
    LIBRARY DESTINATION tumbleResolver/lib
    RUNTIME DESTINATION tumbleResolver/lib)

install(FILES "${CMAKE_BINARY_DIR}/resources/plugInfo.json"
    DESTINATION tumbleResolver/resources)

install(FILES "${CMAKE_BINARY_DIR}/BUILD_INFO"
    DESTINATION tumbleResolver)
