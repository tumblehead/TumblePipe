//! Pure (GUI-free) core of the tt_setup wizard: project validation and the
//! template scaffold + config-DB customization. Kept separate from `main.rs`
//! so the byte-for-byte JSON parity with the old Python wizard can be pinned
//! by unit tests without spinning up a window.

pub mod beauty_alpha;
pub mod conventions;
pub mod migration;
pub mod templates;

use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::{json, Value};

/// Standard top-level project directories, created after the template copy.
///
/// `kits` used to be here, inherited from the very first commit. Nothing ever
/// resolved a `kits:` URI — its only mention was a comment in the legacy
/// Project Browser, retired in f310d35 — so every project created since has
/// carried an empty directory for a concept that never shipped.
pub const TOP_LEVEL_DIRS: [&str; 4] = ["assets", "shots", "groups", "export"];

// ---------- validation ------------------------------------------------------

/// Alphanumeric, non-empty — same rule as the Python `str.isalnum()` gate.
pub fn is_valid_project_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(char::is_alphanumeric)
}

/// A directory looks like a project when it has `_config/db/entity.json`.
pub fn looks_like_project(path: &Path) -> bool {
    path.join("_config").join("db").join("entity.json").is_file()
}

/// Files an OS drops into a folder on its own. A folder holding only these
/// still counts as empty, so browsing to a fresh folder in Explorer or Finder
/// (which may write `desktop.ini` / `.DS_Store`) doesn't refuse it.
const OS_LITTER: [&str; 3] = [".DS_Store", "desktop.ini", "Thumbs.db"];

/// What a browsed-to folder would become. The setup app has one path field,
/// and this is what decides between "use it" and "create a project in it".
#[derive(Debug, PartialEq, Eq)]
pub enum FolderKind {
    /// Already a project (`_config/db/entity.json`): point TumblePipe at it.
    Project,
    /// An empty directory, or a path whose parent exists: scaffold here.
    NewProject,
    /// A file, not a directory.
    NotADirectory,
    /// A directory with other contents that isn't a project. Most often a
    /// sub-folder of a project, or the folder *above* one.
    NotEmpty,
    /// Neither the path nor its parent exists.
    ParentMissing,
}

pub fn classify_folder(path: &Path) -> FolderKind {
    if path.is_dir() {
        if looks_like_project(path) {
            FolderKind::Project
        } else if is_effectively_empty(path) {
            FolderKind::NewProject
        } else {
            FolderKind::NotEmpty
        }
    } else if path.exists() {
        FolderKind::NotADirectory
    } else if path.parent().is_some_and(Path::is_dir) {
        FolderKind::NewProject
    } else {
        FolderKind::ParentMissing
    }
}

/// The project a folder sits inside, when it is a sub-folder of one
/// (`<project>/assets`): the commonest wrong pick after the folder above.
pub fn enclosing_project(path: &Path) -> Option<PathBuf> {
    path.ancestors().skip(1).find(|a| looks_like_project(a)).map(Path::to_path_buf)
}

/// The projects directly inside a folder, sorted, for when someone picks the
/// folder *above* their project (`P:\Projects` instead of
/// `P:\Projects\HideAndReek`).
pub fn projects_in(path: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(path) else {
        return Vec::new();
    };
    let mut projects: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|child| child.is_dir() && looks_like_project(child))
        .collect();
    projects.sort();
    projects
}

fn is_effectively_empty(path: &Path) -> bool {
    match std::fs::read_dir(path) {
        Ok(entries) => entries.flatten().all(|entry| {
            OS_LITTER.contains(&entry.file_name().to_string_lossy().as_ref())
        }),
        Err(_) => false,
    }
}

/// The name a new project gets, which becomes its farm pool. Nobody is asked
/// to type it: the user already named the project in TumbleTrove Desktop, so
/// `desktop_name` (`TT_PROJECT_NAME`) wins when the desktop passes it, and
/// the folder's name stands in when it doesn't.
pub fn project_name_for(desktop_name: Option<&str>, path: &Path) -> Option<String> {
    desktop_name
        .and_then(sanitize_project_name)
        .or_else(|| sanitize_project_name(&path.file_name()?.to_string_lossy()))
}

/// Drop everything but letters and digits (`Hide And Reek` -> `HideAndReek`);
/// `None` when nothing is left.
pub fn sanitize_project_name(text: &str) -> Option<String> {
    let name: String = text.chars().filter(|c| c.is_alphanumeric()).collect();
    is_valid_project_name(&name).then_some(name)
}

/// Expand a leading `~` to the user's home directory (mirrors
/// `Path.expanduser()` for the common cases the wizard sees).
pub fn expanduser(text: &str) -> PathBuf {
    if let Some(rest) = text.strip_prefix('~') {
        if rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\') {
            if let Some(home) = home_dir() {
                let rest = rest.trim_start_matches(['/', '\\']);
                return if rest.is_empty() { home } else { home.join(rest) };
            }
        }
    }
    PathBuf::from(text)
}

pub fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

// ---------- the bundled template -------------------------------------------

/// The package-relative location of the project template.
const TEMPLATE_REL: &str = "scripts/project_template";

/// The bundled `scripts/project_template/` the hooks scaffold and migrate
/// from, for `tt_setup` and `tt_prepare` alike.
///
/// The first of these that is a directory: an explicit `--template-dir`; the
/// template beside the running binary (`bin/<platform>/<exe>` →
/// `<root>/scripts/project_template`); `$HPM_PACKAGE_ROOT`'s; the working
/// directory's. The binary's own package comes before the environment and
/// the working directory because it is the one fact about where the package
/// is that the caller cannot get wrong: `tt_prepare` used to fall back to a
/// cwd-relative path straight after `HPM_PACKAGE_ROOT`, and a migration run
/// from anywhere else reported its own packaged `storage.json` as missing
/// ("the TumblePipe package is incomplete") while the file sat in the
/// package. An explicit path that is not a directory falls through too, so
/// a wrong `--template-dir` is repaired rather than reported. When nothing
/// exists, the explicit path, else the binary-relative one, names what was
/// expected in the error that follows.
pub fn bundled_template_dir(explicit: Option<&Path>) -> PathBuf {
    template_dir_among(
        explicit,
        std::env::current_exe().ok().as_deref(),
        std::env::var_os("HPM_PACKAGE_ROOT").map(PathBuf::from).as_deref(),
        |p| p.is_dir(),
    )
}

/// [`bundled_template_dir`] with its inputs passed in, so the order is
/// testable without a real executable or environment.
pub fn template_dir_among(
    explicit: Option<&Path>,
    exe: Option<&Path>,
    package_root: Option<&Path>,
    is_dir: impl Fn(&Path) -> bool,
) -> PathBuf {
    let beside_exe = exe
        .and_then(Path::parent)
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(|root| root.join(TEMPLATE_REL));
    let candidates = [
        explicit.map(Path::to_path_buf),
        beside_exe.clone(),
        package_root.map(|root| root.join(TEMPLATE_REL)),
        Some(PathBuf::from(TEMPLATE_REL)),
    ];
    if let Some(found) = candidates.iter().flatten().find(|p| is_dir(p)) {
        return found.clone();
    }
    explicit
        .map(Path::to_path_buf)
        .or(beside_exe)
        .unwrap_or_else(|| PathBuf::from(TEMPLATE_REL))
}

// ---------- scaffolding -----------------------------------------------------

/// Copy the template, create the top-level dirs, and customize the config DBs.
/// `target` may be missing or an empty folder the user picked. On any failure
/// the partial copy is removed so a retry starts clean — the folder itself
/// too when this created it, only its contents when the user chose it.
pub fn scaffold_project(
    template_dir: &Path,
    target: &Path,
    project_name: &str,
    fps: i64,
) -> Result<(), String> {
    if !template_dir.is_dir() {
        return Err(format!(
            "Bundled project template not found at {}. The TumblePipe package is incomplete.",
            template_dir.display()
        ));
    }

    let existed = target.is_dir();
    let result = (|| -> Result<(), String> {
        copy_template_tree(template_dir, target)
            .map_err(|e| format!("Failed to copy template: {e}"))?;
        for sub in TOP_LEVEL_DIRS {
            std::fs::create_dir_all(target.join(sub))
                .map_err(|e| format!("Failed to create '{sub}' directory: {e}"))?;
        }
        customize_template(target, project_name, fps)
    })();

    if result.is_err() {
        if existed {
            remove_scaffold_from(target);
        } else {
            let _ = std::fs::remove_dir_all(target);
        }
    }
    result
}

/// Undo a failed scaffold inside a folder the user picked: it was empty but
/// for OS litter, so everything else in it came from the scaffold.
fn remove_scaffold_from(target: &Path) {
    let Ok(entries) = std::fs::read_dir(target) else {
        return;
    };
    for entry in entries.flatten() {
        if OS_LITTER.contains(&entry.file_name().to_string_lossy().as_ref()) {
            continue;
        }
        let path = entry.path();
        let _ = if path.is_dir() {
            std::fs::remove_dir_all(&path)
        } else {
            std::fs::remove_file(&path)
        };
    }
}

/// Copy `template_dir` -> `target`, skipping `__pycache__/`, `*.pyc`, `*.bak`
/// (the same carve-outs as the Python `shutil.ignore_patterns`). `target` must
/// not already exist.
pub fn copy_template_tree(template_dir: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(target)?;
    for entry in std::fs::read_dir(template_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str == "__pycache__" || name_str.ends_with(".pyc") || name_str.ends_with(".bak") {
            continue;
        }
        let src = entry.path();
        let dst = target.join(&name);
        if entry.file_type()?.is_dir() {
            copy_template_tree(&src, &dst)?;
        } else {
            std::fs::copy(&src, &dst)?;
        }
    }
    Ok(())
}

/// Patch the three config databases in-place with the new project's name and
/// fps, preserving key order and formatting.
pub fn customize_template(project_path: &Path, project_name: &str, fps: i64) -> Result<(), String> {
    let db = project_path.join("_config").join("db");

    let entity_path = db.join("entity.json");
    let mut entity = load_json(&entity_path)?;
    patch_entity(&mut entity, project_name);
    store_json(&entity_path, &entity)?;

    let config_path = db.join("config.json");
    let mut config = load_json(&config_path)?;
    patch_config(&mut config, fps);
    store_json(&config_path, &config)?;

    let schemas_path = db.join("schemas.json");
    let mut schemas = load_json(&schemas_path)?;
    patch_schemas(&mut schemas, fps);
    store_json(&schemas_path, &schemas)?;

    Ok(())
}

/// entity.json: the project's own farm pools default to its name.
pub fn patch_entity(entity: &mut Value, project_name: &str) {
    let farm = ensure_object(entity, &["properties", "farm"]);
    farm.insert("pools".to_string(), json!([project_name]));
    farm.insert("default_pool".to_string(), json!(project_name));
}

/// config.json: project fps.
pub fn patch_config(config: &mut Value, fps: i64) {
    let props = ensure_object(config, &["children", "project", "properties"]);
    props.insert("fps".to_string(), json!(fps));
}

/// schemas.json: the fps default appears in two places.
pub fn patch_schemas(schemas: &mut Value, fps: i64) {
    ensure_object(schemas, &["children", "entity", "properties"])
        .insert("fps".to_string(), json!(fps));
    ensure_object(
        schemas,
        &["children", "config", "children", "project", "properties"],
    )
    .insert("fps".to_string(), json!(fps));
}

/// Navigate/create a nested-object path and return the leaf object, creating
/// missing intermediates as empty objects at the end of their parent — the
/// same behavior as chained Python `dict.setdefault({})`. `serde_json::Map`'s
/// insert keeps an existing key's position, so patching a value that already
/// exists never reorders the file.
fn ensure_object<'a>(root: &'a mut Value, path: &[&str]) -> &'a mut serde_json::Map<String, Value> {
    let mut cur = root
        .as_object_mut()
        .expect("config DB root must be a JSON object");
    for key in path {
        cur = cur
            .entry((*key).to_string())
            .or_insert_with(|| Value::Object(serde_json::Map::new()))
            .as_object_mut()
            .expect("config DB path element must be a JSON object");
    }
    cur
}

pub fn load_json(path: &Path) -> Result<Value, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("Could not read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("Invalid JSON in {}: {e}", path.display()))
}

/// Serialize with 4-space indentation and a single trailing newline, matching
/// the Python `json.dump(data, fh, indent=4)` + `fh.write('\n')` output.
pub fn to_json_string(value: &Value) -> String {
    let mut buf = Vec::new();
    let formatter = PythonFormatter(serde_json::ser::PrettyFormatter::with_indent(b"    "));
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    value.serialize(&mut ser).expect("Value serialization");
    buf.push(b'\n');
    String::from_utf8(buf).expect("serde_json emits UTF-8")
}

/// `PrettyFormatter`, except that string contents are escaped the way
/// Python's `json.dump` escapes them by default (`ensure_ascii=True`): every
/// character outside printable ASCII as `\uXXXX` (DEL included), and
/// anything past the BMP as a UTF-16 surrogate pair. `serde_json` writes raw UTF-8 instead, so without this
/// every file a migration rewrote turned Python's `"Søren"` into
/// `"Søren"` — a diff on every non-ASCII value, and bytes that a Python
/// reader opening the file in the Windows locale code page decodes wrong.
/// The escapes JSON itself requires (quote, backslash, controls) are left to
/// `serde_json`, whose spellings already match Python's.
struct PythonFormatter<'a>(serde_json::ser::PrettyFormatter<'a>);

impl serde_json::ser::Formatter for PythonFormatter<'_> {
    fn write_string_fragment<W: ?Sized + std::io::Write>(
        &mut self,
        writer: &mut W,
        fragment: &str,
    ) -> std::io::Result<()> {
        let mut units = [0u16; 2];
        for c in fragment.chars() {
            if c.is_ascii() && c != '\x7f' {
                writer.write_all(&[c as u8])?;
            } else {
                for unit in c.encode_utf16(&mut units) {
                    write!(writer, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }

    fn begin_array<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_array(w)
    }

    fn end_array<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_array(w)
    }

    fn begin_array_value<W: ?Sized + std::io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> std::io::Result<()> {
        self.0.begin_array_value(w, first)
    }

    fn end_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_array_value(w)
    }

    fn begin_object<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_object(w)
    }

    fn end_object<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_object(w)
    }

    fn begin_object_key<W: ?Sized + std::io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> std::io::Result<()> {
        self.0.begin_object_key(w, first)
    }

    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_object_value(w)
    }

    fn end_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_object_value(w)
    }
}

pub fn store_json(path: &Path, value: &Value) -> Result<(), String> {
    std::fs::write(path, to_json_string(value))
        .map_err(|e| format!("Could not write {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every combination of which candidates exist, with and without an
    /// explicit path: exhaustive over the four locations, so the order is
    /// pinned entirely rather than by example.
    #[test]
    fn the_template_is_the_first_location_that_exists() {
        let exe = Path::new("/pkg/bin/macos-aarch64/tt_prepare");
        let explicit = PathBuf::from("/given/template");
        let beside = PathBuf::from("/pkg/scripts/project_template");
        let from_env = PathBuf::from("/env/scripts/project_template");
        let cwd = PathBuf::from("scripts/project_template");
        for with_explicit in [false, true] {
            for mask in 0u8..16 {
                let exists = |p: &Path| {
                    (mask & 1 != 0 && p == explicit)
                        || (mask & 2 != 0 && p == beside)
                        || (mask & 4 != 0 && p == from_env)
                        || (mask & 8 != 0 && p == cwd)
                };
                let got = template_dir_among(
                    with_explicit.then_some(explicit.as_path()),
                    Some(exe),
                    Some(Path::new("/env")),
                    exists,
                );
                let order = [
                    (with_explicit && mask & 1 != 0, &explicit),
                    (mask & 2 != 0, &beside),
                    (mask & 4 != 0, &from_env),
                    (mask & 8 != 0, &cwd),
                ];
                let want = order
                    .iter()
                    .find(|(ok, _)| *ok)
                    .map(|(_, p)| (*p).clone())
                    .unwrap_or_else(|| if with_explicit { explicit.clone() } else { beside.clone() });
                assert_eq!(got, want, "explicit={with_explicit} mask={mask:04b}");
            }
        }
    }

    /// The reported failure: run from outside the package with no
    /// `--template-dir` and no `HPM_PACKAGE_ROOT`, the template beside the
    /// binary is found, never a cwd-relative path that does not exist.
    #[test]
    fn a_run_from_elsewhere_finds_the_template_beside_the_binary() {
        let pkg = tempfile::tempdir().unwrap();
        let template = pkg.path().join("scripts").join("project_template");
        std::fs::create_dir_all(&template).unwrap();
        let exe = pkg.path().join("bin").join("macos-aarch64").join("tt_prepare");
        let got = template_dir_among(None, Some(&exe), None, |p| p.is_dir());
        assert_eq!(got, template);
    }

    #[test]
    fn valid_project_names() {
        assert!(is_valid_project_name("myfilm"));
        assert!(is_valid_project_name("Film2"));
        assert!(!is_valid_project_name(""));
        assert!(!is_valid_project_name("my film"));
        assert!(!is_valid_project_name("my-film"));
        assert!(!is_valid_project_name("my_film"));
    }

    #[test]
    fn project_name_prefers_the_desktop_then_the_folder() {
        let folder = Path::new("/p/my_folder");
        assert_eq!(project_name_for(Some("Hide And-Reek 2"), folder).as_deref(), Some("HideAndReek2"));
        assert_eq!(project_name_for(None, folder).as_deref(), Some("myfolder"));
        // A desktop name with nothing usable in it falls back to the folder.
        assert_eq!(project_name_for(Some("--"), folder).as_deref(), Some("myfolder"));
        assert_eq!(project_name_for(None, Path::new("/p/__")), None);
    }

    #[test]
    fn classify_folder_decides_use_or_create() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();

        let project = root.join("existing");
        std::fs::create_dir_all(project.join("_config").join("db")).unwrap();
        std::fs::write(project.join("_config").join("db").join("entity.json"), "{}").unwrap();
        assert_eq!(classify_folder(&project), FolderKind::Project);

        let empty = root.join("empty");
        std::fs::create_dir(&empty).unwrap();
        std::fs::write(empty.join("desktop.ini"), "").unwrap();
        assert_eq!(classify_folder(&empty), FolderKind::NewProject);

        assert_eq!(classify_folder(&root.join("fresh")), FolderKind::NewProject);
        assert_eq!(classify_folder(&root.join("a").join("b")), FolderKind::ParentMissing);
        assert_eq!(classify_folder(&project.join("_config")), FolderKind::NotEmpty);
        assert_eq!(
            classify_folder(&project.join("_config").join("db").join("entity.json")),
            FolderKind::NotADirectory
        );
    }

    #[test]
    fn a_wrong_pick_points_at_the_project() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        for name in ["beta", "alpha"] {
            let db = root.join(name).join("_config").join("db");
            std::fs::create_dir_all(&db).unwrap();
            std::fs::write(db.join("entity.json"), "{}").unwrap();
        }
        std::fs::create_dir_all(root.join("alpha").join("assets").join("CHAR")).unwrap();
        std::fs::create_dir(root.join("notes")).unwrap();

        // The folder above: its projects, sorted; plain folders are not offered.
        assert_eq!(projects_in(root), vec![root.join("alpha"), root.join("beta")]);
        assert!(projects_in(&root.join("notes")).is_empty());

        // A folder inside a project, at any depth; a project is not inside itself.
        let deep = root.join("alpha").join("assets").join("CHAR");
        assert_eq!(enclosing_project(&deep), Some(root.join("alpha")));
        assert_eq!(enclosing_project(&root.join("alpha")), None);
        assert_eq!(enclosing_project(&root.join("notes")), None);
    }

    #[test]
    fn failed_scaffold_into_a_picked_folder_keeps_the_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let template = tmp.path().join("template");
        std::fs::create_dir(&template).unwrap();
        std::fs::write(template.join("stray.txt"), "x").unwrap(); // no _config/db -> customize fails
        let picked = tmp.path().join("picked");
        std::fs::create_dir(&picked).unwrap();
        std::fs::write(picked.join(".DS_Store"), "").unwrap();

        assert!(scaffold_project(&template, &picked, "picked", 24).is_err());
        assert!(picked.is_dir());
        let left: Vec<_> = std::fs::read_dir(&picked).unwrap().flatten().map(|e| e.file_name()).collect();
        assert_eq!(left, vec![std::ffi::OsString::from(".DS_Store")]);
    }

    #[test]
    fn entity_patch_replaces_pools_in_place_and_keeps_order() {
        let src = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"default\"\n            ],\n            \"default_pool\": \"default\"\n        }\n    },\n    \"children\": {}\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_entity(&mut v, "myfilm");
        let expected = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"myfilm\"\n            ],\n            \"default_pool\": \"myfilm\"\n        }\n    },\n    \"children\": {}\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn config_patch_sets_fps() {
        let src = "{\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 24\n            },\n            \"children\": {}\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_config(&mut v, 30);
        let expected = "{\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 30\n            },\n            \"children\": {}\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn schemas_patch_sets_fps_in_both_places() {
        let src = "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {\n                \"fps\": 24\n            }\n        },\n        \"config\": {\n            \"children\": {\n                \"project\": {\n                    \"properties\": {\n                        \"fps\": 30\n                    }\n                }\n            }\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_schemas(&mut v, 48);
        let expected = "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {\n                \"fps\": 48\n            }\n        },\n        \"config\": {\n            \"children\": {\n                \"project\": {\n                    \"properties\": {\n                        \"fps\": 48\n                    }\n                }\n            }\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn untouched_floats_and_ints_round_trip_verbatim() {
        // arbitrary_precision keeps 0.0 / 0.5 / 1920 as their original tokens
        // instead of reformatting them — a naive f64 round-trip risks 0.0 -> 0.
        let src = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"default\"\n            ],\n            \"default_pool\": \"default\"\n        },\n        \"render\": {\n            \"resolution\": [\n                1920,\n                1080\n            ],\n            \"overscan\": [\n                0.0,\n                0.0\n            ],\n            \"scale\": 0.5\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_entity(&mut v, "myfilm");
        let out = to_json_string(&v);
        assert!(out.contains("\"overscan\": [\n                0.0,\n                0.0\n            ]"));
        assert!(out.contains("\"scale\": 0.5"));
        assert!(out.contains("1920"));
    }

    #[test]
    fn non_ascii_is_escaped_like_python_json_dump() {
        // Python's json.dump writes these escapes (ensure_ascii); serde_json
        // alone writes raw UTF-8 and a raw DEL, which rewrote every non-ASCII
        // value a migration touched. tests/test_wizard_json_parity.py checks
        // the same against the Python interpreter over generated documents.
        let src = "{\n    \"user\": \"S\\u00f8ren\",\n    \"dash\": \"a \\u2014 b\",\n    \"emoji\": \"\\ud83d\\ude00\",\n    \"del\": \"\\u007f\",\n    \"tab\": \"\\t\"\n}\n";
        let v: Value = serde_json::from_str(src).unwrap();
        assert_eq!(to_json_string(&v), src);
    }

    #[test]
    fn ensure_object_appends_missing_intermediates() {
        let mut v: Value = serde_json::from_str("{\n    \"a\": 1\n}\n").unwrap();
        patch_config(&mut v, 25);
        // "a" stays first; the created children.project.properties.fps appends.
        let expected = "{\n    \"a\": 1,\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 25\n            }\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }
}
