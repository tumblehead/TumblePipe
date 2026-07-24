use std::path::PathBuf;

pub const EXPORT_PATH_VAR: &str = "TH_EXPORT_PATH";
pub const LATEST_MODE_VAR: &str = "TH_RESOLVER_LATEST_MODE";

pub fn export_base() -> Option<PathBuf> {
    std::env::var_os(EXPORT_PATH_VAR)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

pub fn latest_mode() -> bool {
    matches!(
        std::env::var(LATEST_MODE_VAR).as_deref(),
        Ok("1") | Ok("true") | Ok("True")
    )
}
