use std::path::Path;

/// Find the highest `v\d+` directory entry under `root`. Returns `None`
/// if the directory doesn't exist or contains no version dirs.
pub fn find_latest(root: &Path) -> Option<String> {
    let entries = std::fs::read_dir(root).ok()?;
    let mut best: Option<(u64, String)> = None;

    for entry in entries.flatten() {
        if !entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let name = match entry.file_name().into_string() {
            Ok(s) => s,
            Err(_) => continue,
        };
        if let Some(n) = parse_v(&name) {
            match &best {
                Some((b, _)) if *b >= n => {}
                _ => best = Some((n, name)),
            }
        }
    }

    best.map(|(_, name)| name)
}

fn parse_v(name: &str) -> Option<u64> {
    let digits = name.strip_prefix('v')?;
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    digits.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_v_rules() {
        assert_eq!(parse_v("v0001"), Some(1));
        assert_eq!(parse_v("v42"), Some(42));
        assert_eq!(parse_v("V42"), None);
        assert_eq!(parse_v("v"), None);
        assert_eq!(parse_v("v42x"), None);
        assert_eq!(parse_v("42"), None);
    }

    #[test]
    fn missing_dir_returns_none() {
        let td = tempfile::tempdir().unwrap();
        assert!(find_latest(&td.path().join("nope")).is_none());
    }

    #[test]
    fn picks_highest_not_lexicographic() {
        let td = tempfile::tempdir().unwrap();
        for v in ["v0001", "v0002", "v0013", "v0007"] {
            std::fs::create_dir(td.path().join(v)).unwrap();
        }
        assert_eq!(find_latest(td.path()).as_deref(), Some("v0013"));
    }

    #[test]
    fn ignores_non_version_entries() {
        let td = tempfile::tempdir().unwrap();
        std::fs::create_dir(td.path().join("notes")).unwrap();
        std::fs::create_dir(td.path().join("v0001")).unwrap();
        std::fs::write(td.path().join("v0002"), b"").unwrap(); // file, not dir
        assert_eq!(find_latest(td.path()).as_deref(), Some("v0001"));
    }
}
