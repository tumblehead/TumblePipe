//! TumblePipe asset-resolver core.
//!
//! Native resolution logic for the `entity://` URI scheme. Exposed to the
//! C++ ArResolver shim (`cpp/`) via an `extern "C"` ABI. All logic lives
//! here; the C++ side is a thin translation layer.
//!
//! Panics never leave this crate. The crate builds with the default
//! `panic = "unwind"` strategy and every `extern "C"` entry point runs its
//! body under `catch_unwind` (`ffi_guard`), so a bug here becomes an error
//! code plus a `TF_WARN` in the host instead of either unwinding into C++
//! (undefined behaviour) or aborting Houdini.

#![warn(unsafe_op_in_unsafe_fn, clippy::undocumented_unsafe_blocks)]

pub mod env;
pub mod error;
pub mod resolve;
pub mod uri;
pub mod version;

pub use error::{ResolveError, ResolveResult};
pub use uri::EntityUri;

use std::any::Any;
use std::cell::RefCell;
use std::panic::{self, AssertUnwindSafe};

/// Top-level Rust entry point. Callable directly from Rust tests and
/// indirectly (via the C ABI below) from the C++ shim.
pub fn resolve_uri(uri: &str) -> ResolveResult<String> {
    let parsed = EntityUri::parse(uri)?;
    resolve::resolve(&parsed)
}

thread_local! {
    static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
}

fn stash_error(msg: impl Into<String>) {
    LAST_ERROR.with(|e| *e.borrow_mut() = msg.into());
}

/// Resolve an `entity:` URI to a filesystem path.
///
/// Returns 0 on success, or a negative `ResolveError::code`. On
/// `BufferTooSmall` (-6), `*out_len` holds the size needed.
///
/// # Safety
/// `uri` must point to `uri_len` readable bytes (or be null, read as an
/// empty uri).
/// `out_path` must point to at least `out_cap` writable bytes.
/// `out_len` must be a valid `size_t*`.
#[no_mangle]
pub unsafe extern "C" fn th_resolver_resolve(
    uri: *const u8,
    uri_len: usize,
    out_path: *mut u8,
    out_cap: usize,
    out_len: *mut usize,
) -> i32 {
    ffi_guard(|| {
        let uri_bytes: &[u8] = if uri.is_null() || uri_len == 0 {
            &[]
        } else {
            // SAFETY: non-null, `uri_len` readable bytes (the contract).
            unsafe { std::slice::from_raw_parts(uri, uri_len) }
        };
        let uri_str = std::str::from_utf8(uri_bytes).map_err(|e| ResolveError::Parse(format!("uri is not valid utf-8: {e}")))?;
        let resolved = resolve_uri(uri_str)?;
        // SAFETY: the out buffer and length pointer as the contract gives them.
        unsafe { write_out(&resolved, out_path, out_cap, out_len) }
    })
}

/// Copy the calling thread's most recent error message into `out_msg`,
/// truncated to `out_cap`. `*out_len` receives the full message length.
///
/// # Safety
/// `out_msg` must point to at least `out_cap` writable bytes.
/// `out_len` must be a valid `size_t*`.
#[no_mangle]
pub unsafe extern "C" fn th_resolver_last_error(out_msg: *mut u8, out_cap: usize, out_len: *mut usize) {
    // Nothing in here can panic in practice, but the same rule holds for
    // every entry point: no unwind may cross into C++.
    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
        LAST_ERROR.with(|e| {
            let msg = e.borrow();
            let bytes = msg.as_bytes();
            // SAFETY: `out_len` is a valid size_t* (the contract).
            unsafe { *out_len = bytes.len() };
            let n = bytes.len().min(out_cap);
            if !out_msg.is_null() && n > 0 {
                // SAFETY: `out_msg` has `out_cap` >= n writable bytes (the
                // contract), and cannot overlap the thread-local message.
                unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), out_msg, n) };
            }
        })
    }));
}

/// Run `body` as the whole of an FFI entry point: catch any panic (so it
/// never unwinds into the C++ caller) and convert errors to codes. On Ok,
/// returns 0. On Err or panic, stashes the message for
/// `th_resolver_last_error` and returns the negative code.
fn ffi_guard<F>(body: F) -> i32
where
    F: FnOnce() -> ResolveResult<()>,
{
    let result = panic::catch_unwind(AssertUnwindSafe(body)).unwrap_or_else(|payload| Err(ResolveError::Panic(panic_message(&*payload))));
    match result {
        Ok(()) => {
            stash_error("");
            0
        }
        Err(e) => {
            stash_error(e.to_string());
            e.code()
        }
    }
}

fn panic_message(payload: &(dyn Any + Send)) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        (*s).to_owned()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "non-string panic payload".to_owned()
    }
}

/// Write a UTF-8 string into an out buffer. `*out_len` always receives the
/// full length; on truncation nothing is written and `BufferTooSmall` is
/// returned so the caller can retry with a larger buffer.
///
/// # Safety
/// `out` must point to `cap` writable bytes; `out_len` must be valid.
unsafe fn write_out(s: &str, out: *mut u8, cap: usize, out_len: *mut usize) -> ResolveResult<()> {
    let bytes = s.as_bytes();
    // SAFETY: `out_len` is valid (the contract).
    unsafe { *out_len = bytes.len() };
    if bytes.len() > cap {
        return Err(ResolveError::BufferTooSmall { needed: bytes.len(), got: cap });
    }
    if !out.is_null() && !bytes.is_empty() {
        // SAFETY: `out` has `cap` >= bytes.len() writable bytes (the
        // contract), and cannot overlap `s`, which the caller owns.
        unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), out, bytes.len()) };
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ffi_guard_turns_a_panic_into_an_error_code() {
        let code = ffi_guard(|| panic!("boom"));
        assert_eq!(code, ResolveError::Panic(String::new()).code());
        LAST_ERROR.with(|e| assert!(e.borrow().contains("boom"), "{}", e.borrow()));
    }

    #[test]
    fn header_codes_mirror_resolve_error_codes() {
        // cpp/th_resolver_core.h hand-copies these; a drift would make the
        // shim misread a code (e.g. never take the buffer-retry path).
        let header = include_str!("../cpp/th_resolver_core.h");
        let cases = [
            ("TH_RESOLVER_ERR_PARSE", ResolveError::Parse(String::new())),
            ("TH_RESOLVER_ERR_MISSING_EXPORT_PATH", ResolveError::MissingExportPath),
            ("TH_RESOLVER_ERR_NO_VERSIONS", ResolveError::NoVersions { searched: String::new() }),
            ("TH_RESOLVER_ERR_EMPTY_SCENE_PATH", ResolveError::EmptyScenePath),
            ("TH_RESOLVER_ERR_INVALID_LATEST_MODE", ResolveError::InvalidLatestMode(String::new())),
            ("TH_RESOLVER_ERR_BUFFER_TOO_SMALL", ResolveError::BufferTooSmall { needed: 0, got: 0 }),
            ("TH_RESOLVER_ERR_PANIC", ResolveError::Panic(String::new())),
        ];
        for (name, err) in cases {
            let line = format!("{name} = {},", err.code());
            assert!(header.contains(&line), "header is missing `{line}`");
        }
    }

    #[test]
    fn write_out_reports_the_needed_size_when_the_buffer_is_short() {
        let mut len = 0usize;
        let mut buf = [0u8; 4];
        // SAFETY: a live 4-byte buffer and a live length.
        let code = unsafe { write_out("abcdefgh", buf.as_mut_ptr(), buf.len(), &mut len) }.map(|_| 0).unwrap_or_else(|e| e.code());
        assert_eq!(code, -6);
        assert_eq!(len, 8);
    }

    #[test]
    fn a_null_uri_is_an_empty_one_whatever_its_length() {
        let mut out = [0u8; 64];
        let mut len = 0usize;
        for uri_len in [0, 5, usize::MAX] {
            // SAFETY: a null uri, which the contract allows, and live out
            // buffers.
            let code = unsafe { th_resolver_resolve(std::ptr::null(), uri_len, out.as_mut_ptr(), out.len(), &mut len) };
            assert_eq!(code, ResolveError::Parse(String::new()).code(), "uri_len {uri_len}");
        }
    }
}
