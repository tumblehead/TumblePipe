//! Opening the pipeline's Farm Submit window on some shots.
//!
//! Cinedex submits nothing itself. **Farm Submit…** starts the window the
//! Render quick action opens in Houdini (`python -m tumblepipe.farm_app`)
//! with the shots ticked (`--entity`, one each): every setting is there to
//! be read and changed, and nothing goes to the farm until Submit is
//! pressed in it.
//!
//! The window runs in Houdini's bundled Python, the one that has PySide6
//! and `pxr` (`scripts/farm_launcher.py` finds it the same way: the
//! interpreter that matches `$HFS/houdini/python3.XXlibs`, with Houdini's
//! `site-packages-forced` put on its path). Cinedex is started by that
//! launcher or by Houdini, so `$HFS` and the pipeline's `PYTHONPATH` are in
//! its environment; where `$HFS` is not, [`launch`] says so and the menu
//! row is not offered.

use std::path::{Path, PathBuf};

/// What to run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Launch {
    pub program: PathBuf,
    pub args: Vec<String>,
    /// Folders to put first on `PYTHONPATH`.
    pub python_path: Vec<PathBuf>,
}

/// What [`launch`] asks of the disk.
pub trait Disk {
    /// The names in a folder.
    fn names(&self, folder: &Path) -> Vec<String>;
    fn is_file(&self, path: &Path) -> bool;
    fn is_dir(&self, path: &Path) -> bool;
}

/// This machine's.
#[cfg_attr(test, allow(dead_code))]
pub struct RealDisk;

impl Disk for RealDisk {
    fn names(&self, folder: &Path) -> Vec<String> {
        std::fs::read_dir(folder)
            .map(|entries| entries.filter_map(Result::ok).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default()
    }
    fn is_file(&self, path: &Path) -> bool {
        path.is_file()
    }
    fn is_dir(&self, path: &Path) -> bool {
        path.is_dir()
    }
}

/// The minor of the Python Houdini itself runs: the `XX` of the last
/// `python3.XXlibs` in `$HFS/houdini`.
fn minor(hfs: &Path, disk: &impl Disk) -> Option<u32> {
    disk.names(&hfs.join("houdini"))
        .iter()
        .filter_map(|name| name.strip_prefix("python3.")?.strip_suffix("libs")?.parse::<u32>().ok())
        .max()
}

/// Houdini's bundled interpreter under `hfs`, in each platform's layout.
pub fn houdini_python(hfs: &Path, disk: &impl Disk) -> Result<PathBuf, String> {
    let minor =
        minor(hfs, disk).ok_or_else(|| format!("no python3.*libs under {}: is it a Houdini install?", hfs.join("houdini").display()))?;
    let mut candidates = vec![
        hfs.join(format!("python3{minor}")).join("python.exe"),
        hfs.join("python").join("bin").join(format!("python3.{minor}")),
        hfs.join("python").join("bin").join("python3"),
    ];
    // macOS: HFS is <app>/Frameworks/Houdini.framework/Versions/<v>/Resources
    // and Python.framework sits beside Houdini.framework.
    if let Some(frameworks) = hfs.ancestors().nth(4) {
        candidates.push(
            frameworks.join("Python.framework").join("Versions").join(format!("3.{minor}")).join("bin").join(format!("python3.{minor}")),
        );
    }
    candidates
        .into_iter()
        .find(|c| disk.is_file(c))
        .ok_or_else(|| format!("Houdini's Python 3.{minor} was not found under {}", hfs.display()))
}

/// Where Houdini keeps PySide6 for `python`, which is not on its own path.
pub fn forced_site_packages(python: &Path, disk: &impl Disk) -> Vec<PathBuf> {
    let Some(bin) = python.parent() else { return Vec::new() };
    let mut candidates = vec![bin.join("lib").join("site-packages-forced")];
    if let Some(prefix) = bin.parent() {
        let lib = prefix.join("lib");
        let mut versions: Vec<String> = disk.names(&lib).into_iter().filter(|n| n.starts_with("python3")).collect();
        versions.sort();
        candidates.extend(versions.into_iter().map(|v| lib.join(v).join("site-packages-forced")));
    }
    candidates.into_iter().filter(|c| disk.is_dir(c)).collect()
}

pub const NO_HOUDINI: &str = "Farm Submit needs Houdini: HFS is not set (start Cinedex from TumbleTrove Desktop or from Houdini)";
pub const NO_SHOTS: &str = "no shot to submit";

/// What opens Farm Submit ticked on `shots` (their URIs, each once, in
/// that order), with Houdini at `hfs`.
pub fn launch(hfs: Option<&Path>, shots: &[String], disk: &impl Disk) -> Result<Launch, String> {
    let hfs = hfs.filter(|h| !h.as_os_str().is_empty()).ok_or(NO_HOUDINI)?;
    let mut uris: Vec<&String> = Vec::new();
    for shot in shots {
        if !uris.contains(&shot) {
            uris.push(shot);
        }
    }
    if uris.is_empty() {
        return Err(NO_SHOTS.into());
    }
    let program = houdini_python(hfs, disk)?;
    let mut args: Vec<String> = ["-m", "tumblepipe.farm_app", "--context", "shots"].map(String::from).into();
    for uri in uris {
        args.push("--entity".into());
        args.push(uri.clone());
    }
    let python_path = forced_site_packages(&program, disk);
    Ok(Launch { program, args, python_path })
}

/// Start `launch` and leave it running on its own.
#[cfg_attr(test, allow(dead_code))]
pub fn start(launch: &Launch) -> Result<(), String> {
    let mut command = std::process::Command::new(&launch.program);
    command.args(&launch.args).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    let mut path: Vec<PathBuf> = launch.python_path.clone();
    path.extend(std::env::var_os("PYTHONPATH").iter().flat_map(std::env::split_paths));
    if let Ok(joined) = std::env::join_paths(path) {
        command.env("PYTHONPATH", joined);
    }
    command.env("PYTHONUTF8", "1");
    command.spawn().map(drop).map_err(|e| format!("{}: {e}", launch.program.display()))
}

/// A Houdini at `/hfs` for the editor's tests, which start nothing.
#[cfg(test)]
pub struct TestDisk;

#[cfg(test)]
impl Disk for TestDisk {
    fn names(&self, folder: &Path) -> Vec<String> {
        if folder == Path::new("/hfs/houdini") { vec!["python3.11libs".into()] } else { Vec::new() }
    }
    fn is_file(&self, path: &Path) -> bool {
        path == Path::new("/hfs/python/bin/python3.11")
    }
    fn is_dir(&self, _: &Path) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use proptest::prelude::*;

    use super::*;

    /// A disk that holds the files and folders named.
    #[derive(Default)]
    struct Fake {
        files: BTreeSet<PathBuf>,
        dirs: BTreeSet<PathBuf>,
    }

    impl Fake {
        fn file(&mut self, path: PathBuf) {
            let mut at = path.parent();
            while let Some(dir) = at {
                self.dirs.insert(dir.to_path_buf());
                at = dir.parent();
            }
            self.files.insert(path);
        }
        fn dir(&mut self, path: PathBuf) {
            self.file(path.join("x"));
        }
    }

    impl Disk for Fake {
        fn names(&self, folder: &Path) -> Vec<String> {
            self.files
                .iter()
                .chain(&self.dirs)
                .filter(|p| p.parent() == Some(folder))
                .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect()
        }
        fn is_file(&self, path: &Path) -> bool {
            self.files.contains(path)
        }
        fn is_dir(&self, path: &Path) -> bool {
            self.dirs.contains(path)
        }
    }

    /// A Houdini install in one platform's layout, with the interpreters
    /// of `minors` and the libs of the last: (HFS, the python to find,
    /// its PySide6 folder).
    fn install(layout: usize, minors: &BTreeSet<u32>, forced: bool) -> (Fake, PathBuf, PathBuf, PathBuf) {
        let mut disk = Fake::default();
        let runs = *minors.iter().next_back().unwrap();
        let hfs = match layout {
            0 => PathBuf::from("C:/Program Files/Side Effects Software/Houdini 22.0.457"),
            1 => PathBuf::from("/opt/hfs22.0.457"),
            _ => PathBuf::from("/Applications/Houdini/Houdini22.0.457/Frameworks/Houdini.framework/Versions/22.0/Resources"),
        };
        disk.dir(hfs.join("houdini").join(format!("python3.{runs}libs")));
        // An older one's libs left beside it do not make it the one that runs.
        if minors.len() > 1 {
            disk.dir(hfs.join("houdini").join(format!("python3.{}libs", minors.iter().next().unwrap())));
        }
        let python_of = |minor: u32| match layout {
            0 => hfs.join(format!("python3{minor}")).join("python.exe"),
            1 => hfs.join("python").join("bin").join(format!("python3.{minor}")),
            _ => PathBuf::from(format!(
                "/Applications/Houdini/Houdini22.0.457/Frameworks/Python.framework/Versions/3.{minor}/bin/python3.{minor}"
            )),
        };
        for &minor in minors {
            disk.file(python_of(minor));
        }
        let python = python_of(runs);
        let site = match layout {
            0 => python.parent().unwrap().join("lib").join("site-packages-forced"),
            _ => python.parent().unwrap().parent().unwrap().join("lib").join(format!("python3.{runs}")).join("site-packages-forced"),
        };
        if forced {
            disk.dir(site.clone());
        }
        (disk, hfs, python, site)
    }

    proptest! {
        /// In each platform's layout the window is started in the Python
        /// Houdini itself runs (not another it ships), with PySide6's folder
        /// on its path when there is one, as `tumblepipe.farm_app` on the
        /// shots given: each once, in order, and nothing else. It is the
        /// window that is asked for: no argument submits.
        #[test]
        fn farm_submit_opens_the_window_on_the_shots_given(
            layout in 0usize..3, minors in prop::collection::btree_set(9u32..15, 1..4), forced in any::<bool>(),
            shots in prop::collection::vec(0usize..5, 1..7),
        ) {
            let (disk, hfs, python, site) = install(layout, &minors, forced);
            let uris: Vec<String> = shots.iter().map(|i| format!("entity:/shots/S1/sh{i}")).collect();
            let launch = launch(Some(&hfs), &uris, &disk).unwrap();
            prop_assert_eq!(&launch.program, &python);
            prop_assert_eq!(&launch.python_path, &if forced { vec![site] } else { Vec::new() });
            prop_assert_eq!(&launch.args[..4], ["-m", "tumblepipe.farm_app", "--context", "shots"]);
            let mut once: Vec<String> = Vec::new();
            for uri in &uris {
                if !once.contains(uri) {
                    once.push(uri.clone());
                }
            }
            let expected: Vec<String> = once.iter().flat_map(|uri| ["--entity".to_owned(), uri.clone()]).collect();
            prop_assert_eq!(&launch.args[4..], &expected[..]);
        }
    }

    #[test]
    fn without_houdini_or_shots_it_says_why() {
        let (disk, hfs, ..) = install(1, &[11].into(), true);
        let shot = vec!["entity:/shots/S1/sh1".to_owned()];
        assert_eq!(launch(None, &shot, &disk), Err(NO_HOUDINI.into()));
        assert_eq!(launch(Some(Path::new("")), &shot, &disk), Err(NO_HOUDINI.into()));
        assert_eq!(launch(Some(&hfs), &[], &disk), Err(NO_SHOTS.into()));
        assert!(launch(Some(Path::new("/opt/nothing")), &shot, &disk).unwrap_err().contains("python3.*libs"));
        let mut gone = Fake::default();
        gone.dir(hfs.join("houdini").join("python3.11libs"));
        assert!(launch(Some(&hfs), &shot, &gone).unwrap_err().contains("Python 3.11 was not found"));
    }
}
