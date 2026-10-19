use std::fmt;

pub type ResolveResult<T> = Result<T, ResolveError>;

#[derive(Debug)]
pub enum ResolveError {
    /// URI did not parse as an `entity:` URI.
    Parse(String),
    /// `TH_EXPORT_PATH` is unset or empty.
    MissingExportPath,
    /// No `vNNNN` directory found where latest was requested.
    NoVersions { searched: String },
    /// Scene URI had no segments after `scenes/`.
    EmptyScenePath,
    /// `TH_RESOLVER_LATEST_MODE` holds something other than `0` / `1`.
    InvalidLatestMode(String),
    /// The caller's output buffer cannot hold the result; `out_len`
    /// carries the size needed so the caller can retry.
    BufferTooSmall { needed: usize, got: usize },
    /// The core panicked. Caught at the C ABI boundary (see `ffi_guard`).
    Panic(String),
}

impl ResolveError {
    /// Errno-style code returned across FFI. Mirrored in
    /// `cpp/th_resolver_core.h`.
    pub fn code(&self) -> i32 {
        match self {
            ResolveError::Parse(_) => -1,
            ResolveError::MissingExportPath => -2,
            ResolveError::NoVersions { .. } => -3,
            ResolveError::EmptyScenePath => -4,
            ResolveError::InvalidLatestMode(_) => -5,
            ResolveError::BufferTooSmall { .. } => -6,
            ResolveError::Panic(_) => -7,
        }
    }
}

impl fmt::Display for ResolveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ResolveError::Parse(m) => write!(f, "parse: {m}"),
            ResolveError::MissingExportPath => write!(f, "TH_EXPORT_PATH is not set"),
            ResolveError::NoVersions { searched } => {
                write!(f, "no versions found at: {searched}")
            }
            ResolveError::EmptyScenePath => {
                write!(f, "scene URI has no segments after 'scenes/'")
            }
            ResolveError::InvalidLatestMode(v) => write!(
                f,
                "TH_RESOLVER_LATEST_MODE must be \"0\" or \"1\", got {v:?}"
            ),
            ResolveError::BufferTooSmall { needed, got } => {
                write!(f, "output buffer too small: need {needed} got {got}")
            }
            ResolveError::Panic(m) => write!(f, "panic in th_resolver_core: {m}"),
        }
    }
}

impl std::error::Error for ResolveError {}
