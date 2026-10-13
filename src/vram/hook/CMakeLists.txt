cmake_minimum_required(VERSION 3.22)
project(tt_vramhook LANGUAGES CXX)

# The VRAM Inspector's viewport hook: a DM_SceneHook render hook plus three
# hscript commands, built as one HDK DSO. See README.md.
#
# Built by .ci/build_vramhook.py (the build-vramhook prepack script) into
# dso/<major>.<minor>/, which hpm.toml puts on HOUDINI_DSO_PATH.

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

if(NOT DEFINED HFS AND DEFINED ENV{HFS})
    set(HFS $ENV{HFS})
endif()
if(NOT DEFINED HFS)
    message(FATAL_ERROR
        "HFS not set. Pass -DHFS=/path/to/hfs<ver> or export HFS=... so "
        "CMake can locate the HDK.")
endif()

list(APPEND CMAKE_PREFIX_PATH "${HFS}/toolkit/cmake")
find_package(Houdini REQUIRED)

add_library(tt_vramhook SHARED vramhook.cpp)
target_link_libraries(tt_vramhook Houdini)

# Houdini 22's macOS HoudiniConfig.cmake omits USE_VULKAN (Windows and Linux
# define it). Without it GR_Defines.h stubs RV_TextureRef as an int, which
# collides with the real class that GUI_DetailLook.h pulls in through
# GR_Light.h and GR_Material.h -> RV_TextureCache.h — the HDK's own headers
# do not compile without it, so SideFX's libraries are built with it.
# Defining it again where it is already set is harmless.
target_compile_definitions(tt_vramhook PRIVATE USE_VULKAN)

# Sets the HDK's compile definitions (UT_DSO_TAGINFO, platform defines) and
# the DSO naming Houdini expects; the install step below places the result.
houdini_configure_target(tt_vramhook INSTDIR "${CMAKE_BINARY_DIR}/hdk-out")

install(TARGETS tt_vramhook
        RUNTIME DESTINATION .
        LIBRARY DESTINATION .)
