# TumblePipe asset resolver

USD asset resolver for TumblePipe's `entity://` URI scheme. Rust core +
thin C++ ArResolver shim; produces a plugin loadable directly by
Houdini's bundled USD.

## Why this exists

This crate owns `entity://` URI resolution for TumblePipe. CI builds
the plugin per `(platform, houdini_major)` cell and drops the binaries
into `../resolver/<platform>/houdini<major>/tumbleResolver/`. The
pipeline's Python side (`tumblepipe.resolver`) is now a thin façade
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
cmake --install build --prefix ../resolver/<platform>/houdini21
```

What happens under the hood:

1. CMake loads Houdini's bundled USD CMake package from `$HFS/toolkit/cmake`.
2. `ExternalProject_Add` shells out to `cargo build --release` to produce
   the `th_resolver_core` staticlib into `build/rust-target/release/`.
3. `tumbleResolver` (C++ MODULE library) is linked against `ar`, `sdf`,
   `tf` from USD, plus the Rust staticlib.
4. `plugInfo.json.in` is rendered with the platform-specific library
   filename.
5. Install lays out `tumbleResolver/{lib,resources,BUILD_INFO}` at the
   install prefix, ready to drop into the package's `resolver/<platform>/
   houdini<major>/` directory.

CI (`.woodpecker/resolver.yml`, coming in a follow-up) runs this for
each cell in the platform × houdini-major matrix on workers that have
the corresponding Houdini install.

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
