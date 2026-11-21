//! The filesystem as the Python cleanup sees it: `Path.resolve()`, path
//! equality (case-blind on Windows, as `WindowsPath` compares), `os.walk`'s
//! and `os.scandir`'s order, `st_mtime` as a float.
//!
//! Order matters where Python's does: what a walk meets first decides which
//! error is reported, and the listing comes out in the order Python lists.
//! `std::fs::read_dir` reads a folder in the order `os.scandir` does
//! (`FindFirstFileW` on Windows, `readdir` elsewhere), so walking the same
//! way gives the same order.

use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Seconds since the epoch, as `time.time()`.
pub fn now() -> f64 {
    seconds(SystemTime::now())
}

/// `time` as seconds since the epoch (`st_mtime`).
pub fn seconds(time: SystemTime) -> f64 {
    match time.duration_since(UNIX_EPOCH) {
        Ok(after) => after.as_secs_f64(),
        Err(before) => -before.duration().as_secs_f64(),
    }
}

/// `os.stat(path).st_mtime` (links followed); None when it cannot be read.
pub fn mtime(path: &Path) -> Option<f64> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok().map(seconds)
}

/// `os.lstat(path).st_mtime`.
pub fn lmtime(path: &Path) -> std::io::Result<f64> {
    std::fs::symlink_metadata(path).and_then(|m| m.modified()).map(seconds)
}

/// A verbatim path (`\\?\C:\x`, `\\?\UNC\server\share\x`) as Python spells
/// it once resolved (`C:\x`, `\\server\share\x`).
fn plain(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = text.strip_prefix(r"\\?\")
        && rest.as_bytes().get(1) == Some(&b':')
    {
        return PathBuf::from(rest);
    }
    path
}

/// `Path(path).resolve()`: absolute, links and short names resolved as far
/// as the path exists, the rest joined on as it is spelled.
pub fn resolve(path: &Path) -> PathBuf {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let mut head = absolute.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(found) = std::fs::canonicalize(&head) {
            let mut out = plain(found);
            for part in tail.iter().rev() {
                out.push(part);
            }
            return out;
        }
        match (head.file_name().map(|n| n.to_os_string()), head.parent()) {
            (Some(name), Some(parent)) => {
                tail.push(name);
                head = parent.to_path_buf();
            }
            _ => return absolute,
        }
    }
}

/// How Python compares a path part: case-blind on Windows.
pub fn norm_case(text: &str) -> String {
    if cfg!(windows) { text.replace('/', "\\").to_lowercase() } else { text.to_string() }
}

/// A path's parts, as `PurePath.parts` compares them.
fn parts(path: &Path) -> Vec<String> {
    path.components()
        .filter(|c| !matches!(c, Component::CurDir))
        .map(|c| norm_case(&c.as_os_str().to_string_lossy()))
        .map(|p| if cfg!(windows) { p.trim_end_matches('\\').to_string() } else { p })
        .collect()
}

/// `a == b` for two `Path`s.
pub fn same_path(a: &Path, b: &Path) -> bool {
    parts(a) == parts(b)
}

/// `path.is_relative_to(parent)`: `path` is `parent` or under it.
pub fn is_relative_to(path: &Path, parent: &Path) -> bool {
    let (path, parent) = (parts(path), parts(parent));
    path.len() >= parent.len() && path[..parent.len()] == parent[..]
}

/// `cleanup.core.within`: `path` (resolved) sits under `parent` (resolved).
pub fn within(path: &Path, parent: &Path) -> bool {
    is_relative_to(&resolve(path), &resolve(parent))
}

/// A name as Python reads it from the filesystem.
pub fn name_of(name: &std::ffi::OsStr) -> String {
    name.to_string_lossy().into_owned()
}

/// `archive.inventory._walk`: `(relative parts, size)` for every file under
/// `root`, links not followed, folders in `skip` not entered. Folders are
/// walked from a stack, as Python's does, so the order is Python's.
pub fn walk_files(root: &Path, skip: &[PathBuf], errors: &mut Vec<String>) -> Vec<(Vec<String>, u64)> {
    let skip: Vec<Vec<String>> = skip.iter().map(|p| parts(p)).collect();
    let mut out = Vec::new();
    let mut stack: Vec<(PathBuf, Vec<String>)> = vec![(root.to_path_buf(), Vec::new())];
    while let Some((directory, prefix)) = stack.pop() {
        let entries: Vec<std::fs::DirEntry> = match std::fs::read_dir(&directory) {
            Ok(entries) => entries.filter_map(Result::ok).collect(),
            Err(error) => {
                errors.push(format!("{}: {error}", directory.display()));
                continue;
            }
        };
        for entry in entries {
            let name = name_of(&entry.file_name());
            let mut here = prefix.clone();
            here.push(name.clone());
            let kind = match entry.file_type() {
                Ok(kind) => kind,
                Err(error) => {
                    errors.push(format!("{}: {error}", entry.path().display()));
                    continue;
                }
            };
            if kind.is_dir() {
                let child = directory.join(&name);
                if skip.contains(&parts(&child)) {
                    continue;
                }
                stack.push((child, here));
            } else if kind.is_file() {
                match entry.metadata() {
                    Ok(meta) => out.push((here, meta.len())),
                    Err(error) => errors.push(format!("{}: {error}", entry.path().display())),
                }
            }
        }
    }
    out
}

/// `f` over `items`, up to `threads` at once, the results in `items`'
/// order: what one at a time would give, sooner where `f` waits on a share.
pub fn par_map<T: Sync, R: Send>(items: &[T], threads: usize, f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicUsize, Ordering};
    if threads <= 1 || items.len() < 2 {
        return items.iter().map(f).collect();
    }
    let next = AtomicUsize::new(0);
    let results: Vec<Mutex<Option<R>>> = items.iter().map(|_| Mutex::new(None)).collect();
    std::thread::scope(|scope| {
        for _ in 0..threads.min(items.len()) {
            scope.spawn(|| {
                loop {
                    let at = next.fetch_add(1, Ordering::Relaxed);
                    let Some(item) = items.get(at) else { break };
                    *results[at].lock().unwrap() = Some(f(item));
                }
            });
        }
    });
    results.into_iter().map(|r| r.into_inner().unwrap().expect("every item mapped")).collect()
}

/// One entry of a folder as the parallel walk lists it.
/// Folders still to list, and how many are being listed.
type Queue = (Vec<(PathBuf, Vec<String>)>, usize);
/// What listing a folder gave, by the folder's parts under the root.
type Listings = std::collections::HashMap<Vec<String>, Result<Vec<Found>, String>>;

enum Found {
    /// A folder, and whether it is one of the folders not entered.
    Dir(String, bool),
    File(String, Result<u64, String>),
    /// An entry whose type could not be read: its error.
    Unknown(String),
    Other,
}

/// [`walk_files`] with `threads` folders listed at once (a share answers
/// one listing per round trip, so several in flight hide the latency),
/// `progress(files, bytes)` told as they are listed, from any thread.
/// The files and errors are exactly [`walk_files`]'s, in its order: the
/// folders are listed in parallel, then walked from the listings in
/// Python's stack order. Once `cancel` is set no more folders are listed
/// (what comes back is then partial, for the caller to throw away).
pub fn walk_files_threads(
    root: &Path,
    skip: &[PathBuf],
    errors: &mut Vec<String>,
    threads: usize,
    cancel: &std::sync::atomic::AtomicBool,
    progress: &(dyn Fn(usize, u64) + Sync),
) -> Vec<(Vec<String>, u64)> {
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Condvar, Mutex};

    let skip: Vec<Vec<String>> = skip.iter().map(|p| parts(p)).collect();
    let list = |directory: &Path| -> Result<Vec<Found>, String> {
        let entries = std::fs::read_dir(directory).map_err(|error| format!("{}: {error}", directory.display()))?;
        Ok(entries
            .filter_map(Result::ok)
            .map(|entry| {
                let name = name_of(&entry.file_name());
                match entry.file_type() {
                    Err(error) => Found::Unknown(format!("{}: {error}", entry.path().display())),
                    Ok(kind) if kind.is_dir() => {
                        let skipped = skip.contains(&parts(&directory.join(&name)));
                        Found::Dir(name, skipped)
                    }
                    Ok(kind) if kind.is_file() => {
                        let size = entry.metadata().map(|m| m.len()).map_err(|error| format!("{}: {error}", entry.path().display()));
                        Found::File(name, size)
                    }
                    Ok(_) => Found::Other,
                }
            })
            .collect())
    };

    // Every folder listed, by its parts under `root`.
    let listings: Mutex<Listings> = Mutex::new(HashMap::new());
    let queue: Mutex<Queue> = Mutex::new((vec![(root.to_path_buf(), Vec::new())], 0));
    let ready = Condvar::new();
    let (files, bytes) = (AtomicUsize::new(0), AtomicU64::new(0));
    std::thread::scope(|scope| {
        for _ in 0..threads.max(1) {
            scope.spawn(|| {
                loop {
                    let (directory, prefix) = {
                        let mut state = queue.lock().unwrap();
                        loop {
                            if cancel.load(Ordering::Relaxed) {
                                state.0.clear();
                            }
                            if let Some(job) = state.0.pop() {
                                state.1 += 1;
                                break job;
                            }
                            if state.1 == 0 {
                                ready.notify_all();
                                return;
                            }
                            state = ready.wait(state).unwrap();
                        }
                    };
                    let listing = list(&directory);
                    let mut children = Vec::new();
                    if let Ok(found) = &listing {
                        for entry in found {
                            match entry {
                                Found::Dir(name, false) => {
                                    let mut here = prefix.clone();
                                    here.push(name.clone());
                                    children.push((directory.join(name), here));
                                }
                                Found::File(_, size) => {
                                    files.fetch_add(1, Ordering::Relaxed);
                                    bytes.fetch_add(*size.as_ref().unwrap_or(&0), Ordering::Relaxed);
                                }
                                _ => {}
                            }
                        }
                    }
                    listings.lock().unwrap().insert(prefix, listing);
                    let mut state = queue.lock().unwrap();
                    state.0.extend(children);
                    state.1 -= 1;
                    ready.notify_all();
                    drop(state);
                    progress(files.load(Ordering::Relaxed), bytes.load(Ordering::Relaxed));
                }
            });
        }
    });

    // Walked from the listings exactly as `walk_files` walks the disk.
    let mut listings = listings.into_inner().unwrap();
    let mut out = Vec::new();
    let mut stack: Vec<Vec<String>> = vec![Vec::new()];
    while let Some(prefix) = stack.pop() {
        let found = match listings.remove(&prefix) {
            Some(Ok(found)) => found,
            Some(Err(error)) => {
                errors.push(error);
                continue;
            }
            None => continue,
        };
        for entry in found {
            match entry {
                Found::Dir(_, true) | Found::Other => {}
                Found::Dir(name, false) => {
                    let mut here = prefix.clone();
                    here.push(name);
                    stack.push(here);
                }
                Found::File(name, Ok(size)) => {
                    let mut here = prefix.clone();
                    here.push(name);
                    out.push((here, size));
                }
                Found::File(_, Err(error)) | Found::Unknown(error) => errors.push(error),
            }
        }
    }
    out
}

/// One folder `os.walk` yields: its path, the folders in it (which the
/// visitor may prune, as Python's caller assigns `dirs[:]`) and its files.
pub struct WalkDir<'a> {
    pub path: &'a Path,
    pub dirs: &'a mut Vec<String>,
    pub files: &'a [String],
}

/// `os.walk(top)`, top down: each folder before what is in it, in listing
/// order; a folder that cannot be read is passed over; links to folders are
/// listed among the folders but not entered.
pub fn os_walk(top: &Path, mut visit: impl FnMut(WalkDir<'_>)) {
    let mut stack = vec![top.to_path_buf()];
    while let Some(path) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&path) else { continue };
        let mut dirs = Vec::new();
        let mut files = Vec::new();
        for entry in entries {
            let Ok(entry) = entry else { break };
            let name = name_of(&entry.file_name());
            // `DirEntry.is_dir()` follows a link.
            let is_dir = match entry.file_type() {
                Ok(kind) if kind.is_symlink() => entry.path().is_dir(),
                Ok(kind) => kind.is_dir(),
                Err(_) => false,
            };
            if is_dir { dirs.push(name) } else { files.push(name) }
        }
        visit(WalkDir { path: &path, dirs: &mut dirs, files: &files });
        for name in dirs.iter().rev() {
            let child = path.join(name);
            let link = std::fs::symlink_metadata(&child).map(|m| m.file_type().is_symlink()).unwrap_or(false);
            if !link {
                stack.push(child);
            }
        }
    }
}

/// The bytes of the files under `path`, links followed for files, what
/// cannot be read left out (`deleted._size`, `prune._size`).
pub fn tree_size(path: &Path) -> u64 {
    let mut total = 0;
    os_walk(path, |dir| {
        for name in dir.files {
            if let Ok(meta) = std::fs::metadata(dir.path.join(name)) {
                total += meta.len();
            }
        }
    });
    total
}

/// A file's size, 0 when it cannot be read.
pub fn lsize(path: &Path) -> u64 {
    std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

/// `cleanup.core.recently_written`: whether the folder `path` or anything
/// directly in it changed in the last `days` (a folder's time moves when a
/// file is added to it). What cannot be read counts as written: kept.
pub fn recently_written(path: &Path, now: f64, days: f64) -> bool {
    let limit = now - days * 86400.0;
    let Some(own) = mtime(path) else { return true };
    if own > limit {
        return true;
    }
    let Ok(entries) = std::fs::read_dir(path) else { return true };
    for entry in entries {
        let Ok(entry) = entry else { return true };
        match lmtime(&entry.path()) {
            Ok(stamp) if stamp > limit => return true,
            Ok(_) => {}
            Err(_) => return true,
        }
    }
    false
}

/// `Path.iterdir()` names sorted as `sorted()` sorts `Path`s: by their
/// parts, case-blind on Windows.
pub fn sort_paths(paths: &mut [PathBuf]) {
    paths.sort_by_key(|p| parts(p));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_compare_as_python() {
        assert!(is_relative_to(Path::new("/a/b/c"), Path::new("/a/b")));
        assert!(is_relative_to(Path::new("/a/b"), Path::new("/a/b")));
        assert!(!is_relative_to(Path::new("/a/bc"), Path::new("/a/b")));
        if cfg!(windows) {
            assert!(same_path(Path::new(r"C:\A\b"), Path::new("c:/a/B")));
        }
    }

    #[test]
    fn resolve_keeps_what_does_not_exist() {
        let dir = tempfile::tempdir().unwrap();
        let real = resolve(dir.path());
        let missing = resolve(&dir.path().join("x").join("y"));
        assert_eq!(missing, real.join("x").join("y"));
        assert!(!real.to_string_lossy().starts_with(r"\\?\"));
    }
}
