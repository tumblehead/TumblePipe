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
    /// Caller passed a URI that isn't in the `entity:` scheme; only
    /// returned from `resolve_entity_uri` as a programmer-error signal.
    NotEntityScheme,
}

impl ResolveError {
    /// Errno-style code returned across FFI.
    pub fn code(&self) -> i32 {
        match self {
            ResolveError::Parse(_) => -1,
            ResolveError::MissingExportPath => -2,
            ResolveError::NoVersions { .. } => -3,
            ResolveError::EmptyScenePath => -4,
            ResolveError::NotEntityScheme => -5,
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
            ResolveError::EmptyScenePath => write!(f, "scene URI has no segments after 'scenes/'"),
            ResolveError::NotEntityScheme => write!(f, "URI is not in the entity: scheme"),
        }
    }
}

impl std::error::Error for ResolveError {}
