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
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .map_or_else(Layout::default, |v| Layout::from_json(&v))
}

/// Save `layout` at `path`, whole: written beside it and renamed over it,
/// so a second Cinedex reading it never sees half a file.
pub fn save(path: &Path, layout: Layout) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let partial = path.with_extension(format!("json.{}.partial", std::process::id()));
    std::fs::write(&partial, serde_json::to_vec_pretty(&layout.to_json()).map_err(std::io::Error::other)?)?;
    std::fs::rename(&partial, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&partial);
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

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
