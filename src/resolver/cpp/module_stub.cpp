// Translation unit intentionally empty.
//
// CMake requires every MODULE library target to have at least one source.
// The actual C++ shim lives in resolver.cpp, which is compiled into the
// `tumbleResolver_cpp` static library and force-linked into this MODULE
// via /WHOLEARCHIVE on MSVC (-Wl,--whole-archive on GNU ld, -force_load on
// ld64). That force-load is what keeps the AR_DEFINE_RESOLVER static
// initializer's COMDAT alive — without it, /OPT:REF strips the
// anonymous-namespace TF_REGISTRY_FUNCTION block, the Ar_ResolverFactory
// never gets registered, and USD's _PluginResolver::Create returns null
// with "Failed to manufacture asset resolver" even though the
// TumbleResolver TfType still appears in ArResolver's derived list.
