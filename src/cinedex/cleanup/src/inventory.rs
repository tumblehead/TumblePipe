//! Every file of a project's roots, classified (`tumblepipe.archive.inventory`).
//!
//! `scan` walks each root once and gives every file a [`Kind`]; files that
//! come in versions also get the version folder they belong to (their
//! *unit*), the series it is one of, its number and whether it finished.
//! Only what the cleanup reads is classified as Python does; the archive's
//! own kinds (config, pipeline folders, ...) are told apart only as far as
//! the cleanup needs.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde_json::Value;
use th_pipe::naming::is_valid_version_name;

use crate::core::{CategoryPlan, Progress, Watch};
use crate::py;
use crate::roots::{ProjectRoots, Root, WORKFILE_PURPOSES};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Workfile,
    WorkfileMeta,
    WorkfileFile,
    Backup,
    Cache,
    LocalRender,
    DerivedTexture,
    Export,
    ExportFile,
    Staged,
    FarmJob,
    ExportLog,
    Render,
    DerivedRoot,
    Edit,
    Other,
    Environment,
    Noise,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    /// The root's purpose.
    pub root: String,
    /// POSIX path relative to the root.
    pub rel: String,
    pub size: u64,
    pub kind: Kind,
    pub unit: Option<String>,
    pub series: Option<String>,
    pub version: Option<i128>,
    pub complete: bool,
    /// False for a check render's files.
    pub published: bool,
}

impl Item {
    fn plain(root: &Root, rel: String, size: u64, kind: Kind) -> Self {
        Item { root: root.purpose.clone(), rel, size, kind, unit: None, series: None, version: None, complete: true, published: true }
    }

    pub fn path(&self, roots: &ProjectRoots) -> PathBuf {
        let mut path = roots.path(&self.root).map(Path::to_path_buf).unwrap_or_default();
        path.extend(self.rel.split('/'));
        path
    }
}

#[derive(Debug, Default)]
pub struct Inventory {
    pub items: Vec<Item>,
    /// Paths that could not be read.
    pub errors: Vec<String>,
}

const NOISE_NAMES: [&str; 5] = ["__pycache__", "Thumbs.db", "desktop.ini", ".DS_Store", ".git"];
const NOISE_SUFFIXES: [&str; 3] = [".pyc", ".pyo", ".tmp"];
const ENVIRONMENT_DIRS: [&str; 8] =
    ["node_modules", "site-packages", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints", ".tox"];
const CACHE_DIRS: [&str; 2] = ["cache", "lops_cache"];

/// `_BAK`: `\.bak(?:$|[-_.\d])`, case-blind, searched.
fn has_bak(name: &str) -> bool {
    let chars: Vec<char> = name.chars().collect();
    for at in 0..chars.len() {
        if chars[at] != '.' || at + 4 > chars.len() {
            continue;
        }
        let word = &chars[at + 1..at + 4];
        let b = matches!(word[0], 'b' | 'B');
        let a = matches!(word[1], 'a' | 'A');
        let k = matches!(word[2], 'k' | 'K' | '\u{212A}');
        if !(b && a && k) {
            continue;
        }
        match chars.get(at + 4) {
            None => return true,
            // `$` also matches before a final newline.
            Some('\n') if at + 5 == chars.len() => return true,
            Some(c) if matches!(c, '-' | '_' | '.') || py::is_digit(*c) => return true,
            _ => {}
        }
    }
    false
}

/// `inventory.is_noise`.
pub fn is_noise(parts: &[String]) -> bool {
    let name = parts.last().map(String::as_str).unwrap_or("");
    if parts.iter().any(|p| NOISE_NAMES.contains(&p.as_str())) {
        return true;
    }
    if NOISE_SUFFIXES.iter().any(|s| name.to_lowercase().ends_with(s)) || name.ends_with('~') {
        return true;
    }
    has_bak(name)
}

/// `inventory._file_version`: the version a file's name ends in
/// (`v0003.mp4`, `010_sh020_animation_v0003.mp4`).
pub fn file_version(name: &str) -> Option<String> {
    let stem = name.split('.').next().unwrap_or("");
    if is_valid_version_name(stem) {
        return Some(stem.to_string());
    }
    let (_, version) = stem.rsplit_once('_')?;
    is_valid_version_name(version).then(|| version.to_string())
}

/// `inventory._version_index`.
fn version_index(parts: &[String], stems: bool) -> Option<usize> {
    for (index, part) in parts.iter().enumerate() {
        if is_valid_version_name(part) {
            return Some(index);
        }
        if stems && index == parts.len() - 1 && file_version(part).is_some() {
            return Some(index);
        }
    }
    None
}

/// `naming.get_version_code` on a valid version name.
pub fn version_code(name: &str) -> i128 {
    py::int_digits(&name[1..]).unwrap_or(0)
}

/// `_WORKFILE`: `_(v\d{4})\.(hip|hiplc|hipnc)$`, case-blind (`İ`, `ı`
/// match `i` there), searched; the version name, lower-cased.
pub fn workfile_version(name: &str) -> Option<String> {
    let chars: Vec<char> = name.chars().collect();
    let end = if chars.last() == Some(&'\n') { chars.len() - 1 } else { chars.len() };
    let i = |c: char| matches!(c, 'i' | 'I' | '\u{130}' | '\u{131}');
    let tail = |from: usize| -> bool {
        // `.hip`, `.hiplc` or `.hipnc` from `from` to the end.
        let rest = &chars[from..end];
        let hip = rest.len() >= 4 && rest[0] == '.' && matches!(rest[1], 'h' | 'H') && i(rest[2]) && matches!(rest[3], 'p' | 'P');
        if !hip {
            return false;
        }
        match rest.len() {
            4 => true,
            6 => {
                (matches!(rest[4], 'l' | 'L') && matches!(rest[5], 'c' | 'C'))
                    || (matches!(rest[4], 'n' | 'N') && matches!(rest[5], 'c' | 'C'))
            }
            _ => false,
        }
    };
    for at in 0..chars.len() {
        if chars[at] != '_' || at + 6 > chars.len() {
            continue;
        }
        if !matches!(chars[at + 1], 'v' | 'V') || !chars[at + 2..at + 6].iter().all(|c| py::is_digit(*c)) {
            continue;
        }
        if tail(at + 6) {
            let version: String = chars[at + 1..at + 6].iter().collect();
            return Some(version.to_lowercase());
        }
    }
    None
}

fn join(parts: &[String]) -> String {
    parts.join("/")
}

/// The sidecar a `th::cache` version writes when it finishes
/// (`pipe.cache_context.load_cache_context`): None when there is none or it
/// is not a cache's. A sidecar that is there but cannot be read is an
/// error, as `load_json` raises in Python.
pub fn load_cache_context(version_path: &Path) -> Result<Option<Value>, String> {
    let path = version_path.join("context.json");
    if !path.exists() {
        return Ok(None);
    }
    let bytes = std::fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let data: Value = serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(match &data {
        Value::Object(map) if map.get("kind").and_then(Value::as_str) == Some("cache") => Some(data),
        _ => None,
    })
}

struct Classifier<'a> {
    complete: HashMap<String, bool>,
    published: HashMap<String, bool>,
    layers: HashMap<String, Vec<String>>,
    venv: HashMap<Vec<String>, bool>,
    _roots: &'a ProjectRoots,
}

impl Classifier<'_> {
    fn in_environment(&mut self, root: &Root, dirs: &[String]) -> bool {
        if dirs.iter().any(|d| ENVIRONMENT_DIRS.contains(&d.as_str())) {
            return true;
        }
        for depth in 1..=dirs.len() {
            let mut key = vec![root.purpose.clone()];
            key.extend(dirs[..depth].iter().cloned());
            let found = *self.venv.entry(key).or_insert_with(|| {
                let mut path = root.path.clone();
                path.extend(&dirs[..depth]);
                path.join("pyvenv.cfg").is_file()
            });
            if found {
                return true;
            }
        }
        false
    }

    fn export_complete(&mut self, root: &Root, unit: &[String]) -> bool {
        let key = format!("{}:{}", root.purpose, join(unit));
        *self.complete.entry(key).or_insert_with(|| {
            let mut path = root.path.clone();
            path.extend(unit);
            path.join("context.json").is_file()
        })
    }

    fn cache_complete(&mut self, root: &Root, unit: &[String]) -> Result<bool, String> {
        let key = format!("{}:{}", root.purpose, join(unit));
        if let Some(done) = self.complete.get(&key) {
            return Ok(*done);
        }
        let mut path = root.path.clone();
        path.extend(unit);
        let done = load_cache_context(&path)?.is_some();
        self.complete.insert(key, done);
        Ok(done)
    }

    fn unit_item(root: &Root, parts: &[String], size: u64, kind: Kind, index: usize, complete: bool) -> Item {
        let unit = &parts[..=index];
        let last = &unit[index];
        let version_name = if is_valid_version_name(last) { Some(last.clone()) } else { file_version(last) };
        Item {
            root: root.purpose.clone(),
            rel: join(parts),
            size,
            kind,
            unit: Some(join(unit)),
            series: Some(join(&unit[..index])),
            version: version_name.map(|v| version_code(&v)),
            complete,
            published: true,
        }
    }

    fn workfile_item(&mut self, root: &Root, parts: &[String], size: u64) -> Result<Item, String> {
        let rel = join(parts);
        let dirs = &parts[..parts.len() - 1];
        if dirs.iter().any(|d| d == "backup") {
            return Ok(Item::plain(root, rel, size, Kind::Backup));
        }
        for cache_dir in CACHE_DIRS {
            if let Some(at) = dirs.iter().position(|d| d == cache_dir) {
                if at + 2 < parts.len() - 1 && is_valid_version_name(&parts[at + 2]) {
                    let index = at + 2;
                    let complete = self.cache_complete(root, &parts[..=index])?;
                    return Ok(Self::unit_item(root, parts, size, Kind::Cache, index, complete));
                }
                let mut item = Item::plain(root, rel, size, Kind::Cache);
                item.complete = false;
                return Ok(item);
            }
        }
        let last = &parts[parts.len() - 1];
        if dirs.iter().any(|d| d == "_context") || last == "context.json" {
            return Ok(Item::plain(root, rel, size, Kind::WorkfileMeta));
        }
        if dirs.iter().any(|d| d == "render" || d == "galleries") {
            return Ok(Item::plain(root, rel, size, Kind::LocalRender));
        }
        if let Some(version) = workfile_version(last) {
            let mut item = Item::plain(root, rel.clone(), size, Kind::Workfile);
            item.unit = Some(rel);
            item.series = Some(join(dirs));
            item.version = Some(version_code(&version));
            return Ok(item);
        }
        let lower = last.to_lowercase();
        if lower.ends_with(".rat") || lower.ends_with(".tx") {
            return Ok(Item::plain(root, rel, size, Kind::DerivedTexture));
        }
        Ok(Item::plain(root, rel, size, Kind::WorkfileFile))
    }

    fn export_item(&mut self, root: &Root, parts: &[String], size: u64) -> Item {
        let rel = join(parts);
        if parts[0] == "other" && parts.len() > 1 {
            return Item::plain(root, rel, size, if parts[1] == "jobs" { Kind::FarmJob } else { Kind::ExportLog });
        }
        let dirs = &parts[..parts.len() - 1];
        let index = version_index(dirs, false);
        let kind = if dirs.iter().any(|d| d == "_staged") { Kind::Staged } else { Kind::Export };
        let Some(index) = index else {
            return Item::plain(root, rel, size, if kind == Kind::Staged { Kind::Staged } else { Kind::ExportFile });
        };
        let complete = kind == Kind::Staged || self.export_complete(root, &parts[..=index]);
        Self::unit_item(root, parts, size, kind, index, complete)
    }

    fn render_published(&mut self, root: &Root, unit: &[String]) -> bool {
        let key = format!("{}:{}", root.purpose, join(unit));
        *self.published.entry(key).or_insert_with(|| {
            let mut path = root.path.clone();
            path.extend(unit);
            let Ok(bytes) = std::fs::read(path.join("context.json")) else { return true };
            match serde_json::from_slice::<Value>(&bytes) {
                Ok(Value::Object(map)) => map.get("published") != Some(&Value::Bool(false)),
                _ => true,
            }
        })
    }

    fn movie_published(&mut self, root: &Root, parts: &[String]) -> bool {
        if parts.len() < 3 || parts[0] != "playblast" {
            return true;
        }
        let version_name = file_version(&parts[parts.len() - 1]).unwrap_or_default();
        let middle = &parts[1..parts.len() - 1];
        for department in [middle, &middle[..middle.len().saturating_sub(1)]] {
            let key = format!("{}:render/{}", root.purpose, join(department));
            let layers = self
                .layers
                .entry(key)
                .or_insert_with(|| {
                    let mut folder = root.path.join("render");
                    folder.extend(department);
                    let mut names: Vec<String> = std::fs::read_dir(&folder)
                        .map(|entries| {
                            entries
                                .filter_map(Result::ok)
                                .filter(|e| e.path().is_dir())
                                .map(|e| crate::fsx::name_of(&e.file_name()))
                                .collect()
                        })
                        .unwrap_or_default();
                    names.sort();
                    names
                })
                .clone();
            for layer in layers {
                let mut unit: Vec<String> = vec!["render".into()];
                unit.extend(department.iter().cloned());
                unit.push(layer);
                unit.push(version_name.clone());
                if !self.render_published(root, &unit) {
                    return false;
                }
            }
        }
        true
    }

    fn render_item(&mut self, root: &Root, parts: &[String], size: u64) -> Item {
        let Some(index) = version_index(parts, true) else {
            return Item::plain(root, join(parts), size, Kind::Render);
        };
        let mut item = Self::unit_item(root, parts, size, Kind::Render, index, true);
        item.published =
            if index == parts.len() - 1 { self.movie_published(root, parts) } else { self.render_published(root, &parts[..=index]) };
        item
    }

    fn classify(&mut self, root: &Root, parts: &[String], size: u64) -> Result<Item, String> {
        if is_noise(parts) {
            return Ok(Item::plain(root, join(parts), size, Kind::Noise));
        }
        if root.purpose != "config" && self.in_environment(root, &parts[..parts.len() - 1]) {
            return Ok(Item::plain(root, join(parts), size, Kind::Environment));
        }
        let purpose = root.purpose.as_str();
        if WORKFILE_PURPOSES.contains(&purpose) {
            return self.workfile_item(root, parts, size);
        }
        Ok(match purpose {
            "export" => self.export_item(root, parts, size),
            "render" => self.render_item(root, parts, size),
            "edit" => Item::plain(root, join(parts), size, Kind::Edit),
            "cache" | "proxy" | "turntable" => Item::plain(root, join(parts), size, Kind::DerivedRoot),
            _ => Item::plain(root, join(parts), size, Kind::Other),
        })
    }
}

/// Classify every file under the roots of `purposes`, the other roots not
/// walked into (`inventory.scan`). An error when a cache's sidecar is there
/// but cannot be read, as Python's scan raises.
pub fn scan(roots: &ProjectRoots, purposes: &[&str]) -> Result<Inventory, String> {
    let never = std::sync::atomic::AtomicBool::new(false);
    let watch = Watch::new(1, false, &never, &|_: &Progress| {}, &|_: usize, _: &CategoryPlan| {});
    scan_watched(roots, purposes, &watch)
}

/// A root's files as listed: `(relative parts, size)`.
type Listed = Vec<(Vec<String>, u64)>;

/// [`scan`], its folders listed `watch.threads` at a time and its progress
/// reported: the files listed (no total), then the files sorted of all.
/// The inventory is exactly [`scan`]'s.
pub fn scan_watched(roots: &ProjectRoots, purposes: &[&str], watch: &Watch<'_>) -> Result<Inventory, String> {
    let mut inventory = Inventory::default();
    let mut classifier =
        Classifier { complete: HashMap::new(), published: HashMap::new(), layers: HashMap::new(), venv: HashMap::new(), _roots: roots };
    let all: Vec<PathBuf> = roots.roots.iter().map(|r| r.path.clone()).collect();
    // Every root listed first, then sorted: a sorting error stops the scan
    // as it did root by root, and the listing errors are kept in order.
    let listing = watch.meter(None, "Listing files", None);
    let mut listed: Vec<(&Root, Listed)> = Vec::new();
    let (mut files, mut bytes) = (0usize, 0u64);
    for root in &roots.roots {
        if !purposes.contains(&root.purpose.as_str()) || !root.path.is_dir() {
            continue;
        }
        let skip: Vec<PathBuf> = all.iter().filter(|p| !crate::fsx::same_path(p, &root.path)).cloned().collect();
        let found = if watch.threads <= 1 {
            crate::fsx::walk_files(&root.path, &skip, &mut inventory.errors)
        } else {
            let progress = |n: usize, b: u64| listing.set(files + n, bytes + b);
            crate::fsx::walk_files_threads(&root.path, &skip, &mut inventory.errors, watch.threads, watch.cancel, &progress)
        };
        watch.check()?;
        files += found.len();
        bytes += found.iter().map(|(_, size)| size).sum::<u64>();
        listing.set(files, bytes);
        listed.push((root, found));
    }
    listing.finish();
    let sorting = watch.meter(None, "Sorting files", Some(files));
    for (root, found) in listed {
        for (parts, size) in found {
            let item = classifier.classify(root, &parts, size)?;
            inventory.items.push(item);
            sorting.add(1, size);
            if inventory.items.len() % 1024 == 0 {
                watch.check()?;
            }
        }
    }
    sorting.finish();
    Ok(inventory)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_and_versions() {
        let p = |s: &str| s.split('/').map(String::from).collect::<Vec<_>>();
        assert!(is_noise(&p("a/context.json.bak-2026")));
        assert!(is_noise(&p("a/x.BAK")));
        assert!(is_noise(&p("a/x.bak7")));
        assert!(!is_noise(&p("a/x.bakery")));
        assert!(is_noise(&p("__pycache__/x.py")));
        assert_eq!(file_version("010_sh020_anim_v0003.mp4").as_deref(), Some("v0003"));
        assert_eq!(file_version("v0003.mp4").as_deref(), Some("v0003"));
        assert_eq!(file_version("x.mp4"), None);
        assert_eq!(workfile_version("a_V0003.HIPLC").as_deref(), Some("v0003"));
        assert_eq!(workfile_version("a_v0003.hip.bak"), None);
        assert_eq!(workfile_version("a_v00031.hip"), None);
        assert_eq!(workfile_version("a_v0003.h\u{131}p").as_deref(), Some("v0003"));
    }
}
