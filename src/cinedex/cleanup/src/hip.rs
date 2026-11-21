//! What a plain `.hip` and a staged `.usda` say, as `prune_exports` reads
//! them: a node's parms one `name [ ... ] ( value )` line each, the
//! `entity:/` URIs in a value, the version folders a ref names, and which
//! workfile is the newest of each department folder.
//!
//! The Python reads these with regular expressions; each function here
//! names the one it is and matches what it matches, lookarounds and all.

use std::path::{Path, PathBuf};

use th_pipe::naming::is_valid_version_name;

use crate::fsx;
use crate::inventory::{version_code, workfile_version};
use crate::py;
use crate::roots::ProjectRoots;

pub const STAGED: &str = "_staged";
pub const ROOT: &str = "_root";

/// Folders inside a workfile folder that hold no workfiles
/// (`prune_exports._NOT_WORKFILE_DIRS`).
const NOT_WORKFILE_DIRS: [&str; 7] = ["cache", "lops_cache", "backup", "render", "galleries", "_context", "__pycache__"];

/// `re`'s `\w`: letters, digits and `_`.
fn is_word(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

/// `_PARM`: `^(\w+)\t\[[^\]]*\]\t\(\t(.*)\t\)\s*$` on one line, the name
/// and the value as the callers take it (`.strip().strip('"')`).
pub fn parm(line: &str) -> Option<(&str, &str)> {
    let name_end = line.find(|c: char| !is_word(c)).unwrap_or(line.len());
    if name_end == 0 {
        return None;
    }
    let name = &line[..name_end];
    let rest = line[name_end..].strip_prefix("\t[")?;
    let close = rest.find(']')?;
    let rest = rest[close + 1..].strip_prefix("\t(\t")?;
    // `(.*)` takes the most it can: the last `\t)` with only whitespace after.
    let found = rest.rmatch_indices("\t)").map(|(at, _)| at).find(|at| rest[at + 2..].chars().all(py::is_space))?;
    let value = &rest[..found];
    Some((name, py::strip_chars(py::strip(value), "\"")))
}

/// `_ENTITY.findall`: `entity:/((?:assets|shots)/[A-Za-z0-9_./-]+?)(?=[?"\s@]|$)`.
pub fn entities(value: &str) -> Vec<&str> {
    let run = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-');
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(found) = value[from..].find("entity:/") {
        let start = from + found + "entity:/".len();
        let body = &value[start..];
        let head = if body.starts_with("assets/") {
            "assets/".len()
        } else if body.starts_with("shots/") {
            "shots/".len()
        } else {
            from = from + found + 1;
            continue;
        };
        let tail = &body[head..];
        let length = tail.find(|c: char| !run(c)).unwrap_or(tail.len());
        let next = tail[length..].chars().next();
        let ends = match next {
            None => true,
            Some(c) => matches!(c, '?' | '"' | '@') || py::is_space(c),
        };
        if length > 0 && ends {
            out.push(&body[..head + length]);
            from = start + head + length;
        } else {
            from = from + found + 1;
        }
    }
    out
}

/// `_PINNED_URI.findall`: `entity:/[^\s"@<>]+`.
pub fn pinned_uris(value: &str) -> Vec<&str> {
    let stop = |c: char| py::is_space(c) || matches!(c, '"' | '@' | '<' | '>');
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(found) = value[from..].find("entity:/") {
        let start = from + found;
        let body = &value[start + "entity:/".len()..];
        let length = body.find(stop).unwrap_or(body.len());
        if length > 0 {
            let end = start + "entity:/".len() + length;
            out.push(&value[start..end]);
            from = end;
        } else {
            from = start + 1;
        }
    }
    out
}

/// An `entity:/` sublayer ref (`pipe.usd.sublayer_refs.SublayerRef`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SublayerRef {
    pub base: String,
    pub department: Option<String>,
    pub channel: String,
    pub version: Option<String>,
}

/// `parse_entity_sublayer_uri`: None for anything not an entity URI; an
/// error when it names its channel twice, differently (both spellings,
/// `channel` and `variant`), as Python's `read_published_channel` raises.
pub fn parse_entity_sublayer_uri(uri: &str) -> Result<Option<SublayerRef>, String> {
    if !uri.starts_with("entity:/") {
        return Ok(None);
    }
    let (base, query) = uri.split_once('?').unwrap_or((uri, ""));
    let mut params: Vec<(&str, &str)> = Vec::new();
    if !query.is_empty() {
        for param in query.split('&') {
            if let Some((key, value)) = param.split_once('=') {
                match params.iter_mut().find(|(k, _)| *k == key) {
                    Some(slot) => slot.1 = value,
                    None => params.push((key, value)),
                }
            }
        }
    }
    let get = |key: &str| params.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string());
    let (new, old) = (get("channel"), get("variant"));
    if let (Some(new), Some(old)) = (&new, &old)
        && new != old
    {
        return Err(format!(
            "AmbiguousChannelError: sublayer ref {uri:?} carries both 'variant'={old:?} and 'channel'={new:?}, which name different channels."
        ));
    }
    let channel = new.or(old).unwrap_or_else(|| "default".into());
    Ok(Some(SublayerRef { base: base.to_string(), department: get("dept"), channel, version: get("version") }))
}

/// `prune_exports._unit`: the version folder a path under the export root
/// is in (its parts up to the first version name).
pub fn unit_of(parts: &[String]) -> Option<Vec<String>> {
    parts.iter().position(|p| is_valid_version_name(p)).map(|at| parts[..=at].to_vec())
}

/// `prune_exports.ref_units`: the version folders a ref names (at most
/// one), as parts under the export root; `file_dir` is the folder of the
/// file holding a relative ref.
pub fn ref_units(reference: &str, file_dir: &[String]) -> Result<Vec<Vec<String>>, String> {
    if reference.starts_with("entity:/") {
        let Some(parsed) = parse_entity_sublayer_uri(reference)? else { return Ok(Vec::new()) };
        let Some(version) = parsed.version.filter(|v| !v.is_empty()) else { return Ok(Vec::new()) };
        let mut unit: Vec<String> = parsed.base["entity:/".len()..].split('/').filter(|s| !s.is_empty()).map(String::from).collect();
        match parsed.department.as_deref() {
            None => unit.extend([STAGED.to_string(), parsed.channel, version]),
            Some("root") => unit.extend([ROOT.to_string(), version]),
            Some(department) => unit.extend([parsed.channel, department.to_string(), version]),
        }
        return Ok(vec![unit]);
    }
    let first = reference.split('/').next().unwrap_or("");
    if first.contains(':') || reference.starts_with('/') || reference.starts_with('\\') {
        return Ok(Vec::new());
    }
    let mut parts: Vec<String> = file_dir.to_vec();
    for step in reference.split(['\\', '/']) {
        match step {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return Ok(Vec::new());
                }
            }
            _ => parts.push(step.to_string()),
        }
    }
    Ok(unit_of(&parts).into_iter().collect())
}

/// `_REF.finditer` over a `.usda`'s bytes: `@([^@\r\n]+)@`, each decoded.
pub fn usda_refs(data: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut at = 0;
    while at < data.len() {
        if data[at] != b'@' {
            at += 1;
            continue;
        }
        let body = &data[at + 1..];
        let length = body.iter().position(|b| matches!(b, b'@' | b'\r' | b'\n')).unwrap_or(body.len());
        if length > 0 && body.get(length) == Some(&b'@') {
            out.push(String::from_utf8_lossy(&body[..length]).into_owned());
            at += length + 2;
        } else {
            at += 1;
        }
    }
    out
}

/// One department folder's newest workfile.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LatestWorkfile {
    pub purpose: String,
    pub parts: Vec<String>,
    pub path: PathBuf,
}

/// `prune_exports.latest_workfiles`: the newest workfile in every folder of
/// the assets, shots and groups roots.
pub fn latest_workfiles(roots: &ProjectRoots) -> Vec<LatestWorkfile> {
    let mut found = Vec::new();
    for purpose in ["assets", "shots", "groups"] {
        let Some(root) = roots.get(purpose) else { continue };
        if !root.path.is_dir() {
            continue;
        }
        let others: Vec<&Path> = roots.roots.iter().filter(|r| !fsx::same_path(&r.path, &root.path)).map(|r| r.path.as_path()).collect();
        fsx::os_walk(&root.path, |dir| {
            let here = dir.path.to_path_buf();
            dir.dirs.retain(|d| {
                !NOT_WORKFILE_DIRS.contains(&d.as_str()) && !d.starts_with('.') && !others.iter().any(|o| fsx::same_path(o, &here.join(d)))
            });
            let mut newest: Option<(i128, &String)> = None;
            for name in dir.files {
                let Some(version) = workfile_version(name) else { continue };
                let code = version_code(&version);
                if newest.is_none_or(|(best, _)| code > best) {
                    newest = Some((code, name));
                }
            }
            if let Some((_, name)) = newest {
                let parts = here
                    .strip_prefix(&root.path)
                    .map(|rel| rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect())
                    .unwrap_or_default();
                found.push(LatestWorkfile { purpose: purpose.into(), parts, path: here.join(name) });
            }
        });
    }
    found
}

/// `prune_exports._workfile_entity`: the entity a workfile folder
/// (`<entity>/<department>`) is for, as export parts; None for a group's.
pub fn workfile_entity(purpose: &str, parts: &[String]) -> Option<Vec<String>> {
    if !matches!(purpose, "shots" | "assets") || parts.len() < 2 {
        return None;
    }
    let mut out = vec![purpose.to_string()];
    out.extend(parts[..parts.len() - 1].iter().cloned());
    Some(out)
}

/// `archive.hipfile.readable`: whether a workfile's bytes are read. A
/// `.hip` always; a `.hiplc` / `.hipnc` when it starts with a licensed
/// section header (`HouLC`/`HouNC`, 0x1a, 8-64 hex digits, a 1-256 byte
/// section name, NUL) and the first 64 bytes after it are plain text: the
/// same text sections as a `.hip`'s, each behind a header of its own in
/// place of cpio's. Any other form is not read, and counted unreadable.
pub fn readable(bytes: &[u8], name: &str) -> bool {
    let lower = name.to_lowercase();
    let suffix = lower.rsplit_once('.').map(|(_, s)| s).unwrap_or("");
    if suffix == "hip" {
        return true;
    }
    if suffix != "hiplc" && suffix != "hipnc" {
        return false;
    }
    let Some(end) = licensed_header(bytes) else { return false };
    let mut first = &bytes[end..bytes.len().min(end + 64)];
    // A short first section ends at the next section's header.
    for magic in [b"HouLC\x1a", b"HouNC\x1a"] {
        if let Some(at) = first.windows(magic.len()).position(|w| w == magic) {
            first = &first[..at];
        }
    }
    !first.is_empty() && first.iter().all(|&b| matches!(b, 9 | 10 | 13) || (32..127).contains(&b) || b >= 128)
}

/// Where the licensed section header at the start of `bytes` ends (just
/// past its NUL), as the regex `Hou(?:LC|NC)\x1a[0-9a-f]{8,64}[^\x00\n]{1,256}\x00`
/// matches it; None when there is none.
fn licensed_header(bytes: &[u8]) -> Option<usize> {
    if !(bytes.starts_with(b"HouLC\x1a") || bytes.starts_with(b"HouNC\x1a")) {
        return None;
    }
    let rest = &bytes[6..];
    let stop = rest.iter().position(|&b| b == 0 || b == b'\n')?;
    if rest[stop] != 0 {
        return None;
    }
    let segment = &rest[..stop];
    let hex = segment.iter().take_while(|&&b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)).count();
    // the key takes 8..=min(64, hex) of the run, the name the rest (1..=256)
    let longest = hex.min(64);
    if hex < 8 || segment.len() < 9 || segment.len() - longest > 256 {
        return None;
    }
    Some(6 + stop + 1)
}

/// A workfile's text, as Python reads it (`read_bytes().decode('utf-8',
/// 'replace')`).
pub fn read_text(path: &Path) -> std::io::Result<String> {
    std::fs::read(path).map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
}

/// A file's bytes a megabyte at a time, `more(bytes)` told after each; it
/// answers false to stop (an `Interrupted` error).
pub fn read_bytes_metered(path: &Path, more: &dyn Fn(u64) -> bool) -> std::io::Result<Vec<u8>> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut bytes = Vec::new();
    let mut chunk = vec![0u8; 1 << 20];
    loop {
        let n = match file.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => n,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        bytes.extend_from_slice(&chunk[..n]);
        if !more(n as u64) {
            return Err(std::io::Error::new(std::io::ErrorKind::Interrupted, "cancelled"));
        }
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|p| p.to_string()).collect()
    }

    /// `archive.hipfile.readable` (test_hipfile pins Python's; the parity run
    /// draws both forms): a `.hiplc` / `.hipnc` reads in Houdini's licensed
    /// layout only.
    #[test]
    fn licensed_workfiles() {
        let start = b"fplayback -i on -r off\nfps 24\n";
        let file =
            |magic: &[u8], key: &str, name: &str, first: &[u8]| [magic, b"\x1a", key.as_bytes(), name.as_bytes(), b"\x00", first].concat();
        let key = "1033600baa067e1658609a7e5963";
        assert!(readable(&file(b"HouLC", key, ".start", start), "a_v0001.hiplc"));
        assert!(readable(&file(b"HouNC", key, ".start", start), "a_v0001.HIPNC"));
        // a short first section ends at the next header
        let two = [file(b"HouLC", key, ".start", b"fps 24\n"), file(b"HouLC", key, ".variables", b"set -g A = 1\n")].concat();
        assert!(readable(&two, "a.hiplc"));
        // any other form is not read
        assert!(!readable(start, "a.hiplc"));
        assert!(!readable(&file(b"HouXX", key, ".start", start), "a.hiplc"));
        assert!(!readable(&file(b"HouLC", "abc123", ".start", start), "a.hiplc"));
        assert!(!readable(&file(b"HouLC", &key.to_uppercase(), ".start", start), "a.hiplc"));
        assert!(!readable(&file(b"HouLC", key, ".start\n", start), "a.hiplc"));
        assert!(!readable(&file(b"HouLC", key, ".start", &[1, 2, 3, 0, 255]), "a.hiplc"));
        assert!(!readable(&file(b"HouLC", key, &"x".repeat(257), start), "a.hiplc"));
        assert!(readable(&file(b"HouLC", key, &"x".repeat(256), start), "a.hiplc"));
        // a .hip always reads; another name never
        assert!(readable(&[0, 1, 2], "a.hip"));
        assert!(!readable(&file(b"HouLC", key, ".start", start), "a.txt"));
    }

    #[test]
    fn parm_lines() {
        assert_eq!(parm("version\t[ 0\tlocks=0 ]\t(\tv0003\t)"), Some(("version", "v0003")));
        assert_eq!(parm("entity\t[ 0\tlocks=0 ]\t(\t\"entity:/shots/a\"\t)  "), Some(("entity", "entity:/shots/a")));
        assert_eq!(parm("a\t[ ]\t(\tx\t)\ty\t)"), Some(("a", "x\t)\ty")));
        assert_eq!(parm("a\t[ ]\t(\tx\t)\ty"), None);
        assert_eq!(parm("\t[ ]\t(\tx\t)"), None);
        assert_eq!(parm("a\t[ ]\t(\t\t)"), Some(("a", "")));
    }

    #[test]
    fn entity_finds() {
        assert_eq!(entities("entity:/shots/s0/h1?dept=x entity:/assets/A"), vec!["shots/s0/h1", "assets/A"]);
        assert_eq!(entities("entity:/shots/s0:x"), Vec::<&str>::new());
        assert_eq!(entities("entity:/edits/x"), Vec::<&str>::new());
        assert_eq!(pinned_uris("a entity:/shots/x?version=v0001@b entity:/"), vec!["entity:/shots/x?version=v0001"]);
    }

    #[test]
    fn refs_name_units() {
        assert_eq!(
            ref_units("entity:/shots/s/h?dept=fx&channel=c&version=v0002", &[]).unwrap(),
            vec![s(&["shots", "s", "h", "c", "fx", "v0002"])]
        );
        assert_eq!(ref_units("entity:/shots/s/h?version=v0002", &[]).unwrap(), vec![s(&["shots", "s", "h", STAGED, "default", "v0002"])]);
        assert_eq!(ref_units("entity:/shots/s/h?dept=root&version=v0002", &[]).unwrap(), vec![s(&["shots", "s", "h", ROOT, "v0002"])]);
        assert!(ref_units("entity:/a?channel=x&variant=y&version=v0001", &[]).is_err());
        assert_eq!(ref_units("../../b/v0003/x.usd", &s(&["shots", "a", "v0001", "sub"])).unwrap(), vec![s(&["shots", "a", "b", "v0003"])]);
        assert!(ref_units("/abs/v0001", &[]).unwrap().is_empty());
        assert!(ref_units("export:/v0001", &[]).unwrap().is_empty());
        assert_eq!(usda_refs(b"x @a.usd@ y @@@b@@@"), vec!["a.usd".to_string(), "b".to_string()]);
        assert_eq!(usda_refs(b"@@@a@@@ @b\n@ @c@"), vec!["a".to_string(), " ".to_string(), " ".to_string()]);
    }
}
