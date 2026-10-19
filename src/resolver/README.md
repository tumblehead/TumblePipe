# TumblePipe asset resolver

USD asset resolver for TumblePipe's `entity://` URI scheme. Rust core +
thin C++ ArResolver shim; produces a plugin loadable directly by
Houdini's bundled USD.

## Why this exists

This crate owns `entity://` URI resolution for TumblePipe. CI builds the
plugin per `(platform, houdini_major)` cell into
`<repo>/resolver/houdini<major>/tumbleResolver/`. Each per-platform release
archive contains exactly one platform's binaries at that path, and the
`hpm.toml` `[runtime]` table registers it with USD via
`PXR_PLUGINPATH_NAME` (keyed on `$HOUDINI_MAJOR_RELEASE`). The pipeline's
Python side (`tumblepipe.resolver`) is a thin facade that toggles env vars
and calls `Ar.GetResolver().Resolve()`; all resolution decisions happen
here in Rust.

## Layout

```
src/resolver/
├── Cargo.toml             # crate manifest
├── CMakeLists.txt         # orchestrates cargo + C++ shim + install layout
├── src/                   # Rust core
│   ├── lib.rs             # C ABI entry points + panic guard
│   ├── uri.rs             # entity://  parser
│   ├── resolve.rs         # the four flavors: department / staged / root / scene
│   ├── version.rs         # "latest" auto-discovery
│   ├── env.rs             # $TH_EXPORT_PATH + $TH_RESOLVER_LATEST_MODE
│   └── error.rs           # ResolveError + its FFI return codes
├── tests/
│   ├── resolve_integration.rs   # tempdir fixtures per flavor
│   └── props.rs                 # proptest properties over generated input
├── examples/
│   └── resolve_lines.rs   # stdin→stdout resolver for tests/test_resolver_parity.py
└── cpp/                   # ArResolver shim (URI-scheme resolver for entity://)
    ├── resolver.h
    ├── resolver.cpp
    ├── module_stub.cpp    # empty TU the MODULE target needs; see its comment
    ├── th_resolver_core.h # C ABI declarations exported by the Rust staticlib
    └── plugInfo.json.in
```

## URI scheme

Scheme: `entity:`. Four flavors routed by query parameters:

| Flavor     | Example                                                           | Resolves to                                                                   |
|------------|-------------------------------------------------------------------|-------------------------------------------------------------------------------|
| Department | `entity:/assets/SET/Arena?dept=lookdev&channel=default&version=v0013` | `$TH_EXPORT_PATH/assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd` |
| Staged     | `entity:/assets/CHAR/Crowd?channel=cheering`                      | `$TH_EXPORT_PATH/assets/CHAR/Crowd/_staged/cheering/v<latest>/CHAR_Crowd_v<latest>.usda` |
| Root       | `entity:/shots/sq050/sh446?dept=root&version=v0006`               | `$TH_EXPORT_PATH/shots/sq050/sh446/_root/v0006/shots_sq050_sh446_root_v0006.usda`        |
| Scene      | `entity:/scenes/arena?version=v0001`                              | `$TH_EXPORT_PATH/scenes/arena/_staged/v0001/arena_v0001.usda`                            |

`channel` names the publish channel. Layers published before the
variant→channel rename carry `variant=` instead; published versions are never
rewritten, so the parser reads that key exactly like `channel`, in one place
(`uri::channel_from_query`). Writers emit `channel` only.

Env inputs (read on every resolve; nothing is cached):

- `TH_EXPORT_PATH` *(required)*: base directory for all resolved paths.
- `TH_RESOLVER_LATEST_MODE`: `1` ignores explicit `version=` params and
  scans the filesystem for the highest `vNNNN`; `0` or unset honours them.
  Any other value fails every resolve with an error naming the variable.
  `tumblepipe.resolver.set_latest_mode()` is the only writer.
- `TH_RESOLVER_DEBUG`: when set (any non-empty value other than `0`),
  the C++ shim appends one line to `tumble-resolver.log` in the system
  temp directory (`std::filesystem::temp_directory_path()`: `%TEMP%` on
  Windows, `$TMPDIR` or `/tmp` elsewhere) and duplicates it to `stderr`,
  for every `ArResolver` override it sees (`_CreateIdentifier`,
  `_Resolve`, `_OpenAsset`, etc.) plus a `static_init_marker` line at
  load. Used to diagnose URI-dispatch problems where the plugin loads but
  `_Resolve` doesn't appear to be reached. The log file is the reliable
  channel: Houdini's launcher routes `stderr` away from the Python shell.

## Panics

No Rust panic ever crosses the C ABI. The crate builds with the default
`panic = "unwind"` strategy, and every `extern "C"` entry point runs its
body under `catch_unwind` (`ffi_guard` in `lib.rs`). A caught panic comes
back to the shim as `TH_RESOLVER_ERR_PANIC` with the panic message in
`th_resolver_last_error`, which the shim reports through `TF_WARN` and an
unresolved path. `panic = "abort"` is deliberately not used: it would turn
any core bug into a Houdini crash. Never panicking is still the contract,
and `tests/props.rs` pins it for the parser and for `resolve_uri`.

## Building

### Rust tests

```sh
cargo test
```

Runs the pure-Rust unit tests (URI parsing, version discovery, the FFI
guard), the integration tests (filesystem fixtures) and the property tests
in `tests/props.rs`. No USD / Houdini needed.

The properties generate URIs and version trees instead of using fixed
fixtures: the parser reads back exactly what a writer puts in the query (in
any key order, in either channel spelling), unknown keys and disagreeing
spellings are refused, "latest" is the numeric maximum, and latest mode
overrides a pinned version for every flavor.

Cross-language agreement is pinned from the Python side:
`tests/test_resolver_parity.py` (the minigun suite) builds
`examples/resolve_lines.rs` with cargo and checks that every path the
Python writers publish to is the path this crate resolves the recorded URI
to.

### Full plugin

From the repo root, the same script CI runs (`hpm run build-resolver`):

```sh
python .ci/build_resolver.py --platform windows-x86_64 --majors 22   # or linux-x86_64 / macos-aarch64
```

It finds the Houdini 22 install (HFS), runs CMake configure / build /
install into `resolver/houdini22/`, then checks the artifact's CPU
architecture and that the `TumbleResolver` class and the Rust FFI symbols
survived the link. On Windows, run it from a shell that has called
`vcvars64.bat`. By hand, the CMake steps are:

```sh
cmake -B build -S . -DHFS=/opt/hfs22.0           # linux
cmake -B build -S . -DHFS="C:/Program Files/Side Effects Software/Houdini 22.0.429"   # windows
cmake -B build -S . -DHFS=/Applications/Houdini/Houdini22.0.429/Frameworks/Houdini.framework/Versions/Current/Resources   # macos

cmake --build build --config Release
cmake --install build --prefix ../../resolver/houdini22
```

What happens under the hood:

1. CMake loads Houdini's CMake package from `$HFS/toolkit/cmake`.
2. `ExternalProject_Add` shells out to `cargo build --release` to produce
   the `th_resolver_core` staticlib into `build/rust-target/release/`.
   Builds are native: cargo follows the host, which is the target on
   every CI worker (the macOS one is Apple Silicon).
3. `tumbleResolver` (C++ MODULE library) is linked against `ar`, `arch`,
   `sdf`, `tf`, `vt` from USD, plus the Rust staticlib and the shim's own
   static library. Both are pulled in with platform-specific
   whole-archive flags (`/WHOLEARCHIVE` on MSVC, `-force_load` on Apple,
   `--whole-archive` on GNU): nothing references the shim's object by
   symbol, so without them the linker would never pull in the resolver
   factory registration.

   Resolver factory registration deliberately does **not** use
   `AR_DEFINE_RESOLVER`. That macro expands to a `TF_REGISTRY_FUNCTION`
   block that places an `extern const Arch_ConstructorEntry` in a
   `.pxrctor` PE section via `__declspec(allocate(".pxrctor"))`. On
   MSVC that data is unreferenced from anywhere else in the DLL, so
   link.exe drops it as dead — observed on MSVC 14.44 (VS 2022
   BuildTools) but not 14.50 (VS 2026 Community), so the bug ships in
   CI binaries while staying invisible in dev rebuilds. Factory never
   gets registered, USD's `_PluginResolver::Create` returns null with
   `Failed to manufacture asset resolver`, and `Ar.Resolve("entity:/...")`
   throws. We instead register via a regular C++ static initializer at
   the bottom of `resolver.cpp` that calls `Ar_DefineResolver<>()`
   directly — that goes through `.CRT$XCU` (standard CRT init), which
   every MSVC version preserves. Same registration outcome, robust
   against the section-data-elimination behaviour.
4. `plugInfo.json.in` is rendered with the platform-specific library
   filename: `tumbleResolver.so` / `tumbleResolver.dll`, no `lib` prefix,
   matching the target's `PREFIX ""`. Until v1.74.2 it was built from
   `CMAKE_SHARED_MODULE_PREFIX`, which is `lib` on Linux and macOS, so
   `LibraryPath` named a file that was never shipped there. USD skips a
   plugin it cannot load without raising, so every `entity:/` URI
   resolved to nothing. `.ci/build_resolver.py` now fails the build when
   a `LibraryPath` does not exist.
5. Install lays out `tumbleResolver/{lib,resources,BUILD_INFO}` at the
   install prefix.

The symbol checks: Linux and macOS grep `th_resolver` against the .so
(their `.dynsym` / Mach-O symbol table preserves `#[no_mangle]` names).
Windows can't do this against the DLL — MSVC `link.exe` drops the COFF
symbol table from a Release PE, so internal (non-exported) function names
disappear even when the code is linked in. The Windows build greps the
Rust staticlib instead; combined with a successful PE32+ link, that proves
cargo emitted the FFI surface and `link.exe` consumed it.

## Cache invalidation

The shim has no context overrides (`_BindContext`, `_RefreshContext`, ...).
`plugInfo.json` does not declare `implementsContexts`, so USD's
dispatching resolver never forwards context calls to it, and resolution
depends only on process env and the filesystem anyway.
`Ar.GetResolver().RefreshContext(...)` therefore does nothing for
`entity:` layers.

The Rust core keeps no cache (every `_Resolve` re-scans the filesystem);
the staleness that matters is USD's Sdf layer registry, which reuses the
originally-opened layer for an identifier forever. The pipeline's
mechanism for that is `tumblepipe.resolver.refresh_context()` (Python): it
re-resolves every loaded `entity://` layer and reloads the stale ones — see
that module's docstring and `scripts/verify_resolver_refresh.py`, which
pins the behaviour end-to-end.

## C ABI

The C++ shim sees only this surface (`cpp/th_resolver_core.h`). Return
codes are `ResolveError::code` (`src/error.rs`), mirrored as
`TH_RESOLVER_*` constants in the header; a unit test keeps the two in step.

```c
// 0 on success; TH_RESOLVER_ERR_BUFFER_TOO_SMALL leaves the needed size
// in *out_len so the caller can retry.
int32_t th_resolver_resolve(
    const char* uri, size_t uri_len,
    char* out_path, size_t out_cap, size_t* out_len);

// The calling thread's last error message (truncated to out_cap).
void th_resolver_last_error(char* out_msg, size_t out_cap, size_t* out_len);
```

## Contributing

Resolution rules are sole-sourced here. When you change a filename
template or a path layout, update the integration tests in
`tests/resolve_integration.rs` in the same PR, and run
`tests/test_resolver_parity.py` from the repo's minigun suite. It fails
when the Python writers (`tumblepipe.pipe.paths`) and this crate disagree
about where a layer lives.
