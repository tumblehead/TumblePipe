#pragma once

/// C ABI exposed by the Rust `th_resolver_core` crate.
///
/// All string buffers are UTF-8. `out_len` receives the required size
/// even if `out_cap` was too small, so callers can retry with a larger
/// buffer. All entry points catch Rust panics and return errno-style
/// codes; error detail is retrievable via `th_resolver_last_error`.

#include <cstddef>
#include <cstdint>

#ifdef __cplusplus
extern "C" {
#endif

int32_t th_resolver_resolve(
    const char* uri,
    size_t uri_len,
    char* out_path,
    size_t out_cap,
    size_t* out_len);

int32_t th_resolver_get_timestamp(
    const char* identifier,
    size_t identifier_len,
    int64_t* out_seconds,
    int32_t* out_nanos);

void th_resolver_last_error(
    char* out_msg,
    size_t out_cap,
    size_t* out_len);

#ifdef __cplusplus
}
#endif
