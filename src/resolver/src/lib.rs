//! TumblePipe asset-resolver core.
//!
//! Native resolution logic for the `entity://` URI scheme. Exposed to the
//! C++ ArResolver shim (`cpp/`) via an `extern "C"` ABI. All logic lives
//! here; the C++ side is a thin translation layer.

pub mod env;
pub mod error;
pub mod resolve;
pub mod uri;
pub mod version;

pub use error::{ResolveError, ResolveResult};
pub use uri::EntityUri;

use std::cell::RefCell;
use std::panic::{self, AssertUnwindSafe};
use std::time::{SystemTime, UNIX_EPOCH};

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
/// # Safety
/// `uri` must point to `uri_len` valid UTF-8 bytes.
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
        let uri_bytes = std::slice::from_raw_parts(uri, uri_len);
        let uri_str = std::str::from_utf8(uri_bytes).map_err(|e| {
            ResolveError::Parse(format!("uri is not valid utf-8: {e}"))
        })?;
        let resolved = resolve_uri(uri_str)?;
        write_out(&resolved, out_path, out_cap, out_len)
    })
}

/// Mirror of `th_resolver_resolve` for modification timestamps. Returns
/// (seconds, nanos) since UNIX epoch. The identifier passed in must be
/// a fully resolved filesystem path (what `_Resolve` produced) — not an
/// `entity:` URI — matching USD's `_GetModificationTimestamp` contract.
///
/// # Safety
/// `identifier` must point to `identifier_len` valid UTF-8 bytes.
/// `out_seconds` and `out_nanos` must be valid pointers.
#[no_mangle]
pub unsafe extern "C" fn th_resolver_get_timestamp(
    identifier: *const u8,
    identifier_len: usize,
    out_seconds: *mut i64,
    out_nanos: *mut i32,
) -> i32 {
    ffi_guard(|| {
        let bytes = std::slice::from_raw_parts(identifier, identifier_len);
        let path_str = std::str::from_utf8(bytes).map_err(|e| {
            ResolveError::Parse(format!("path is not valid utf-8: {e}"))
        })?;
        let meta = std::fs::metadata(path_str).map_err(|e| {
            ResolveError::Parse(format!("stat failed for {path_str}: {e}"))
        })?;
        let modified = meta.modified().map_err(|e| {
            ResolveError::Parse(format!("no mtime for {path_str}: {e}"))
        })?;
        let dur = modified
            .duration_since(UNIX_EPOCH)
            .unwrap_or_else(|_| SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default());
        *out_seconds = dur.as_secs() as i64;
        *out_nanos = dur.subsec_nanos() as i32;
        Ok(0)
    })
}

/// Copy the most recent thread-local error message into `out_msg`.
///
/// # Safety
/// `out_msg` must point to at least `out_cap` writable bytes.
/// `out_len` must be a valid `size_t*`.
#[no_mangle]
pub unsafe extern "C" fn th_resolver_last_error(
    out_msg: *mut u8,
    out_cap: usize,
    out_len: *mut usize,
) {
    LAST_ERROR.with(|e| {
        let msg = e.borrow();
        let _ = write_out(&msg, out_msg, out_cap, out_len);
    });
}

/// Run `body`, catching panics and converting errors to codes. On Ok,
/// returns the inner i32 (typically 0). On Err, stashes the error for
/// `th_resolver_last_error` and returns the negative code.
fn ffi_guard<F>(body: F) -> i32
where
    F: FnOnce() -> ResolveResult<i32>,
{
    match panic::catch_unwind(AssertUnwindSafe(body)) {
        Ok(Ok(code)) => {
            stash_error("");
            code
        }
        Ok(Err(e)) => {
            let code = e.code();
            stash_error(e.to_string());
            code
        }
        Err(_) => {
            stash_error("panic in th_resolver (see stderr)");
            i32::MIN
        }
    }
}

/// Write a UTF-8 string into an out buffer. On truncation returns
/// `ResolveError::Parse` with the required size so the caller can
/// retry with a larger buffer.
///
/// # Safety
/// `out` must point to `cap` writable bytes; `out_len` must be valid.
unsafe fn write_out(
    s: &str,
    out: *mut u8,
    cap: usize,
    out_len: *mut usize,
) -> ResolveResult<i32> {
    let bytes = s.as_bytes();
    *out_len = bytes.len();
    if bytes.len() > cap {
        return Err(ResolveError::Parse(format!(
            "output buffer too small: need {} got {cap}",
            bytes.len()
        )));
    }
    if !out.is_null() && !bytes.is_empty() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), out, bytes.len());
    }
    Ok(0)
}
