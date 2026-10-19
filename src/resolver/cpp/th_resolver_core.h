#pragma once

/// C ABI exposed by the Rust `th_resolver_core` crate.
///
/// All string buffers are UTF-8. `out_len` receives the full length even
/// when `out_cap` was too small, so callers can retry with a larger
/// buffer. No Rust panic ever unwinds out of these functions: the core
/// catches it and returns TH_RESOLVER_ERR_PANIC. Error detail for the
/// calling thread is retrievable via `th_resolver_last_error`.

#include <cstddef>
#include <cstdint>

#ifdef __cplusplus
extern "C" {
#endif

// Return codes; mirror `ResolveError::code` in src/error.rs.
enum {
    TH_RESOLVER_OK = 0,
    TH_RESOLVER_ERR_PARSE = -1,
    TH_RESOLVER_ERR_MISSING_EXPORT_PATH = -2,
    TH_RESOLVER_ERR_NO_VERSIONS = -3,
    TH_RESOLVER_ERR_EMPTY_SCENE_PATH = -4,
    TH_RESOLVER_ERR_INVALID_LATEST_MODE = -5,
    TH_RESOLVER_ERR_BUFFER_TOO_SMALL = -6,
    TH_RESOLVER_ERR_PANIC = -7,
};

int32_t th_resolver_resolve(
    const char* uri,
    size_t uri_len,
    char* out_path,
    size_t out_cap,
    size_t* out_len);

void th_resolver_last_error(
    char* out_msg,
    size_t out_cap,
    size_t* out_len);

#ifdef __cplusplus
}
#endif
