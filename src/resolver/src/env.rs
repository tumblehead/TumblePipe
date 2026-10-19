//! Process-environment inputs to resolution. Both are read on every resolve
//! (no caching), so a change made by the Python facade takes effect on the
//! next `Ar.Resolve()`.

use std::path::PathBuf;

use crate::error::{ResolveError, ResolveResult};

pub const EXPORT_PATH_VAR: &str = "TH_EXPORT_PATH";
pub const LATEST_MODE_VAR: &str = "TH_RESOLVER_LATEST_MODE";

pub fn export_base() -> Option<PathBuf> {
    std::env::var_os(EXPORT_PATH_VAR)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// Latest mode is on when `TH_RESOLVER_LATEST_MODE` is `"1"` and off when it
/// is `"0"` or unset: the only values `tumblepipe.resolver.set_latest_mode`
/// writes. Anything else is refused rather than read as "off", since a stray
/// `true`/`yes` would otherwise silently resolve pinned versions.
pub fn latest_mode() -> ResolveResult<bool> {
    match std::env::var_os(LATEST_MODE_VAR) {
        None => Ok(false),
        Some(v) if v == "0" => Ok(false),
        Some(v) if v == "1" => Ok(true),
        Some(v) => Err(ResolveError::InvalidLatestMode(
            v.to_string_lossy().into_owned(),
        )),
    }
}
