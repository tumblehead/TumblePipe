// Translation unit intentionally empty.
//
// CMake requires every MODULE library target to have at least one source.
// The delegate lives in delegate.cpp, compiled into the `hdThPlayblast_cpp`
// static library and force-linked into this MODULE, as src/resolver does:
// nothing references delegate.cpp's registrar by symbol.
