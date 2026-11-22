//! The panel sizes Cinedex remembers for this user between sessions: the
//! bins' width, the timeline's height, whether either is folded to a
//! strip, and whether the bins show as a grid of thumbnails or a list. One
//! small JSON file per user, on local disk:
//! `%LOCALAPPDATA%/tumblepipe/cinedex/layout.json` on Windows,
//! `$XDG_CONFIG_HOME` (or `~/.config`) on Linux, `~/Library/Application
//! Support` on macOS, beside TumblePipe's other per-user files.
//!
//! A size is kept as the user left it, not as it fits: the panels clamp to
//! the window when they are laid out (`primer::core::split`), so a size saved
//! on a big screen opens squeezed on a small one, and is whole again on the
//! big one. Only nonsense (not a number, or beyond any screen) is dropped
//! here, for the default.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

/// The bins' width before the user changes it.
pub const BINS_WIDTH: f32 = 260.0;
/// The narrowest and widest a saved bins width is believed.
const BINS_RANGE: (f32, f32) = (120.0, 4000.0);
/// The lowest and tallest a saved timeline height is believed.
const TIMELINE_RANGE: (f32, f32) = (40.0, 4000.0);

/// The remembered panel sizes, in logical pixels.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Layout {
    /// The bins panel's width.
    pub bins_width: f32,
    /// The bins are folded to a strip.
    pub bins_collapsed: bool,
    /// The timeline's height (the clip fields and the tracks); None until
    /// the user drags it, when it fits the tracks.
    pub timeline_height: Option<f32>,
    /// The timeline is folded to a strip.
    pub timeline_collapsed: bool,
    /// The bins show as a grid of thumbnails; else as a list.
    pub bins_grid: bool,
}

impl Default for Layout {
    fn default() -> Self {
        Self { bins_width: BINS_WIDTH, bins_collapsed: false, timeline_height: None, timeline_collapsed: false, bins_grid: false }
    }
}

/// `value` when it is a number within `range`, else None.
fn believed(value: Option<f64>, range: (f32, f32)) -> Option<f32> {
    let value = value? as f32;
    (value.is_finite() && value >= range.0 && value <= range.1).then_some(value)
}

impl Layout {
    /// The layout a file's JSON says, each field it does not say (or says
    /// nonsense for) at its default.
    pub fn from_json(value: &Value) -> Self {
        let default = Self::default();
        let flag = |key: &str, or: bool| value.get(key).and_then(Value::as_bool).unwrap_or(or);
        Self {
            bins_width: believed(value.get("bins_width").and_then(Value::as_f64), BINS_RANGE).unwrap_or(default.bins_width),
            bins_collapsed: flag("bins_collapsed", default.bins_collapsed),
            timeline_height: believed(value.get("timeline_height").and_then(Value::as_f64), TIMELINE_RANGE),
            timeline_collapsed: flag("timeline_collapsed", default.timeline_collapsed),
            bins_grid: flag("bins_grid", default.bins_grid),
        }
    }

    pub fn to_json(self) -> Value {
        json!({
            "bins_width": self.bins_width,
            "bins_collapsed": self.bins_collapsed,
            "timeline_height": self.timeline_height,
            "timeline_collapsed": self.timeline_collapsed,
            "bins_grid": self.bins_grid,
        })
    }
}

/// The folder the layout file goes in. `var` reads the environment;
/// `platform` is Python's `sys.platform` (as `th_pipe::live::live_root`).
pub fn prefs_dir(var: impl Fn(&str) -> Option<String>, platform: &str) -> PathBuf {
    let set = |name: &str| var(name).filter(|v| !v.is_empty());
    let home = set("HOME").or_else(|| set("USERPROFILE")).map(PathBuf::from).or_else(std::env::home_dir).unwrap_or_default();
    let base = if platform.starts_with("win") {
        set("LOCALAPPDATA").map_or_else(|| home.join("AppData").join("Local"), PathBuf::from)
    } else if platform == "darwin" {
        home.join("Library").join("Application Support")
    } else {
        set("XDG_CONFIG_HOME").map_or_else(|| home.join(".config"), PathBuf::from)
    };
    base.join("tumblepipe").join("cinedex")
}

/// This user's layout file.
pub fn path() -> PathBuf {
    let platform = if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    };
    prefs_dir(|name| std::env::var(name).ok(), platform).join("layout.json")
}

/// The layout saved at `path`; the default when there is none, or it does
/// not read.
pub fn load(path: &Path) -> Layout {
    th_pipe::io::load_or_default(path, Layout::from_json)
}

/// Save `layout` at `path`, whole: written beside it and renamed over it,
/// so a second Cinedex reading it never sees half a file.
pub fn save(path: &Path, layout: Layout) -> std::io::Result<()> {
    th_pipe::io::store_pretty_json(path, &layout.to_json())
}

/// The file the folded sequences of the Shots bin are remembered in, beside
/// the layout file at `layout`: `{"<project>": ["<sequence>", ...]}`, a
/// project by its root. A sequence's name is folded by the chevron before
/// it; one that is not listed shows its shots.
pub fn folded_path(layout: &Path) -> PathBuf {
    layout.with_file_name("folded.json")
}

/// The sequences `value` (the file's JSON) says are folded in `project`;
/// none when it says nothing, or nonsense.
pub fn folded_from_json(value: &Value, project: &str) -> BTreeSet<String> {
    value
        .get(project)
        .and_then(Value::as_array)
        .map(|names| names.iter().filter_map(Value::as_str).map(str::to_owned).collect())
        .unwrap_or_default()
}

/// `value` with `project`'s folded sequences set to `folded`, the other
/// projects' kept. A project with none folded is left out; a file that was
/// not an object starts again.
pub fn folded_into_json(value: Value, project: &str, folded: &BTreeSet<String>) -> Value {
    let mut all = match value {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    if folded.is_empty() {
        all.remove(project);
    } else {
        all.insert(project.to_owned(), json!(folded));
    }
    Value::Object(all)
}

/// The sequences folded in `project`, as the file at `path` says.
pub fn load_folded(path: &Path, project: &str) -> BTreeSet<String> {
    th_pipe::io::load_or_default(path, |value| folded_from_json(value, project))
}

/// Remember `folded` for `project` in the file at `path`.
pub fn save_folded(path: &Path, project: &str, folded: &BTreeSet<String>) -> std::io::Result<()> {
    let all = th_pipe::io::load_or_default(path, Value::clone);
    th_pipe::io::store_pretty_json(path, &folded_into_json(all, project, folded))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use proptest::prelude::*;

    /// The spec every prefs file's saver meets, `save` writing `json` to
    /// the path it is given: into a folder it makes, whole, as serde_json's
    /// pretty print of `json`, and nothing else beside it; saved again, the
    /// same. (When it cannot be written, see [`nothing_left_when_blocked`].)
    pub(crate) fn saved_whole(save: &dyn Fn(&Path) -> std::io::Result<()>, json: &Value) -> Result<(), TestCaseError> {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("nested");
        let path = folder.join("prefs.json");
        let pretty = serde_json::to_vec_pretty(json).unwrap();
        for _ in 0..2 {
            prop_assert!(save(&path).is_ok());
            prop_assert_eq!(std::fs::read(&path).unwrap(), pretty.clone());
            let names: Vec<_> = std::fs::read_dir(&folder).unwrap().map(|e| e.unwrap().file_name()).collect();
            prop_assert_eq!(names, vec![std::ffi::OsString::from("prefs.json")]);
        }
        Ok(())
    }

    /// The other half of the spec: where a folder is in the way, `save`
    /// fails, leaving nothing behind. Not a property: on Windows the
    /// failed rename is retried for th_pipe::io::REPLACE_TIMEOUT.
    pub(crate) fn nothing_left_when_blocked(save: &dyn Fn(&Path) -> std::io::Result<()>) {
        let dir = tempfile::tempdir().unwrap();
        let blocked = dir.path().join("blocked.json");
        std::fs::create_dir(&blocked).unwrap();
        assert!(save(&blocked).is_err());
        let names: Vec<_> = std::fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(names, vec![std::ffi::OsString::from("blocked.json")]);
        assert_eq!(std::fs::read_dir(&blocked).unwrap().count(), 0);
    }

    #[test]
    fn nothing_is_left_when_the_layout_or_folded_file_is_blocked() {
        nothing_left_when_blocked(&|path| save(path, Layout::default()));
        nothing_left_when_blocked(&|path| save_folded(path, "p", &BTreeSet::from(["s".to_owned()])));
    }

    proptest! {
        #[test]
        fn the_layout_is_saved_whole(layout in layout()) {
            saved_whole(&|path| save(path, layout), &layout.to_json())?;
        }

        #[test]
        fn the_folded_sequences_are_saved_whole(
            project in "[a-zA-Z0-9/:_ -]{0,12}", folded in prop::collection::btree_set("[a-zA-Z0-9_ ]{0,8}", 0..5),
        ) {
            let json = folded_into_json(Value::Null, &project, &folded);
            saved_whole(&|path| save_folded(path, &project, &folded), &json)?;
        }
    }

    fn layout() -> impl Strategy<Value = Layout> {
        (
            BINS_RANGE.0..=BINS_RANGE.1,
            any::<bool>(),
            proptest::option::of(TIMELINE_RANGE.0..=TIMELINE_RANGE.1),
            any::<bool>(),
            any::<bool>(),
        )
            .prop_map(|(bins_width, bins_collapsed, timeline_height, timeline_collapsed, bins_grid)| Layout {
                bins_width,
                bins_collapsed,
                timeline_height,
                timeline_collapsed,
                bins_grid,
            })
    }

    /// Any JSON value a hand-edited or older file might hold for a field.
    fn field() -> impl Strategy<Value = Value> {
        prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::from),
            any::<f64>().prop_map(|f| serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)),
            (-1.0e6f64..1.0e6).prop_map(|f| serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)),
            "[a-z]{0,6}".prop_map(Value::from),
        ]
    }

    proptest! {
        #[test]
        fn a_saved_layout_loads_back_the_same(layout in layout()) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("nested").join("layout.json");
            save(&path, layout).unwrap();
            prop_assert_eq!(load(&path), layout);
            // Saving again replaces it, leaving no partial file behind.
            save(&path, layout).unwrap();
            prop_assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
        }

        #[test]
        fn any_file_loads_as_sizes_within_reason(
            bins in field(), bins_collapsed in field(), timeline in field(), timeline_collapsed in field(), bins_grid in field(),
        ) {
            let value = json!({
                "bins_width": bins,
                "bins_collapsed": bins_collapsed,
                "timeline_height": timeline,
                "timeline_collapsed": timeline_collapsed,
                "bins_grid": bins_grid,
            });
            let layout = Layout::from_json(&value);
            // The view is the file's when it says one, else the list.
            prop_assert_eq!(layout.bins_grid, bins_grid.as_bool().unwrap_or(false));
            prop_assert!(layout.bins_width >= BINS_RANGE.0 && layout.bins_width <= BINS_RANGE.1);
            if let Some(height) = layout.timeline_height {
                prop_assert!(height >= TIMELINE_RANGE.0 && height <= TIMELINE_RANGE.1);
            }
            // What loads saves and loads back unchanged.
            prop_assert_eq!(Layout::from_json(&layout.to_json()), layout);
        }
    }

    proptest! {
        /// Folding is remembered per project: what one project saves loads
        /// back for it, and every other project's is as it was, whatever
        /// the file held before (nothing, nonsense, other projects).
        #[test]
        fn folded_sequences_load_back_per_project(
            saves in prop::collection::vec((0usize..3, prop::collection::btree_set("[A-C][0-9]?", 0..4)), 1..8),
            before in prop::sample::select(vec!["", "{ not json", "[1, 2]", r#"{"P0": "x", "P9": ["Z"]}"#]),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let path = folded_path(&dir.path().join("deep").join("layout.json"));
            prop_assert_eq!(path.parent().map(Path::to_path_buf), Some(dir.path().join("deep")));
            if !before.is_empty() {
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(&path, before).unwrap();
            }
            // The oracle: a map, last save wins.
            let mut oracle: std::collections::BTreeMap<String, BTreeSet<String>> = Default::default();
            if before.contains("P9") {
                oracle.insert("P9".into(), ["Z".to_owned()].into());
            }
            for (project, folded) in saves {
                let project = format!("P{project}");
                save_folded(&path, &project, &folded).unwrap();
                oracle.insert(project, folded);
                for name in ["P0", "P1", "P2", "P9", "never"] {
                    prop_assert_eq!(load_folded(&path, name), oracle.get(name).cloned().unwrap_or_default(), "{}", name);
                }
            }
            prop_assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1, "a partial file was left");
        }
    }

    #[test]
    fn no_file_and_a_broken_file_load_the_default() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("layout.json");
        assert_eq!(load(&path), Layout::default());
        std::fs::write(&path, b"{ not json").unwrap();
        assert_eq!(load(&path), Layout::default());
        // A file from before the grid view (1.112.0) opens as a list, its
        // sizes kept.
        std::fs::write(&path, br#"{"bins_width": 300.0, "bins_collapsed": true, "timeline_height": null, "timeline_collapsed": false}"#)
            .unwrap();
        assert_eq!(load(&path), Layout { bins_width: 300.0, bins_collapsed: true, ..Layout::default() });
    }

    #[test]
    fn the_file_goes_with_this_users_other_tumblepipe_files() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |name: &str| pairs.iter().find(|(k, _)| *k == name).map(|(_, v)| (*v).to_owned())
        };
        assert_eq!(prefs_dir(env(&[("LOCALAPPDATA", "L:/app")]), "win32"), Path::new("L:/app/tumblepipe/cinedex"));
        assert_eq!(prefs_dir(env(&[("XDG_CONFIG_HOME", "/x"), ("HOME", "/h")]), "linux"), Path::new("/x/tumblepipe/cinedex"));
        assert_eq!(prefs_dir(env(&[("HOME", "/h")]), "linux"), Path::new("/h/.config/tumblepipe/cinedex"));
        assert_eq!(prefs_dir(env(&[("HOME", "/h")]), "darwin"), Path::new("/h/Library/Application Support/tumblepipe/cinedex"));
    }
}
