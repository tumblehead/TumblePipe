//! The files a USD layer references, as `UsdUtils.ExtractExternalReferences`
//! gives them to the Python cleanup: sublayers, references, payloads,
//! asset-valued attributes (defaults, time samples, arrays), asset values in
//! metadata dictionaries, clips, in every prim and variant.
//!
//! Read with the pure-Rust `openusd` crate, `.usda` text and `.usdc` crate
//! files alike; every asset path the layer authors is visited
//! (`usd_utils::modify_asset_paths` with a visitor that changes nothing), so
//! what is listed is never less than what pxr lists. The format is chosen
//! as pxr chooses it, by extension: a `.usdc` must be a crate file and a
//! `.usda` text, only a `.usd` may be either; a layer pxr would refuse is
//! refused here too, and a layer that cannot be read stops the caches from
//! being deleted at all, as in Python.

use std::path::Path;

use openusd::sdf;

/// The crate file's first bytes.
const CRATE_MAGIC: &[u8] = b"PXR-USDC";
/// The text format's.
const TEXT_MAGIC: &[u8] = b"#usda ";

/// Every asset path the layer at `path` authors, each once. An error for a
/// layer that cannot be read or parsed.
pub fn layer_references(path: &Path) -> Result<Vec<String>, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("OSError: {e}"))?;
    let extension = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    let is_crate = bytes.starts_with(CRATE_MAGIC);
    match extension.as_str() {
        "usdc" if !is_crate => return Err("not a usdc crate file".into()),
        "usda" if !bytes.starts_with(TEXT_MAGIC) => return Err("not a usda text file".into()),
        "usd" if !is_crate && !bytes.starts_with(TEXT_MAGIC) => return Err("neither a usdc nor a usda file".into()),
        _ => {}
    }
    if !is_crate && let Some(problem) = text_problem(&bytes) {
        return Err(format!("UsdError: {problem}"));
    }
    let mut layer = sdf::Layer::from_bytes(path.display(), bytes).map_err(usd_error)?;
    let mut seen: Vec<String> = Vec::new();
    openusd::usd_utils::modify_asset_paths(
        &mut layer,
        |asset| {
            if !asset.is_empty() && !seen.iter().any(|s| s == asset) {
                seen.push(asset.to_string());
            }
            asset.to_string()
        },
        true,
    )
    .map_err(usd_error)?;
    Ok(seen)
}

/// A parse error on one line: `openusd`'s span its message over several
/// (a ` --> file:line` pointer), which would break the listing's lines.
fn usd_error(error: impl std::fmt::Display) -> String {
    format!("UsdError: {}", error.to_string().split_whitespace().collect::<Vec<_>>().join(" "))
}

/// What pxr's text parser refuses that `openusd`'s reads anyway: two asset
/// paths one after the other with no comma between them (`[@a@ @b@]`,
/// which `openusd` reads as a list). Refusing such a layer keeps every
/// cache, as Python does; a quoted string holding `@` may be refused too,
/// which only keeps more.
fn text_problem(bytes: &[u8]) -> Option<&'static str> {
    let mut at = 0;
    let mut after_asset = false;
    while at < bytes.len() {
        let b = bytes[at];
        if b == b'@' {
            if after_asset {
                return Some("two asset paths without a comma between them");
            }
            // `@@@...@@@` or `@...@`.
            let (open, close): (&[u8], &[u8]) = if bytes[at..].starts_with(b"@@@") { (b"@@@", b"@@@") } else { (b"@", b"@") };
            let body = at + open.len();
            let end = bytes[body..].windows(close.len()).position(|w| w == close)?;
            at = body + end + close.len();
            after_asset = true;
            continue;
        }
        if b == b'#' {
            // A comment runs to the end of its line.
            at += bytes[at..].iter().position(|c| *c == b'\n').unwrap_or(bytes.len() - at);
            continue;
        }
        if !b.is_ascii_whitespace() {
            after_asset = false;
        }
        at += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_text_layer_lists_every_asset_path() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.usda");
        std::fs::write(
            &path,
            "#usda 1.0\n(\n    subLayers = [@../sub/cache/smoke/v0003/x.usd@]\n)\n\
             over \"World\" (\n    prepend references = @./cache/a/v0001/ref.usd@</Foo>\n    prepend payload = @/abs/lops_cache/b/v0002/p.usdc@\n)\n\
             {\n    asset file = @rel/cache/c/v0004/f.bgeo.sc@\n    asset[] files = [@a1.bgeo@, @a2.bgeo@]\n    string note = \"@not/cache/n/v0001@\"\n}\n",
        )
        .unwrap();
        let mut refs = layer_references(&path).unwrap();
        refs.sort();
        assert_eq!(
            refs,
            [
                "../sub/cache/smoke/v0003/x.usd",
                "./cache/a/v0001/ref.usd",
                "/abs/lops_cache/b/v0002/p.usdc",
                "a1.bgeo",
                "a2.bgeo",
                "rel/cache/c/v0004/f.bgeo.sc"
            ]
        );
    }

    #[test]
    fn a_layer_pxr_refuses_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        for (name, data) in [
            ("bad.usda", &b"garbage"[..]),
            ("empty.usd", b""),
            ("text.usdc", b"#usda 1.0\n"),
            ("short.usdc", b"PXR-USDC\x00\x00x"),
            ("nocomma.usda", b"#usda 1.0\n(\n    subLayers = [\n        @a.usd@\n        @b.usd@\n    ]\n)\n"),
        ] {
            let path = dir.path().join(name);
            std::fs::write(&path, data).unwrap();
            assert!(layer_references(&path).is_err(), "{name}");
        }
    }
}
