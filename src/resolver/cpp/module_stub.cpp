// Translation unit intentionally empty.
//
// CMake requires every MODULE library target to have at least one source.
// The actual C++ shim lives in resolver.cpp, which is compiled into the
// `tumbleResolver_cpp` static library and force-linked into this MODULE
// via /WHOLEARCHIVE on MSVC (-Wl,--whole-archive on GNU ld, -force_load on
// ld64). Nothing references resolver.cpp's object by symbol, so without the
// force-load the linker never pulls it out of the archive, the static
// registrar at the bottom of resolver.cpp never runs, and USD fails with
// "Failed to manufacture asset resolver" even though the TumbleResolver
// TfType still appears in ArResolver's derived list (from plugInfo.json).
