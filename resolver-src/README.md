# TumblePipe asset resolver

USD asset resolver for TumblePipe's `entity://` URI scheme. Rust core +
thin C++ ArResolver shim; produces a plugin loadable directly by
Houdini's bundled USD.

## Why this exists

This crate owns `entity://` URI resolution for TumblePipe. CI builds
the plugin per `(platform, houdini_major)` cell and drops the binaries
into `../resolver/houdini<major>/tumbleResolver/`. Each per-platform
release archive contains exactly one platform's binaries at that flat
path, which `hpm.toml [env]` then registers with USD via
`PXR_PLUGINPATH_NAME` (the path is a constant string because Houdini
package.json env values can't substitute platform-specific variables).
The pipeline's Python side (`tumblepipe.resolver`) is now a thin façade
that toggles env vars and calls `Ar.GetResolver().Resolve()` — all
resolution decisions happen here in Rust.

(Historically the resolver was a binary copy of LucaScheller's
CachedResolver with rules implemented in Python, dispatched from C++
via `TfPyInvoke`. That setup had two sources of truth, hand-delivered
binaries, and a Python/C++ crossing per resolve. This crate replaces
all three.)

## Layout

```
resolver-src/
├── Cargo.toml             # crate manifest
├── CMakeLists.txt         # orchestrates cargo + C++ shim + install layout
├── houdini_majors.toml    # CI matrix: Houdini majors to build against
├── src/                   # Rust core
│   ├── lib.rs             # C ABI entry points
│   ├── uri.rs             # entity://  parser
│   ├── resolve.rs         # the four flavors: department / staged / root / scene
│   ├── version.rs         # "latest" auto-discovery
│   ├── env.rs             # $TH_EXPORT_PATH + $TH_RESOLVER_LATEST_MODE
│   └── error.rs
├── tests/
│   └── resolve_integration.rs   # tempdir fixtures per flavor
└── cpp/                   # ArResolver shim (URI-scheme resolver for entity://)
    ├── resolver.h
    ├── resolver.cpp
    ├── th_resolver_core.h # C ABI declarations exported by the Rust staticlib
    └── plugInfo.json.in
```

## URI scheme

Scheme: `entity:`. Four flavors routed by query parameters:

| Flavor     | Example                                                           | Resolves to                                                                   |
|------------|-------------------------------------------------------------------|-------------------------------------------------------------------------------|
| Department | `entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013` | `$TH_EXPORT_PATH/assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd` |
| Staged     | `entity:/assets/CHAR/Crowd?variant=cheering`                      | `$TH_EXPORT_PATH/assets/CHAR/Crowd/_staged/cheering/v<latest>/CHAR_Crowd_v<latest>.usda` |
| Root       | `entity:/shots/sq050/sh446?dept=root&version=v0006`               | `$TH_EXPORT_PATH/shots/sq050/sh446/_root/v0006/shots_sq050_sh446_root_v0006.usda`        |
| Scene      | `entity:/scenes/arena?version=v0001`                              | `$TH_EXPORT_PATH/scenes/arena/_staged/v0001/arena_v0001.usda`                            |

Env inputs:

- `TH_EXPORT_PATH` *(required)* — base directory for all resolved paths.
- `TH_RESOLVER_LATEST_MODE` — when `1`/`true`, explicit `version=` params
  are ignored and the filesystem is scanned for the highest `vNNNN`.

## Building

### Rust tests

```sh
cargo test --release
```

Runs the pure-Rust unit tests (URI parsing, version discovery) and the
integration tests (filesystem fixtures). No USD / Houdini needed.

### Full plugin

```sh
cmake -B build -S . -DHFS=/opt/hfs21.0           # linux
cmake -B build -S . -DHFS="C:/Program Files/Side Effects Software/Houdini 21.0.100"   # windows
cmake -B build -S . -DHFS=/Applications/Houdini/Houdini21.0.100/Frameworks/Houdini.framework/Versions/Current/Resources   # macos

cmake --build build --config Release
cmake --install build --prefix ../resolver/houdini21
```

What happens under the hood:

1. CMake loads Houdini's bundled USD CMake package from `$HFS/toolkit/cmake`.
2. `ExternalProject_Add` shells out to `cargo build --release` to produce
   the `th_resolver_core` staticlib into `build/rust-target/release/`.
3. `tumbleResolver` (C++ MODULE library) is linked against `ar`, `arch`,
   `sdf`, `tf`, `vt` from USD, plus the Rust staticlib. The staticlib is
   pulled in with platform-specific whole-archive flags (`/WHOLEARCHIVE`
   on MSVC, `-force_load` on Apple, `--whole-archive` on GNU) so linker
   dead-code passes can't drop static ctors. The `TumbleResolver` class
   also carries a `TH_RESOLVER_API` export annotation for the same reason.
4. `plugInfo.json.in` is rendered with the platform-specific library
   filename.
5. Install lays out `tumbleResolver/{lib,resources,BUILD_INFO}` at the
   install prefix, ready to drop into the package's
   `resolver/houdini<major>/` directory.

CI runs this per-platform from `.woodpecker/build-windows.yml`,
`build-linux.yml`, and `build-macos.yml` on workers that have the
corresponding Houdini install. Each build smoke-tests the resulting
binary with `grep TumbleResolver` to catch the linker-strip regression
that would otherwise ship a plugin USD can load but can't instantiate.

Linux and macOS additionally `grep th_resolver` against the .so to
verify the Rust FFI symbols survived (their `.dynsym` / Mach-O symbol
table preserves `#[no_mangle]` names). Windows can't do this against
the DLL — MSVC `link.exe` drops the COFF symbol table from a Release
PE, so internal (non-exported) function names disappear even when the
code is linked in. The Windows job greps the Rust staticlib instead;
combined with a successful PE32+ link, that proves cargo emitted the
FFI surface and `link.exe` consumed it.

## C ABI

The C++ shim sees only this surface. Every entry point catches panics
and returns errno-style codes; error detail is retrieved via
`th_resolver_last_error`.

```c
int32_t th_resolver_resolve(
    const char* uri, size_t uri_len,
    char* out_path, size_t out_cap, size_t* out_len);

int32_t th_resolver_get_timestamp(
    const char* identifier, size_t identifier_len,
    int64_t* out_seconds, int32_t* out_nanos);

void th_resolver_last_error(char* out_msg, size_t out_cap, size_t* out_len);
```

## Contributing

Resolution rules are sole-sourced here. When you change a filename
template or a path layout, update the integration tests in
`tests/resolve_integration.rs` in the same PR — they're the only
guardrail against pipeline-wide path mismatches, so keep them honest.
