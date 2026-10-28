//! Versioned, on-the-fly project `_config` migrations.
//!
//! A project's `_config` layout evolves; historically the convention module and
//! the department templates were copied into each project at creation and then
//! frozen, so a fix to the engine never reached existing projects. This module
//! makes the layout versioned and migratable in place: the current version lives
//! in `_config/version.json`, a project with no such file predates the system
//! and counts as version 0, and a caller walks the pending steps in order.
//!
//! ## Preflight is not optional
//!
//! Listing *pending* steps says nothing about whether they can run. A bulk run
//! across 16 live projects on 2026-08-26 dry-ran clean on every one and still
//! failed on five, because [`Step::AddEntityDepartments`] needs a
//! `_config/db/schemas.json` those projects never had — they are not
//! database-backed projects at all. Each step stamps its version on success, so
//! the five were left part-migrated at v2 with nothing said.
//!
//! Hence [`preflight`]: every step declares what it *needs*, checked before
//! anything is written, so a caller can refuse the run and say why.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::beauty_alpha::{self, Edit};
use crate::conventions;
use crate::load_json;
use crate::templates;

/// Where a project records the `_config` layout version it is at.
pub const VERSION_FILE: &str = "version.json";

/// One step forward in the `_config` layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    /// v1 — move the config DB engine into the package (thin convention shim).
    ConventionToPackage,
    /// v2 — refresh `_config/templates` from the packaged scaffold.
    RefreshTemplates,
    /// v3 — declare the entity `departments` property in the schema.
    AddEntityDepartments,
    /// v4 — seed the project-owned OCIO config into `_config/ocio/`.
    SeedOcio,
    /// v5 — relabel the browser "Variants" column as "Channels".
    RelabelVariantsAsChannels,
    /// v6 — drop the retired `kits` entries from `_config/storage_convention.py`.
    DropKitsFromStorageConvention,
    /// v7 — point the project's convention modules at the renamed package.
    FixConventionImports,
    /// v8 — repoint `temp:/` from the project drive to machine-local scratch.
    TempToLocalScratch,
    /// v9 — refresh `_config/templates` again, for the scaffold changes since v2.
    RefreshTemplatesAgain,
    /// v10 — drop the separate `alpha` AOV from the project's default render
    /// vars and render the beauty RGBA.
    BeautyCarriesAlpha,
    /// v11 — replace the three convention modules with data: the storage
    /// paths move into `_config/storage.json`, naming and the config engine
    /// are the package's (see [`conventions`]).
    ConventionsToData,
    /// v12 — bring every stock department template up to the packaged one,
    /// and refuse a customised one that calls an API the package removed
    /// (see [`templates`]).
    RefreshStockTemplates,
    /// v13 — store the publish channel under its own name: the entity
    /// property `variants` becomes `channels`, a scene asset entry's
    /// `variant` becomes `channel` (see [`v13_plan`]).
    ChannelKeys,
    /// v14 — v12 again, for the scaffold template fixes since: the stock
    /// animation template read `asset_info['uri']` from `list_assets`, which
    /// returns `{prim_path, metadata}`, so a new animation workfile failed
    /// with `KeyError: 'uri'` whenever the shot had assets; and the rig and
    /// blendshape templates opened the sopcreate's LOP internals instead of
    /// `sopnet/create` (1.77.0's fix, which no project past v12 received).
    RefreshStockTemplatesAgain,
    /// v15 — v12 again, for two template fixes: the layout render camera kept
    /// the Camera LOP's 16:9 aspect whatever the shot's `render.resolution`
    /// (its aspect now follows it), and the blendshape template authored
    /// `<asset>/blshp` as an Xform, which the asset structure check refuses
    /// (it now configures it as a Scope).
    LayoutCameraFollowsResolution,
    /// v16 — v12 again, for the animation template: it gave `th::import_rigs`
    /// a row for every asset in the shot, props and sets included, so every
    /// asset with no rig published was a failing row. It now adds only the
    /// assets that have one, and in a Multi's workfile gives each member's
    /// dive a SOP playblast set to that shot (`from_context` names no shot
    /// there, so a placed one could not render).
    AnimationRigsArePublished,
}

/// The convention modules a project owns, loaded and executed by
/// `tumblepipe.api.Client` at construction.
const CONVENTION_FILES: [&str; 3] = [
    "naming_convention.py",
    "storage_convention.py",
    "config_convention.py",
];

/// The two lines that define the dead `kits` concept, which v6 drops.
///
/// `kits` sat in the scaffold from the initial commit and nothing ever resolved
/// a `kits:` URI; its only mention was a comment in the legacy Project Browser,
/// retired in f310d35.
///
/// v6 removes exactly these lines rather than refreshing the file from the
/// package. Refreshing was the first design, and a check against the live share
/// killed it: 15 of 16 projects differ from the packaged copy — most still
/// import the pre-rename `tumblehead.*`, and several carry a real
/// `_primary_path` override — so a step that blocked on divergence would have
/// frozen every future migration for almost every project.
fn is_kits_line(line: &str) -> bool {
    let trimmed = line.trim();
    trimmed.starts_with("self.kits_path") || trimmed.starts_with("case 'kits'")
}

/// The `self.temp_path = ...` line v8 replaces.
///
/// Matching on `project_path` is what keeps this surgical, and it is what
/// scopes the step to the projects that actually have the problem. The
/// scaffold anchors temp to the project drive (`self.project_path.parent /
/// f'{project_name}_temp'`) and that is the line to move — but on the live
/// share only 1 of 16 projects is in that state. The other 15 keep scratch at
/// a WSL-era `_home / 'th_temp' / project_name`, which is machine-local and so
/// never produced a stray `<project>_temp`; those say nothing about
/// `project_path` and are left exactly as they are.
fn is_project_drive_temp_line(line: &str) -> bool {
    let trimmed = line.trim();
    trimmed.starts_with("self.temp_path") && trimmed.contains("project_path")
}

/// Every registered step, in the order they bring a project forward.
pub const STEPS: [Step; 16] = [
    Step::ConventionToPackage,
    Step::RefreshTemplates,
    Step::AddEntityDepartments,
    Step::SeedOcio,
    Step::RelabelVariantsAsChannels,
    Step::DropKitsFromStorageConvention,
    Step::FixConventionImports,
    Step::TempToLocalScratch,
    Step::RefreshTemplatesAgain,
    Step::BeautyCarriesAlpha,
    Step::ConventionsToData,
    Step::RefreshStockTemplates,
    Step::ChannelKeys,
    Step::RefreshStockTemplatesAgain,
    Step::LayoutCameraFollowsResolution,
    Step::AnimationRigsArePublished,
];

impl Step {
    /// The version this step brings the project *to*.
    pub fn version(self) -> u32 {
        match self {
            Step::ConventionToPackage => 1,
            Step::RefreshTemplates => 2,
            Step::AddEntityDepartments => 3,
            Step::SeedOcio => 4,
            Step::RelabelVariantsAsChannels => 5,
            Step::DropKitsFromStorageConvention => 6,
            Step::FixConventionImports => 7,
            Step::TempToLocalScratch => 8,
            Step::RefreshTemplatesAgain => 9,
            Step::BeautyCarriesAlpha => 10,
            Step::ConventionsToData => 11,
            Step::RefreshStockTemplates => 12,
            Step::ChannelKeys => 13,
            Step::RefreshStockTemplatesAgain => 14,
            Step::LayoutCameraFollowsResolution => 15,
            Step::AnimationRigsArePublished => 16,
        }
    }

    /// One line for a UI or a log, matching the Python registry's wording.
    pub fn description(self) -> &'static str {
        match self {
            Step::ConventionToPackage => {
                "move the config DB engine into the package (thin convention shim)"
            }
            Step::RefreshTemplates => "refresh _config/templates from the packaged scaffold",
            Step::AddEntityDepartments => "declare the entity `departments` property in the schema",
            Step::SeedOcio => "seed the project-owned OCIO config into _config/ocio/",
            Step::RelabelVariantsAsChannels => {
                "relabel the browser \"Variants\" column as \"Channels\""
            }
            Step::DropKitsFromStorageConvention => {
                "drop the retired `kits` entries from _config/storage_convention.py"
            }
            Step::FixConventionImports => {
                "point the convention modules at the renamed tumblepipe package"
            }
            Step::TempToLocalScratch => {
                "repoint temp:/ from the project drive to machine-local scratch"
            }
            Step::RefreshTemplatesAgain => {
                "refresh _config/templates from the packaged scaffold again"
            }
            Step::BeautyCarriesAlpha => {
                "drop the separate alpha AOV and render the beauty RGBA"
            }
            Step::ConventionsToData => {
                "replace the _config/*_convention.py modules with _config/storage.json"
            }
            Step::RefreshStockTemplates => {
                "refresh the stock _config/templates for the APIs this version removed"
            }
            Step::ChannelKeys => {
                "store the publish channel under `channels`/`channel` in the config database"
            }
            Step::RefreshStockTemplatesAgain => {
                "refresh the stock _config/templates again, for the template fixes since v12"
            }
            Step::LayoutCameraFollowsResolution => {
                "refresh the stock _config/templates for the layout camera aspect and the blendshape Scope"
            }
            Step::AnimationRigsArePublished => {
                "refresh the stock _config/templates: animation imports only published rigs, and a Multi gets a playblast per shot"
            }
        }
    }
}

/// The newest version any registered step targets.
pub fn latest_version() -> u32 {
    let mut newest = 0;
    let mut i = 0;
    while i < STEPS.len() {
        let version = STEPS[i].version();
        if version > newest {
            newest = version;
        }
        i += 1;
    }
    newest
}

/// Accept either a project root or the `_config` directory itself.
pub fn config_dir(project_path: &Path) -> PathBuf {
    if project_path.file_name().and_then(|name| name.to_str()) == Some("_config") {
        project_path.to_path_buf()
    } else {
        project_path.join("_config")
    }
}

/// The project's recorded layout version — 0 when unstamped or unreadable.
pub fn current_version(project_path: &Path) -> u32 {
    let path = config_dir(project_path).join(VERSION_FILE);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return 0;
    };
    serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|value| value.get("version").and_then(|n| n.as_u64()))
        .unwrap_or(0) as u32
}

/// Steps newer than the project's current version, in order.
pub fn pending(project_path: &Path) -> Vec<Step> {
    pending_through(project_path, latest_version())
}

/// [`pending`], stopping at the step that reaches `last`.
fn pending_through(project_path: &Path, last: u32) -> Vec<Step> {
    let at = current_version(project_path);
    STEPS
        .iter()
        .copied()
        .filter(|s| s.version() > at && s.version() <= last)
        .collect()
}

/// Whether a step can run, decided before anything is written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Readiness {
    /// Its inputs are present; it will run.
    Ready,
    /// Its inputs are missing, or a customization would be clobbered. The
    /// message says which, in terms a user can act on.
    Blocked(String),
}

impl Readiness {
    pub fn is_ready(&self) -> bool {
        matches!(self, Readiness::Ready)
    }
}

fn scaffold_templates_dir(template_dir: &Path) -> PathBuf {
    template_dir.join("_config").join("templates")
}

fn scaffold_ocio_dir(template_dir: &Path) -> PathBuf {
    template_dir.join("_config").join("ocio")
}

impl Step {
    /// Can this step run against this project? Reads only — never writes.
    pub fn readiness(self, project_path: &Path, template_dir: &Path) -> Readiness {
        let cfg = config_dir(project_path);
        match self {
            Step::ConventionToPackage => {
                let path = cfg.join("config_convention.py");
                if !path.exists() {
                    // Nothing to preserve; the shim is simply written.
                    return Readiness::Ready;
                }
                match std::fs::read_to_string(&path) {
                    Err(err) => {
                        Readiness::Blocked(format!("cannot read {}: {err}", path.display()))
                    }
                    // Already the shim — the step is a no-op.
                    Ok(text) if text.contains("JsonConfigStore") => Readiness::Ready,
                    // The stock generic engine — safe to back up and replace.
                    Ok(text) if text.contains("class ProjectConfigConvention") => Readiness::Ready,
                    Ok(_) => Readiness::Blocked(format!(
                        "{} is not the stock config engine, so replacing it would clobber a \
                         customization — migrate it by hand",
                        path.display()
                    )),
                }
            }
            Step::RefreshTemplates | Step::RefreshTemplatesAgain => {
                let source = scaffold_templates_dir(template_dir);
                if source.is_dir() {
                    Readiness::Ready
                } else {
                    Readiness::Blocked(format!(
                        "packaged templates not found at {} — the TumblePipe package is incomplete",
                        source.display()
                    ))
                }
            }
            Step::AddEntityDepartments => {
                let path = cfg.join("db").join("schemas.json");
                if path.is_file() {
                    Readiness::Ready
                } else {
                    Readiness::Blocked(format!(
                        "{} not found — this project has no config database, so the schema \
                         default cannot be declared",
                        path.display()
                    ))
                }
            }
            Step::SeedOcio => {
                let source = scaffold_ocio_dir(template_dir);
                if source.is_dir() {
                    Readiness::Ready
                } else {
                    Readiness::Blocked(format!(
                        "packaged OCIO scaffold not found at {} — the TumblePipe package is \
                         incomplete",
                        source.display()
                    ))
                }
            }
            // Tolerant by design: a project with no browser column config has
            // nothing to relabel, which is a no-op rather than a failure.
            Step::RelabelVariantsAsChannels => Readiness::Ready,
            // Always Ready: this removes two specific lines rather than
            // replacing the file, so it cannot clobber a customization and has
            // nothing it needs from the package. Refreshing the whole file was
            // the first design, and a check against the live share killed it —
            // 15 of 16 projects differ from the packaged copy (most still
            // import the pre-rename `tumblehead.*`, several carry a real
            // `_primary_path` override), so a blocking step here would have
            // frozen every future migration for almost every project.
            Step::DropKitsFromStorageConvention => Readiness::Ready,
            // Also always Ready: a prefix rewrite on import lines needs nothing
            // from the package and cannot clobber a customization.
            Step::FixConventionImports => Readiness::Ready,
            // Ready for the same reason as v6: it rewrites one specific line
            // and leaves the rest alone. The helper it points at ships in the
            // same package as this migrator, so it cannot be missing.
            Step::TempToLocalScratch => Readiness::Ready,
            // Blocked when either file has a shape the edit does not
            // recognise: it is refused rather than guessed at, and the user
            // is told what to fix by hand. A project without the files, or
            // already through, is Ready (and a no-op).
            Step::BeautyCarriesAlpha => match v10_edits(&cfg) {
                Ok(_) => Readiness::Ready,
                Err(why) => Readiness::Blocked(why),
            },
            // Blocked when a module holds something the package cannot
            // stand in for: a naming or config convention that is not one
            // the scaffold shipped, or a storage convention whose paths
            // cannot be read without running it. Nothing is guessed at.
            Step::ConventionsToData => match v11_plan(&cfg, template_dir) {
                Ok(_) => Readiness::Ready,
                Err(why) => Readiness::Blocked(why),
            },
            // Blocked when a customised template calls an API the package
            // removed: replacing it would lose the studio's work, keeping it
            // leaves "New workfile" broken, so its owner decides. A project
            // still to run v9 is judged as v9 will leave it, since v9
            // replaces every packaged template whatever it holds.
            Step::RefreshStockTemplates
            | Step::RefreshStockTemplatesAgain
            | Step::LayoutCameraFollowsResolution
            | Step::AnimationRigsArePublished => {
                let v9_pending =
                    current_version(project_path) < Step::RefreshTemplatesAgain.version();
                match v12_plan(&cfg, template_dir, v9_pending) {
                    Ok(_) => Readiness::Ready,
                    Err(why) => Readiness::Blocked(why),
                }
            }
            // Blocked only when a record spells the channel both ways with
            // different values, or a database file is unreadable: keeping
            // either value would be a guess with a wrong render behind it.
            Step::ChannelKeys => match v13_plan(&cfg) {
                Ok(_) => Readiness::Ready,
                Err(why) => Readiness::Blocked(why),
            },
        }
    }
}

/// Readiness of every pending step, in order. Empty when nothing is pending.
pub fn preflight(project_path: &Path, template_dir: &Path) -> Vec<(Step, Readiness)> {
    preflight_through(project_path, template_dir, latest_version())
}

fn preflight_through(
    project_path: &Path,
    template_dir: &Path,
    last: u32,
) -> Vec<(Step, Readiness)> {
    pending_through(project_path, last)
        .into_iter()
        .map(|step| {
            let readiness = step.readiness(project_path, template_dir);
            (step, readiness)
        })
        .collect()
}

/// The first blocked step of a pending run, if any — what a caller shows the
/// user instead of starting a migration that cannot finish.
pub fn first_blocker(project_path: &Path, template_dir: &Path) -> Option<(Step, String)> {
    first_blocker_through(project_path, template_dir, latest_version())
}

fn first_blocker_through(
    project_path: &Path,
    template_dir: &Path,
    last: u32,
) -> Option<(Step, String)> {
    preflight_through(project_path, template_dir, last)
        .into_iter()
        .find_map(|(step, readiness)| match readiness {
            Readiness::Blocked(why) => Some((step, why)),
            Readiness::Ready => None,
        })
}

/// The per-project `config_convention.py` after v1: a thin shim, so engine
/// fixes ship with the package instead of being frozen into every project at
/// creation. Byte-identical to the Python module's `_THIN_CONVENTION`.
const THIN_CONVENTION: &str = r#""""Project config convention.

The config database engine now lives in the package
(``tumblepipe.config.store.JsonConfigStore``) so that fixes and features
reach every project through a normal package update instead of being frozen
into this per-project file at creation time. This module only wires it up.

If a project needs project-specific config behaviour, subclass
``JsonConfigStore`` here and return that instead.
"""

from tumblepipe.config.store import JsonConfigStore


def create() -> JsonConfigStore:
    return JsonConfigStore()
"#;

/// These files live on a shared project drive, and rewriting their line endings
/// would turn any migration into a whole-file diff on every project — noise that
/// buries the change that actually matters. Worse, the templates step decides
/// whether to touch a file by comparing its text, so a CRLF project file would
/// never match the LF scaffold and every run would back up and rewrite all of
/// them.
///
/// The Python original avoided both by accident: `read_text()` normalises
/// newlines on the way in, and `write_text()` translates back to `os.linesep` on
/// the way out — which also makes its output platform-dependent. Preserving
/// what the file already uses is the same result on Windows and better
/// elsewhere, because a Linux artist migrating a CRLF project no longer flips
/// every line.
mod newlines {
    /// The style a file already uses. CRLF wins if the file has any.
    pub fn style_of(text: &str) -> &'static str {
        if text.contains("\r\n") {
            "\r\n"
        } else {
            "\n"
        }
    }

    /// Strip CR so two texts can be compared regardless of style.
    pub fn normalize(text: &str) -> String {
        text.replace("\r\n", "\n")
    }

    /// Re-apply `style` to LF text.
    pub fn apply(text: &str, style: &str) -> String {
        if style == "\r\n" {
            normalize(text).replace('\n', "\r\n")
        } else {
            normalize(text)
        }
    }

    /// The style `path` uses today, or LF when it does not exist yet.
    pub fn style_of_file(path: &std::path::Path) -> &'static str {
        match std::fs::read_to_string(path) {
            Ok(text) => style_of(&text),
            Err(_) => "\n",
        }
    }
}

/// Write `text` to `path`, first preserving whatever is there as `<name>.bak`,
/// and keeping the destination's existing line-ending style.
///
/// Never clobbers an existing `.bak`: a prior run already preserved the true
/// original, and by then the live file may be the replacement.
fn write_preserving(path: &Path, text: &str) -> Result<(), String> {
    write_preserving_as(path, text, "bak")
}

/// [`write_preserving`] with the backup at `<name>.<suffix>`.
///
/// A step that rewrites files an earlier step already backed up needs its own
/// suffix: the existing `.bak` is the earlier original and is kept, so writing
/// through the plain form would leave nothing of what this step replaced.
fn write_preserving_as(path: &Path, text: &str, suffix: &str) -> Result<(), String> {
    let style = newlines::style_of_file(path);
    if let Ok(existing) = std::fs::read_to_string(path) {
        let backup = path.with_file_name(format!(
            "{}.{suffix}",
            path.file_name().and_then(|n| n.to_str()).unwrap_or("file")
        ));
        if !backup.exists() {
            // Byte-for-byte as it was, so the backup is a true original.
            std::fs::write(&backup, existing)
                .map_err(|e| format!("could not back up {}: {e}", path.display()))?;
        }
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("could not create {}: {e}", parent.display()))?;
    }
    std::fs::write(path, newlines::apply(text, style))
        .map_err(|e| format!("could not write {}: {e}", path.display()))
}

/// `store_json`, but keeping the file's existing line-ending style.
fn store_json_in_place(path: &Path, value: &Value) -> Result<(), String> {
    let style = newlines::style_of_file(path);
    let text = newlines::apply(&crate::to_json_string(value), style);
    std::fs::write(path, text).map_err(|e| format!("could not write {}: {e}", path.display()))
}

/// Every file under `root` matching `name`, depth-first, sorted for a stable
/// order (the Python original walks a sorted `rglob`).
fn find_files(root: &Path, name: Option<&str>, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(root) else {
        return;
    };
    let mut items: Vec<PathBuf> = entries.filter_map(|e| e.ok()).map(|e| e.path()).collect();
    items.sort();
    for item in items {
        if item.is_dir() {
            find_files(&item, name, out);
        } else if name.map_or(true, |want| {
            item.file_name().and_then(|n| n.to_str()) == Some(want)
        }) {
            out.push(item);
        }
    }
}

/// Make sure `storage_convention.py` imports the helper v8 points it at.
///
/// In practice the import to extend is already there: every project's
/// convention subclasses `StorageConvention` from the same module, and v7 has
/// already repointed its prefix by the time this runs. The standalone-line
/// fallback covers a file that reaches the class some other way, so this step
/// can never be the thing that leaves a project with an undefined name.
fn ensure_temp_helper_import(lines: &mut Vec<String>) {
    let imports_helper = lines.iter().any(|line| {
        let trimmed = line.trim_start();
        (trimmed.starts_with("from ") || trimmed.starts_with("import "))
            && line.contains("default_temp_path")
    });
    if imports_helper {
        return;
    }
    // A trailing comment or an open paren means the name list does not end
    // where the line does, so appending to it would corrupt the import.
    let extendable = lines.iter().position(|line| {
        let trimmed = line.trim();
        trimmed.starts_with("from tumblepipe.storage import")
            && !trimmed.contains('#')
            && !trimmed.ends_with('(')
    });
    if let Some(index) = extendable {
        lines[index] = format!("{}, default_temp_path", lines[index].trim_end());
        return;
    }
    let first_import = lines
        .iter()
        .position(|line| line.starts_with("from ") || line.starts_with("import "))
        .unwrap_or(0);
    lines.insert(
        first_import,
        "from tumblepipe.storage import default_temp_path".to_string(),
    );
}

/// Rename the vocabulary in one label/tooltip string (v5).
fn variants_to_channels(text: &str) -> String {
    text.replace("Variants", "Channels")
        .replace("Variant", "Channel")
        .replace("variants", "channels")
        .replace("variant", "channel")
}

/// Relabel every column spec that drives the `variants` property. Returns
/// whether anything changed. The property *key* is deliberately untouched —
/// it is what every project's database already stores, and what the published
/// path and URI wire format spell.
fn relabel_columns(node: &mut Value) -> bool {
    let mut changed = false;
    match node {
        Value::Array(items) => {
            for item in items {
                changed |= relabel_columns(item);
            }
        }
        Value::Object(map) => {
            let names_variants = |field: &str| {
                map.get(field).and_then(|v| v.as_str()) == Some("variants")
            };
            let drives_variants = names_variants("key") || names_variants("property_path");
            if drives_variants {
                for field in ["label", "tooltip"] {
                    if let Some(Value::String(current)) = map.get(field) {
                        let renamed = variants_to_channels(current);
                        if &renamed != current {
                            map.insert(field.to_string(), Value::String(renamed));
                            changed = true;
                        }
                    }
                }
            }
            for (_, value) in map.iter_mut() {
                changed |= relabel_columns(value);
            }
        }
        _ => {}
    }
    changed
}

impl Step {
    /// Apply this step. Idempotent: re-running one that already happened is a
    /// no-op, so a part-migrated project can always be re-run to completion.
    pub fn apply(self, project_path: &Path, template_dir: &Path) -> Result<(), String> {
        let cfg = config_dir(project_path);
        match self {
            Step::ConventionToPackage => {
                let path = cfg.join("config_convention.py");
                match std::fs::read_to_string(&path) {
                    // Absent: nothing to preserve, just write the shim.
                    Err(_) => write_preserving(&path, THIN_CONVENTION),
                    // Already the shim — idempotent.
                    Ok(text) if text.contains("JsonConfigStore") => Ok(()),
                    Ok(text) if text.contains("class ProjectConfigConvention") => {
                        let _ = text;
                        write_preserving(&path, THIN_CONVENTION)
                    }
                    // Unreachable behind preflight, but never clobber a
                    // customization even if a caller skipped the check.
                    Ok(_) => Err(format!(
                        "{} is not the stock config engine — migrate it by hand",
                        path.display()
                    )),
                }
            }
            Step::RefreshTemplates => refresh_templates(&cfg, template_dir, "bak"),
            // v2 was the only template refresh, so a project past it never saw
            // a scaffold template change again: HideAndReek sat on the packed
            // Scene Invoke, the pre-promote_name rig and the old MODEL/LOOKDEV
            // dive targets. Its own backup suffix, because v2 may already have
            // left a `.bak` that must stay the true original.
            Step::RefreshTemplatesAgain => refresh_templates(&cfg, template_dir, "v9.bak"),
            Step::AddEntityDepartments => {
                let path = cfg.join("db").join("schemas.json");
                if !path.is_file() {
                    return Err(format!(
                        "{} not found — cannot add the schema default",
                        path.display()
                    ));
                }
                let mut data = load_json(&path)?;
                let entity = data
                    .get_mut("children")
                    .and_then(|c| c.get_mut("entity"))
                    .ok_or_else(|| format!("{} has no entity schema node", path.display()))?;
                let properties = entity
                    .as_object_mut()
                    .ok_or_else(|| format!("{} entity node is not an object", path.display()))?
                    .entry("properties")
                    .or_insert_with(|| Value::Object(Default::default()));
                let properties = properties.as_object_mut().ok_or_else(|| {
                    format!("{} entity properties is not an object", path.display())
                })?;
                if properties.contains_key("departments") {
                    return Ok(()); // already declared — idempotent
                }
                // The default is [] — "inherit the whole pool" — so this is
                // data-neutral: no existing entity changes behaviour.
                properties.insert("departments".to_string(), Value::Array(Vec::new()));
                store_json_in_place(&path, &data)
            }
            Step::SeedOcio => {
                let source = scaffold_ocio_dir(template_dir);
                if !source.is_dir() {
                    return Err(format!(
                        "packaged OCIO scaffold not found at {}",
                        source.display()
                    ));
                }
                let target = cfg.join("ocio");
                let mut found = Vec::new();
                find_files(&source, None, &mut found);
                for src in found {
                    let relative = src.strip_prefix(&source).map_err(|e| e.to_string())?;
                    let dst = target.join(relative);
                    // Seed-if-absent, never clobber: a project may have
                    // hand-tuned its colour config.
                    if dst.exists() {
                        continue;
                    }
                    if let Some(parent) = dst.parent() {
                        std::fs::create_dir_all(parent)
                            .map_err(|e| format!("could not create {}: {e}", parent.display()))?;
                    }
                    std::fs::copy(&src, &dst)
                        .map_err(|e| format!("could not seed {}: {e}", dst.display()))?;
                }
                Ok(())
            }
            Step::RelabelVariantsAsChannels => {
                let path = cfg.join("db").join("config.json");
                if !path.is_file() {
                    return Ok(()); // no browser column config — nothing to relabel
                }
                let mut data = load_json(&path)?;
                if !relabel_columns(&mut data) {
                    return Ok(()); // already relabelled — idempotent
                }
                store_json_in_place(&path, &data)
            }
            Step::DropKitsFromStorageConvention => {
                let path = cfg.join("storage_convention.py");
                let Ok(current) = std::fs::read_to_string(&path) else {
                    return Ok(()); // no convention here — nothing to drop
                };
                if !current.lines().any(is_kits_line) {
                    return Ok(()); // already gone — idempotent
                }
                // Drop exactly those lines and leave every other one byte for
                // byte, so a project's own path overrides survive untouched.
                let normalized = newlines::normalize(&current);
                let mut kept: Vec<&str> = normalized
                    .lines()
                    .filter(|line| !is_kits_line(line))
                    .collect();
                let trailing_newline = normalized.ends_with('\n');
                if trailing_newline {
                    kept.push("");
                }
                write_preserving(&path, &kept.join("\n"))
            }
            Step::FixConventionImports => {
                // `tumblehead` was renamed to `tumblepipe`. The packaged
                // scaffold was corrected in 0bef314 (2026-05-26), but a
                // project's convention modules are copied at creation and then
                // frozen, so every project made before that still imports a
                // package that no longer exists — and `Client.__init__`
                // *executes* all three at construction, so the project cannot
                // be opened at all. Five live projects are in that state.
                //
                // Only the module prefix on import statements moves; a path or
                // a comment mentioning tumblehead is left alone.
                for name in CONVENTION_FILES {
                    let path = cfg.join(name);
                    let Ok(current) = std::fs::read_to_string(&path) else {
                        continue; // this project does not have that convention
                    };
                    let fixed = current
                        .replace("from tumblehead.", "from tumblepipe.")
                        .replace("import tumblehead.", "import tumblepipe.");
                    if fixed == current {
                        continue; // already correct — idempotent
                    }
                    write_preserving(&path, &newlines::normalize(&fixed))?;
                }
                Ok(())
            }
            Step::TempToLocalScratch => {
                // `temp:/` is pure scratch: an export stages the whole version
                // payload there and copies it into `export/`, a farm render
                // writes its tiles there before the stitch. The scaffold put it
                // on the project drive, so that staging crossed the network
                // twice and left an empty `<project>_temp` beside every project
                // — the callers create the root eagerly, and only the inner
                // `TemporaryDirectory` cleans itself up.
                //
                // The destination now lives in the package
                // (`tumblepipe.storage.default_temp_path`) rather than being
                // spelled out in each project, so a later change to where
                // scratch belongs ships with a package update instead of
                // needing another migration.
                let path = cfg.join("storage_convention.py");
                let Ok(current) = std::fs::read_to_string(&path) else {
                    return Ok(()); // no convention here — nothing to repoint
                };
                let normalized = newlines::normalize(&current);
                if !normalized.lines().any(is_project_drive_temp_line) {
                    // Already repointed, or the project keeps its own scratch
                    // root — either way, leave it alone.
                    return Ok(());
                }
                let mut lines: Vec<String> =
                    normalized.lines().map(str::to_string).collect();
                for line in lines.iter_mut() {
                    if is_project_drive_temp_line(line) {
                        let indent: String =
                            line.chars().take_while(|c| c.is_whitespace()).collect();
                        *line = format!("{indent}self.temp_path = default_temp_path()");
                    }
                }
                ensure_temp_helper_import(&mut lines);
                if normalized.ends_with('\n') {
                    lines.push(String::new());
                }
                write_preserving(&path, &lines.join("\n"))
            }
            Step::BeautyCarriesAlpha => {
                // Both edits are worked out before either is written, so a
                // refusal leaves the project exactly as it was. Own backup
                // suffix: a `.bak` from an earlier edit stays the original.
                for (path, text) in v10_edits(&cfg)? {
                    let style = newlines::style_of_file(&path);
                    write_preserving_as(&path, &newlines::apply(&text, style), "v10.bak")?;
                }
                Ok(())
            }
            Step::ConventionsToData => {
                // The table is worked out, and every module vetted, before
                // anything is written. The table goes down first: a failure
                // after it leaves the modules in place and the step re-runs.
                if let Some(table) = v11_plan(&cfg, template_dir)? {
                    let text = crate::to_json_string(&Value::Object(table));
                    write_preserving_as(&cfg.join(conventions::STORAGE_FILE), &text, "v11.bak")?;
                }
                for name in CONVENTION_FILES {
                    retire(&cfg, name)?;
                }
                Ok(())
            }
            Step::RefreshStockTemplates => refresh_stock_templates(&cfg, template_dir),
            Step::ChannelKeys => {
                // Every file is planned before any is written, so a refusal
                // leaves the database exactly as it was. Own backup suffix:
                // a `.bak` an earlier step left stays that step's original.
                for (path, data) in v13_plan(&cfg)? {
                    write_preserving_as(&path, &crate::to_json_string(&data), "v13.bak")?;
                }
                Ok(())
            }
            // v12 brought the stock templates up to the scaffold of its day;
            // a template fixed since never reached a project past it. Same
            // plan and the same rules: stock is replaced, customised is left
            // alone unless it calls something removed.
            Step::RefreshStockTemplatesAgain => refresh_stock_templates(&cfg, template_dir),
            // Same plan again, for the layout and blendshape template fixes.
            Step::LayoutCameraFollowsResolution => refresh_stock_templates(&cfg, template_dir),
            // Same plan again, for the animation template's rig rows.
            Step::AnimationRigsArePublished => refresh_stock_templates(&cfg, template_dir),
        }
    }
}

/// Write v12's plan: every packaged template whose project copy is missing or
/// stock. Every template is judged before any is written, so a refusal leaves
/// the project exactly as it was. No backup: a stock template is one the
/// scaffold shipped, and a customised one is never written.
fn refresh_stock_templates(cfg: &Path, template_dir: &Path) -> Result<(), String> {
    for (path, text) in v12_plan(cfg, template_dir, false)? {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("could not create {}: {e}", parent.display()))?;
        }
        let style = newlines::style_of_file(&path);
        std::fs::write(&path, newlines::apply(&text, style))
            .map_err(|e| format!("could not write {}: {e}", path.display()))?;
    }
    Ok(())
}

/// v10's edits to a project, as `(file, new LF text)`: empty when there is
/// nothing to do, an error naming the file and the reason when either file
/// has a shape the edit refuses (see [`beauty_alpha`]).
fn v10_edits(cfg: &Path) -> Result<Vec<(PathBuf, String)>, String> {
    let usd_dir = cfg.join("usd");
    let mut out = Vec::new();
    // The render vars every shot starts from, and the AOV list a farm job
    // falls back to when an export context names none
    let files: [(&str, fn(&str) -> Edit); 2] = [
        ("root_default_prims.usda", beauty_alpha::root_defaults_edit),
        ("context.json", beauty_alpha::context_json_edit),
    ];
    for (name, edit) in files {
        let path = usd_dir.join(name);
        let Ok(current) = std::fs::read_to_string(&path) else {
            continue; // the project has no such file
        };
        match edit(&newlines::normalize(&current)) {
            Edit::Unchanged => {}
            Edit::Changed(text) => out.push((path, text)),
            Edit::Refused(why) => {
                return Err(format!(
                    "{} was left alone: {why}. Remove the `alpha` RenderVar (and its \
                     orderedVars target) and make the beauty RenderVar color4f by hand, \
                     then launch again",
                    path.display()
                ))
            }
        }
    }
    Ok(out)
}

/// The packaged scaffold's storage table, which a new project starts from.
fn scaffold_storage_table(template_dir: &Path) -> Result<serde_json::Map<String, Value>, String> {
    let path = template_dir.join("_config").join(conventions::STORAGE_FILE);
    let Value::Object(table) = load_json(&path).map_err(|why| {
        format!(
            "packaged {} unreadable ({why}) — the TumblePipe package is incomplete",
            path.display()
        )
    })?
    else {
        return Err(format!("packaged {} is not a JSON object", path.display()));
    };
    conventions::validate_table(&table)
        .map_err(|why| format!("packaged {} is invalid: {why}", path.display()))?;
    Ok(table)
}

/// v11's work for a project: `Ok(Some(table))` when `storage.json` is to be
/// written, `Ok(None)` when the one already there stands, or why a module
/// cannot be retired.
fn v11_plan(
    cfg: &Path,
    template_dir: &Path,
) -> Result<Option<serde_json::Map<String, Value>>, String> {
    let scaffold = scaffold_storage_table(template_dir)?;
    let read = |name: &str| -> Result<Option<String>, String> {
        let path = cfg.join(name);
        match std::fs::read_to_string(&path) {
            Ok(text) => Ok(Some(text)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(err) => Err(format!("cannot read {}: {err}", path.display())),
        }
    };
    if let Some(text) = read("naming_convention.py")? {
        if !conventions::naming_is_stock(&text) {
            return Err(format!(
                "{} is not a naming convention TumblePipe shipped. Naming (entity names, \
                 vNNNN versions, instance names) is now fixed by the package and cannot be \
                 customised per project: reconcile what it changes, delete the file, then \
                 launch again",
                cfg.join("naming_convention.py").display()
            ));
        }
    }
    if let Some(text) = read("config_convention.py")? {
        if !conventions::config_is_stock(&text) {
            return Err(format!(
                "{} is not the stock config shim. The package's config store is now used \
                 directly and cannot be replaced per project: reconcile what it changes, \
                 delete the file, then launch again",
                cfg.join("config_convention.py").display()
            ));
        }
    }
    let storage_json = cfg.join(conventions::STORAGE_FILE);
    match read("storage_convention.py")? {
        Some(text) => conventions::storage_table(&text, &scaffold)
            .map(Some)
            .map_err(|why| {
                format!(
                    "{} cannot be turned into {}: {why}. Write {} by hand (start from the \
                     packaged scaffold's; docs/configuration.md lists the placeholders), \
                     delete the module, then launch again",
                    cfg.join("storage_convention.py").display(),
                    conventions::STORAGE_FILE,
                    storage_json.display()
                )
            }),
        // A table of the project's own, from an earlier run or by hand
        None if storage_json.exists() => {
            let Value::Object(table) = load_json(&storage_json)? else {
                return Err(format!("{} is not a JSON object", storage_json.display()));
            };
            conventions::validate_table(&table)
                .map_err(|why| format!("{} is invalid: {why}", storage_json.display()))?;
            Ok(None)
        }
        // No module and no table: the project resolved nothing, so it gets
        // the locations a new project is created with.
        None => Ok(Some(scaffold)),
    }
}

/// Move a retired convention module aside as `<name>.v11.bak`, and drop its
/// compiled copy. A `.v11.bak` already there is the true original from an
/// earlier run, so it is kept and the live file simply removed.
fn retire(cfg: &Path, name: &str) -> Result<(), String> {
    let path = cfg.join(name);
    if path.exists() {
        let backup = cfg.join(format!("{name}.v11.bak"));
        let moved = if backup.exists() {
            std::fs::remove_file(&path)
        } else {
            std::fs::rename(&path, &backup)
        };
        moved.map_err(|e| format!("could not retire {}: {e}", path.display()))?;
    }
    let prefix = format!("{}.", name.trim_end_matches(".py"));
    let mut compiled = Vec::new();
    find_files(&cfg.join("__pycache__"), None, &mut compiled);
    for file in compiled {
        let file_name = file.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if file_name.starts_with(&prefix) && file_name.ends_with(".pyc") {
            std::fs::remove_file(&file)
                .map_err(|e| format!("could not remove {}: {e}", file.display()))?;
        }
    }
    Ok(())
}

/// Copy every packaged `template.py` over the project's, backing up any that
/// differ. Replacing is fine here, unlike `storage_convention.py`: a hand-tuned
/// template only shapes future workfiles, and the backup keeps it.
fn refresh_templates(cfg: &Path, template_dir: &Path, backup_suffix: &str) -> Result<(), String> {
    let source = scaffold_templates_dir(template_dir);
    if !source.is_dir() {
        return Err(format!(
            "packaged templates not found at {}",
            source.display()
        ));
    }
    let target = cfg.join("templates");
    let mut found = Vec::new();
    find_files(&source, Some("template.py"), &mut found);
    for src in found {
        let relative = src.strip_prefix(&source).map_err(|e| e.to_string())?;
        let dst = target.join(relative);
        let new_text = std::fs::read_to_string(&src)
            .map_err(|e| format!("could not read {}: {e}", src.display()))?;
        match std::fs::read_to_string(&dst) {
            // Identical bar line endings — leave it, and leave its mtime
            // alone. Comparing raw bytes would rewrite and back up every CRLF
            // template on every run.
            Ok(old) if newlines::normalize(&old) == newlines::normalize(&new_text) => continue,
            _ => write_preserving_as(&dst, &new_text, backup_suffix)?,
        }
    }
    Ok(())
}

/// v12's writes, as `(project template, packaged LF text)`, or why the step
/// refuses.
///
/// Each packaged template is written where the project has none, or a stock
/// one (a version the scaffold shipped, [`templates::is_stock`]) that differs
/// from it. A customised one — and any `template.py` the package does not
/// ship, such as a department of the studio's own — is left alone, unless it
/// calls something the package removed ([`templates::removed_apis`]); then
/// the whole step refuses, naming each file and what it calls.
///
/// `v9_pending`: v9 is part of the same run, and it replaces every packaged
/// template whatever it holds, so those are taken as already current.
fn v12_plan(
    cfg: &Path,
    template_dir: &Path,
    v9_pending: bool,
) -> Result<Vec<(PathBuf, String)>, String> {
    let source = scaffold_templates_dir(template_dir);
    if !source.is_dir() {
        return Err(format!(
            "packaged templates not found at {} — the TumblePipe package is incomplete",
            source.display()
        ));
    }
    let target = cfg.join("templates");
    let read = |path: &Path| -> Result<Option<String>, String> {
        match std::fs::read_to_string(path) {
            Ok(text) => Ok(Some(text)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(err) => Err(format!("cannot read {}: {err}", path.display())),
        }
    };

    let mut packaged = Vec::new();
    find_files(&source, Some("template.py"), &mut packaged);
    let mut writes = Vec::new();
    let mut broken: Vec<(PathBuf, Vec<String>)> = Vec::new();
    let mut shipped = std::collections::HashSet::new();
    for src in packaged {
        let relative = template_key(&source, &src);
        let dst = target.join(src.strip_prefix(&source).map_err(|e| e.to_string())?);
        shipped.insert(relative.clone());
        if v9_pending {
            continue;
        }
        let new_text = newlines::normalize(
            &std::fs::read_to_string(&src)
                .map_err(|e| format!("could not read {}: {e}", src.display()))?,
        );
        match read(&dst)? {
            None => writes.push((dst, new_text)),
            Some(old) if newlines::normalize(&old) == new_text => {}
            Some(old) if templates::is_stock(&relative, &old) => writes.push((dst, new_text)),
            Some(old) => {
                let found = templates::removed_apis(&old);
                if !found.is_empty() {
                    broken.push((dst, found));
                }
            }
        }
    }

    // Templates of the project's own: never written, but a new workfile
    // runs them all the same.
    let mut own = Vec::new();
    find_files(&target, Some("template.py"), &mut own);
    for path in own {
        let relative = template_key(&target, &path);
        if shipped.contains(&relative) {
            continue;
        }
        let Some(text) = read(&path)? else { continue };
        if templates::is_stock(&relative, &text) {
            continue; // a department the scaffold no longer ships
        }
        let found = templates::removed_apis(&text);
        if !found.is_empty() {
            broken.push((path, found));
        }
    }

    if broken.is_empty() {
        return Ok(writes);
    }
    let mut why = format!(
        "{} customised template(s) call what this TumblePipe version removed, so a new \
         workfile from them would fail. Each is the studio's own work, so it is not \
         replaced, and nothing was written:",
        broken.len()
    );
    for (path, found) in &broken {
        why.push_str(&format!("\n    {}:", path.display()));
        for item in found {
            why.push_str(&format!("\n      - {item}"));
        }
    }
    why.push_str(&format!(
        "\n  Port each to the packaged template of the same department under {} (or \
         delete it, and the next launch puts the packaged one in its place), then \
         launch again",
        source.display()
    ));
    Err(why)
}

/// The entity property naming an entity's custom channels, before and after v13.
const CHANNELS_OLD: &str = "variants";
const CHANNELS_NEW: &str = "channels";
/// The key naming one channel inside a scene's asset entry, before and after v13.
const CHANNEL_OLD: &str = "variant";
const CHANNEL_NEW: &str = "channel";

/// Rename `old` to `new` in one map, keeping its position, so the file's
/// diff is that one line. Both present and equal folds to `new`; both
/// present and different is pushed onto `clashes` with `where_` and left
/// alone. Returns whether the map changed.
fn rename_key(
    map: &mut serde_json::Map<String, Value>,
    old: &str,
    new: &str,
    where_: &str,
    clashes: &mut Vec<String>,
) -> bool {
    let Some(old_value) = map.get(old) else {
        return false;
    };
    if let Some(new_value) = map.get(new) {
        if new_value != old_value {
            clashes.push(format!(
                "{where_}: \"{old}\" = {old_value} but \"{new}\" = {new_value}"
            ));
            return false;
        }
        map.remove(old);
        return true;
    }
    let taken = std::mem::take(map);
    *map = taken
        .into_iter()
        .map(|(key, value)| if key == old { (new.to_string(), value) } else { (key, value) })
        .collect();
    true
}

/// Walk a `{properties, children}` config tree, renaming the entity channel
/// property in every node's `properties`.
fn rename_property_in_tree(node: &mut Value, path: &str, clashes: &mut Vec<String>) -> bool {
    let Value::Object(map) = node else {
        return false;
    };
    let mut changed = false;
    if let Some(Value::Object(properties)) = map.get_mut("properties") {
        let where_ = if path.is_empty() { "/" } else { path };
        changed |= rename_key(properties, CHANNELS_OLD, CHANNELS_NEW, where_, clashes);
    }
    if let Some(Value::Object(children)) = map.get_mut("children") {
        for (name, child) in children.iter_mut() {
            changed |= rename_property_in_tree(child, &format!("{path}/{name}"), clashes);
        }
    }
    changed
}

/// Walk `scenes.json`, renaming the channel key of every asset entry.
fn rename_scene_entries(node: &mut Value, path: &str, clashes: &mut Vec<String>) -> bool {
    let Value::Object(map) = node else {
        return false;
    };
    let mut changed = false;
    if let Some(Value::Array(assets)) = map
        .get_mut("properties")
        .and_then(|p| p.as_object_mut())
        .and_then(|p| p.get_mut("assets"))
    {
        for (index, entry) in assets.iter_mut().enumerate() {
            if let Value::Object(entry) = entry {
                let where_ = format!("{path} assets[{index}]");
                changed |= rename_key(entry, CHANNEL_OLD, CHANNEL_NEW, &where_, clashes);
            }
        }
    }
    if let Some(Value::Object(children)) = map.get_mut("children") {
        for (name, child) in children.iter_mut() {
            changed |= rename_scene_entries(child, &format!("{path}/{name}"), clashes);
        }
    }
    changed
}

/// Point every browser column spec that names the channel property at its
/// new name. Labels were already moved by v5; this moves the key itself.
fn rename_column_keys(node: &mut Value) -> bool {
    let mut changed = false;
    match node {
        Value::Array(items) => {
            for item in items {
                changed |= rename_column_keys(item);
            }
        }
        Value::Object(map) => {
            for field in ["key", "property_path"] {
                if map.get(field).and_then(|v| v.as_str()) == Some(CHANNELS_OLD) {
                    map.insert(field.to_string(), Value::String(CHANNELS_NEW.to_string()));
                    changed = true;
                }
            }
            for (_, value) in map.iter_mut() {
                changed |= rename_column_keys(value);
            }
        }
        _ => {}
    }
    changed
}

/// v13's edits to a project's config database, as `(file, new data)`: empty
/// when there is nothing to do, or why it cannot run.
///
/// The publish channel used to be spelled "variant" in the database — the
/// `variants` entity property (and its schema default), the `variant` key of
/// a scene's asset entries, and the browser column naming that property —
/// colliding with USD's own variants. The package reads and writes only the
/// `channels`/`channel` spelling from this version on, so every project is
/// brought to it here, on upgrade, rather than every reader carrying both.
///
/// Published data (`export/**/context.json`, staged `.usda` URIs) is NOT
/// touched: published versions are immutable, and their readers keep
/// accepting the old spelling instead.
///
/// A record carrying both spellings with the same value folds to the new
/// one; with different values the step refuses, naming every such record,
/// because keeping either would be a guess.
fn v13_plan(cfg: &Path) -> Result<Vec<(PathBuf, Value)>, String> {
    let db = cfg.join("db");
    let mut out = Vec::new();
    let mut clashes = Vec::new();
    for name in ["schemas.json", "entity.json", "scenes.json", "config.json"] {
        let path = db.join(name);
        if !path.is_file() {
            continue; // this project has no such database
        }
        let mut data = load_json(&path)?;
        let mut found = Vec::new();
        let changed = match name {
            "scenes.json" => rename_scene_entries(&mut data, "", &mut found),
            "config.json" => rename_column_keys(&mut data),
            _ => rename_property_in_tree(&mut data, "", &mut found),
        };
        clashes.extend(found.into_iter().map(|f| format!("{}: {f}", path.display())));
        if changed {
            out.push((path, data));
        }
    }
    if clashes.is_empty() {
        return Ok(out);
    }
    let mut why = format!(
        "{} record(s) name the publish channel under both its old and new key with \
         different values, so neither can be kept safely and nothing was written:",
        clashes.len()
    );
    for clash in &clashes {
        why.push_str(&format!("\n    {clash}"));
    }
    why.push_str("\n  Delete whichever of the two is wrong, then launch again");
    Err(why)
}

/// A template's directory under `root`, `/`-separated: the key of a
/// [`templates::STOCK`] row.
fn template_key(root: &Path, file: &Path) -> String {
    file.parent()
        .and_then(|dir| dir.strip_prefix(root).ok())
        .map(|dir| {
            dir.components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/")
        })
        .unwrap_or_default()
}

/// Record the layout version a project has reached.
fn write_version(config: &Path, version: u32) -> Result<(), String> {
    let path = config.join(VERSION_FILE);
    let style = newlines::style_of_file(&path);
    let text = newlines::apply(&format!("{{\n    \"version\": {version}\n}}\n"), style);
    std::fs::write(&path, text)
        .map_err(|e| format!("could not stamp {}: {e}", path.display()))
}

/// What a migration run did.
#[derive(Debug, Clone)]
pub struct Report {
    pub from: u32,
    pub to: u32,
    /// Steps that ran, in order, each with its result.
    pub applied: Vec<(Step, Result<(), String>)>,
    /// Set when the run was refused up front because a pending step could not
    /// run. Nothing was written in that case.
    pub refused: Option<(Step, String)>,
}

impl Report {
    pub fn is_ok(&self) -> bool {
        self.refused.is_none() && self.applied.iter().all(|(_, r)| r.is_ok())
    }
}

/// Bring a project forward, refusing the whole run if any pending step cannot
/// complete.
///
/// The refusal is the lesson of the 2026-08-26 bulk run: applying steps until
/// one fails leaves the project stamped at the last success, which is a
/// part-migrated state nobody asked for. Checking every pending step first
/// means a project that cannot be fully migrated is left exactly as it was.
pub fn migrate(project_path: &Path, template_dir: &Path) -> Report {
    migrate_through(project_path, template_dir, latest_version())
}

/// [`migrate`], stopping at the step that reaches `last`. The tests of a
/// historical step use it to look at that step's output before a later step
/// (v11 retires the convention modules v6-v8 edit) moves on from it.
fn migrate_through(project_path: &Path, template_dir: &Path, last: u32) -> Report {
    let from = current_version(project_path);
    let mut report = Report {
        from,
        to: from,
        applied: Vec::new(),
        refused: None,
    };

    if let Some((step, why)) = first_blocker_through(project_path, template_dir, last) {
        report.refused = Some((step, why));
        return report;
    }

    let cfg = config_dir(project_path);
    for step in pending_through(project_path, last) {
        let result = step
            .apply(project_path, template_dir)
            .and_then(|()| write_version(&cfg, step.version()));
        let ok = result.is_ok();
        report.applied.push((step, result));
        if !ok {
            break;
        }
        report.to = step.version();
    }
    report
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `_config` with the given `version.json` contents (None = unstamped).
    fn project(version: Option<u32>) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("_config");
        std::fs::create_dir_all(cfg.join("db")).unwrap();
        if let Some(v) = version {
            std::fs::write(
                cfg.join(VERSION_FILE),
                format!("{{\n    \"version\": {v}\n}}\n"),
            )
            .unwrap();
        }
        dir
    }

    #[test]
    fn unstamped_reads_as_version_zero() {
        let dir = project(None);
        assert_eq!(current_version(dir.path()), 0);
        assert_eq!(pending(dir.path()).len(), STEPS.len());
    }

    #[test]
    fn a_current_project_has_nothing_pending() {
        let dir = project(Some(latest_version()));
        assert!(pending(dir.path()).is_empty());
    }

    #[test]
    fn config_dir_accepts_either_a_root_or_the_config_itself() {
        let dir = project(Some(3));
        assert_eq!(current_version(dir.path()), 3);
        assert_eq!(current_version(&dir.path().join("_config")), 3);
    }

    #[test]
    fn versions_are_dense_and_ordered() {
        // A gap or a repeat would let a project stamp past a step it never ran.
        for (index, step) in STEPS.iter().enumerate() {
            assert_eq!(step.version(), index as u32 + 1);
        }
        assert_eq!(latest_version(), STEPS.len() as u32);
    }

    /// The regression this module exists for: a project with no config database
    /// must be refused *before* anything is written, not part-migrated.
    #[test]
    fn a_project_without_schemas_json_is_blocked_not_migrated() {
        let dir = project(None);
        let template = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(template.path().join("_config").join("templates")).unwrap();
        std::fs::create_dir_all(template.path().join("_config").join("ocio")).unwrap();

        let blocker = first_blocker(dir.path(), template.path());
        let (step, why) = blocker.expect("a project with no schemas.json must be blocked");
        assert_eq!(step, Step::AddEntityDepartments);
        assert!(why.contains("schemas.json"), "unhelpful message: {why}");

        // And the steps before it are still individually ready — the run is
        // refused as a whole, rather than being allowed to get part-way.
        let flight = preflight(dir.path(), template.path());
        assert!(flight[0].1.is_ready());
        assert!(flight[1].1.is_ready());
    }

    #[test]
    fn a_customized_convention_is_blocked() {
        let dir = project(None);
        let template = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("_config").join("config_convention.py"),
            "# hand-rolled, nothing like the stock engine\n",
        )
        .unwrap();

        let (step, why) = first_blocker(dir.path(), template.path()).expect("must be blocked");
        assert_eq!(step, Step::ConventionToPackage);
        assert!(why.contains("customization"), "unhelpful message: {why}");
    }

    /// A template scaffold complete enough for every step to be Ready.
    fn scaffold() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let templates = dir.path().join("_config").join("templates").join("assets");
        std::fs::create_dir_all(&templates).unwrap();
        std::fs::write(templates.join("template.py"), "# packaged\n").unwrap();
        let ocio = dir.path().join("_config").join("ocio");
        std::fs::create_dir_all(&ocio).unwrap();
        std::fs::write(ocio.join("tumblehead.ocio"), "ocio_profile_version: 2\n").unwrap();
        std::fs::write(dir.path().join("_config").join(conventions::STORAGE_FILE), SCAFFOLD_STORAGE)
            .unwrap();
        dir
    }

    /// The packaged storage table.
    const SCAFFOLD_STORAGE: &str =
        include_str!("../../../../scripts/project_template/_config/storage.json");

    /// The last step a test of v6-v8's edits to the convention modules runs:
    /// v11 retires those modules, so their output is looked at before it.
    const BEFORE_V11: u32 = 10;

    /// The convention a project has once v6 has run.
    ///
    /// It carries no `temp_path`, so v8 is a no-op on it — the tests that use
    /// it are about v6 and must not pick up v8's rewrite as well.
    const PACKAGED_CONVENTION: &str = concat!(
        "class StorageConvention:\n",
        "    def __init__(self):\n",
        "        self.assets_path = self.project_path / 'assets'\n",
        "        self.export_path = self.project_path / 'export'\n",
    );

    /// The same file as a project frozen before v6 would have it.
    const CONVENTION_WITH_KITS: &str = concat!(
        "class StorageConvention:\n",
        "    def __init__(self):\n",
        "        self.assets_path = self.project_path / 'assets'\n",
        "        self.kits_path = self.project_path / 'kits'\n",
        "        self.export_path = self.project_path / 'export'\n",
    );

    /// A scaffold-shaped convention as every project carries it before v8:
    /// `temp:/` anchored to the project drive.
    const CONVENTION_WITH_PROJECT_TEMP: &str = concat!(
        "from tumblepipe.storage import StorageConvention\n",
        "\n",
        "\n",
        "class ProjectStorageConvention(StorageConvention):\n",
        "    def __init__(self):\n",
        "        project_name = get_project_name()\n",
        "        self.project_path = get_project_path()\n",
        "        self.cache_path = self.project_path.parent / f'{project_name}_cache'\n",
        "        self.temp_path = self.project_path.parent / f'{project_name}_temp'\n",
        "        self.proxy_path = self.project_path.parent / f'{project_name}_proxy'\n",
    );

    /// A project with a real config database, so every step can run.
    fn database_project() -> tempfile::TempDir {
        let dir = project(None);
        let db = dir.path().join("_config").join("db");
        std::fs::write(
            db.join("schemas.json"),
            "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {}\n        }\n    }\n}\n",
        )
        .unwrap();
        std::fs::write(
            db.join("config.json"),
            "{\n    \"columns\": [\n        {\n            \"key\": \"variants\",\n            \"label\": \"Variants\",\n            \"property_path\": \"variants\",\n            \"tooltip\": \"Variants to render\"\n        }\n    ]\n}\n",
        )
        .unwrap();
        std::fs::write(
            dir.path().join("_config").join("config_convention.py"),
            "class ProjectConfigConvention:\n    pass\n",
        )
        .unwrap();
        dir
    }

    #[test]
    fn a_full_run_reaches_the_latest_version() {
        let dir = database_project();
        let template = scaffold();
        let report = migrate(dir.path(), template.path());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.from, 0);
        assert_eq!(report.to, latest_version());
        assert_eq!(current_version(dir.path()), latest_version());
        assert!(pending(dir.path()).is_empty());
    }

    /// The v5 relabel: the label and tooltip move, the property key does not
    /// (v13 moves that, and is not run here).
    #[test]
    fn relabelling_moves_the_label_but_never_the_key() {
        let dir = database_project();
        let template = scaffold();
        migrate_through(dir.path(), template.path(), Step::RefreshStockTemplates.version());

        let config = load_json(&dir.path().join("_config").join("db").join("config.json")).unwrap();
        let column = &config["columns"][0];
        assert_eq!(column["label"], "Channels");
        assert_eq!(column["tooltip"], "Channels to render");
        assert_eq!(column["key"], "variants", "the storage key must not move");
        assert_eq!(column["property_path"], "variants");
    }

    #[test]
    fn migrating_is_idempotent_and_backs_up_exactly_once() {
        let dir = database_project();
        let template = scaffold();
        let convention = dir.path().join("_config").join("config_convention.py");
        let original = std::fs::read_to_string(&convention).unwrap();

        migrate_through(dir.path(), template.path(), BEFORE_V11);
        let backup = convention.with_file_name("config_convention.py.bak");
        assert_eq!(std::fs::read_to_string(&backup).unwrap(), original);
        let after_first = std::fs::read_to_string(&convention).unwrap();

        // Rewind the stamp so every step runs a second time against current files.
        write_version(&dir.path().join("_config"), 0).unwrap();
        let second = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(second.is_ok(), "{second:?}");
        // The .bak still holds the true original, not the shim from run one.
        assert_eq!(std::fs::read_to_string(&backup).unwrap(), original);
        assert_eq!(std::fs::read_to_string(&convention).unwrap(), after_first);
    }

    /// The 2026-08-26 regression, end to end: a project that cannot complete
    /// the run is left *exactly* as it was, not stamped part-way.
    #[test]
    fn a_blocked_run_writes_nothing_at_all() {
        let dir = project(None); // no db/schemas.json
        let template = scaffold();
        std::fs::write(
            dir.path().join("_config").join("config_convention.py"),
            "class ProjectConfigConvention:\n    pass\n",
        )
        .unwrap();

        let report = migrate(dir.path(), template.path());
        assert!(!report.is_ok());
        let (step, _) = report.refused.expect("the run must be refused up front");
        assert_eq!(step, Step::AddEntityDepartments);
        assert!(report.applied.is_empty(), "nothing may have been applied");

        // Untouched: no version stamp, no shim, no .bak, no templates dir.
        assert_eq!(current_version(dir.path()), 0);
        let cfg = dir.path().join("_config");
        assert!(!cfg.join(VERSION_FILE).exists());
        assert!(!cfg.join("config_convention.py.bak").exists());
        assert!(!cfg.join("templates").exists());
        assert!(std::fs::read_to_string(cfg.join("config_convention.py"))
            .unwrap()
            .contains("class ProjectConfigConvention"));
    }

    /// These files sit on a shared drive. Flipping their line endings would
    /// turn every migration into a whole-file diff, so a CRLF project stays
    /// CRLF and an LF project stays LF — on every platform.
    #[test]
    fn line_endings_survive_a_migration() {
        for style in ["\r\n", "\n"] {
            let dir = project(None);
            let cfg = dir.path().join("_config");
            let db = cfg.join("db");
            let column = concat!(
                "{\n",
                "    \"columns\": [\n",
                "        {\n",
                "            \"key\": \"variants\",\n",
                "            \"label\": \"Variants\"\n",
                "        }\n",
                "    ]\n",
                "}\n",
            )
            .replace('\n', style);
            std::fs::write(db.join("config.json"), &column).unwrap();
            // Multi-line on purpose: a minified file has no line-ending style
            // to preserve, and a real config DB is always pretty-printed.
            let schemas = concat!(
                "{\n",
                "    \"children\": {\n",
                "        \"entity\": {\n",
                "            \"properties\": {}\n",
                "        }\n",
                "    }\n",
                "}\n",
            )
            .replace('\n', style);
            std::fs::write(db.join("schemas.json"), &schemas).unwrap();
            let template = scaffold();

            let report = migrate(dir.path(), template.path());
            assert!(report.is_ok(), "{report:?}");

            for name in ["config.json", "schemas.json"] {
                let text = std::fs::read_to_string(db.join(name)).unwrap();
                let crlf = text.contains("\r\n");
                assert_eq!(
                    crlf,
                    style == "\r\n",
                    "{name} changed line-ending style (wanted CRLF={})",
                    style == "\r\n"
                );
            }
            // version.json did not exist here, so it is a *new* file and gets
            // LF on every platform. Preservation is about not churning files
            // that are already on the share; a new one has no style to keep.
            let stamp = std::fs::read_to_string(cfg.join(VERSION_FILE)).unwrap();
            assert!(!stamp.contains("\r\n"), "a new version.json should be LF");

            // Re-stamping an existing one keeps whatever it has.
            std::fs::write(
                cfg.join(VERSION_FILE),
                "{\r\n    \"version\": 0\r\n}\r\n",
            )
            .unwrap();
            write_version(&cfg, 5).unwrap();
            let restamped = std::fs::read_to_string(cfg.join(VERSION_FILE)).unwrap();
            assert!(restamped.contains("\r\n"), "an existing CRLF stamp must stay CRLF");
        }
    }

    /// The templates step compares text to decide whether to touch a file. A
    /// byte-compare would call a CRLF project file different from the LF
    /// scaffold every single run, backing up and rewriting all of them.
    #[test]
    fn a_crlf_template_matching_the_scaffold_is_left_alone() {
        let dir = database_project();
        let template = scaffold();
        let dst = dir
            .path()
            .join("_config")
            .join("templates")
            .join("assets")
            .join("template.py");
        std::fs::create_dir_all(dst.parent().unwrap()).unwrap();
        std::fs::write(&dst, "# packaged\r\n").unwrap(); // same text, CRLF

        migrate(dir.path(), template.path());
        assert!(
            !dst.with_file_name("template.py.bak").exists(),
            "a CRLF copy of an identical template must not be rewritten"
        );
        assert_eq!(std::fs::read_to_string(&dst).unwrap(), "# packaged\r\n");
    }

    /// v6's payload: the retired `kits` entries go, and nothing else moves.
    #[test]
    fn kits_lines_are_dropped_and_the_rest_kept_verbatim() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        std::fs::write(&path, CONVENTION_WITH_KITS).unwrap();

        let report = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), PACKAGED_CONVENTION);
        assert_eq!(
            std::fs::read_to_string(path.with_file_name("storage_convention.py.bak")).unwrap(),
            CONVENTION_WITH_KITS,
            "the original must be preserved"
        );
    }

    /// The reason v6 edits rather than refreshes. Live projects legitimately
    /// differ from the packaged convention — most still import the pre-rename
    /// `tumblehead.*`, several carry a real path override — so the step has to
    /// leave every line it did not come for exactly where it was.
    #[test]
    fn a_customized_convention_keeps_its_customization() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        let mine = concat!(
            "from tumblehead.storage import StorageConvention
",
            "class StorageConvention:
",
            "    def __init__(self):
",
            "        _primary_path = Path('/mnt/c')
",
            "        self.assets_path = Path('//studio/share/assets')
",
            "        self.kits_path = self.project_path / 'kits'
",
            "        self.export_path = self.project_path / 'export'
",
        );
        std::fs::write(&path, mine).unwrap();

        let report = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(report.is_ok(), "a customized convention must not block: {report:?}");

        let after = std::fs::read_to_string(&path).unwrap();
        assert!(!after.contains("kits_path"), "the kits line should be gone");
        for kept in [
            "_primary_path = Path('/mnt/c')",
            "self.assets_path = Path('//studio/share/assets')",
        ] {
            assert!(after.contains(kept), "customization lost: {kept}");
        }
        // v7 runs in the same sweep and repoints the renamed package — that is
        // a fix, not a lost customization.
        assert!(after.contains("from tumblepipe.storage import StorageConvention"));
    }

    #[test]
    fn a_convention_without_kits_is_left_alone() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        std::fs::write(&path, PACKAGED_CONVENTION).unwrap();

        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        assert!(
            !path.with_file_name("storage_convention.py.bak").exists(),
            "a convention with no kits entries must not be rewritten or backed up"
        );
    }

    /// A project with no convention at all is a no-op, not a failure.
    #[test]
    fn a_missing_convention_is_not_an_error() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        let _ = std::fs::remove_file(&path);

        assert!(migrate(dir.path(), template.path()).is_ok());
        assert!(!path.exists(), "v6 must not invent a convention file");
    }

    /// v7's payload. `tumblehead` was renamed to `tumblepipe`; a project frozen
    /// before that imports a package that no longer exists, and since
    /// `Client.__init__` executes all three convention modules at construction,
    /// the project cannot be opened at all.
    #[test]
    fn stale_package_imports_are_repointed_in_every_convention() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        std::fs::write(
            cfg.join("naming_convention.py"),
            "from tumblehead.naming import NamingConvention\n",
        )
        .unwrap();
        std::fs::write(
            cfg.join("storage_convention.py"),
            concat!(
                "from tumblehead.storage import StorageConvention\n",
                "from tumblehead.util.uri import Uri\n",
                "import tumblehead.api as api\n",
                "        _primary_path = Path('//tumblehead/share')\n",
            ),
        )
        .unwrap();

        let report = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(report.is_ok(), "{report:?}");

        let naming = std::fs::read_to_string(cfg.join("naming_convention.py")).unwrap();
        assert_eq!(naming, "from tumblepipe.naming import NamingConvention\n");

        let storage = std::fs::read_to_string(cfg.join("storage_convention.py")).unwrap();
        assert!(!storage.contains("from tumblehead."));
        assert!(!storage.contains("import tumblehead."));
        assert!(storage.contains("from tumblepipe.storage import StorageConvention"));
        assert!(storage.contains("import tumblepipe.api as api"));
        // A path that merely mentions the studio name is not an import.
        assert!(
            storage.contains("Path('//tumblehead/share')"),
            "only the import prefix should move"
        );
        assert!(cfg.join("naming_convention.py.bak").exists());
    }

    #[test]
    fn conventions_already_on_the_new_package_are_left_alone() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        std::fs::write(
            cfg.join("naming_convention.py"),
            "from tumblepipe.naming import NamingConvention\n",
        )
        .unwrap();

        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        assert!(
            !cfg.join("naming_convention.py.bak").exists(),
            "an already-correct convention must not be rewritten or backed up"
        );
    }

    /// v8's payload: temp leaves the project drive, and the helper it now
    /// calls is imported.
    #[test]
    fn project_drive_temp_is_repointed_at_local_scratch() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        std::fs::write(&path, CONVENTION_WITH_PROJECT_TEMP).unwrap();

        let report = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(report.is_ok(), "{report:?}");

        let after = std::fs::read_to_string(&path).unwrap();
        assert!(
            after.contains("        self.temp_path = default_temp_path()"),
            "temp was not repointed:\n{after}"
        );
        assert!(!after.contains("_temp'"), "the old anchor survived:\n{after}");
        assert!(after.contains(
            "from tumblepipe.storage import StorageConvention, default_temp_path"
        ));
        // Only the temp line moves — the sibling paths are a different
        // decision and stay where the project put them.
        for kept in [
            "self.cache_path = self.project_path.parent / f'{project_name}_cache'",
            "self.proxy_path = self.project_path.parent / f'{project_name}_proxy'",
        ] {
            assert!(after.contains(kept), "sibling path lost: {kept}");
        }
        assert_eq!(
            std::fs::read_to_string(path.with_file_name("storage_convention.py.bak")).unwrap(),
            CONVENTION_WITH_PROJECT_TEMP,
            "the original must be preserved"
        );
    }

    /// v8 moves temp *off the project drive*, and nothing more. A project that
    /// already keeps scratch somewhere else is out of its scope, whatever that
    /// somewhere is — including the WSL-era `_home / 'th_temp'` form that 15 of
    /// the 16 live projects still carry (`_home = Path('/mnt/c') / 'users' /
    /// 'tumblehead'`, which is machine-local, so it never produced the stray
    /// `<project>_temp` this step exists for). Repointing those at the packaged
    /// helper is a separate call, not a side effect of this one.
    #[test]
    fn a_hand_pointed_temp_is_left_alone() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        let mine = concat!(
            "from tumblepipe.storage import StorageConvention\n",
            "class ProjectStorageConvention(StorageConvention):\n",
            "    def __init__(self):\n",
            "        _home = Path('/mnt/c') / 'users' / 'tumblehead'\n",
            "        self.temp_path = _home / 'th_temp' / project_name\n",
        );
        std::fs::write(&path, mine).unwrap();

        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            mine,
            "a project's own scratch root must survive"
        );
        assert!(
            !path.with_file_name("storage_convention.py.bak").exists(),
            "a convention v8 has nothing to do to must not be rewritten"
        );
    }

    /// Re-running a completed migration must not append the import twice.
    #[test]
    fn repointing_temp_is_idempotent() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        std::fs::write(&path, CONVENTION_WITH_PROJECT_TEMP).unwrap();

        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        let once = std::fs::read_to_string(&path).unwrap();

        // Wind the stamp back so the step runs a second time against its own
        // output — what a re-run after a part-migrated project looks like.
        write_version(&dir.path().join("_config"), 7).unwrap();
        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), once);
    }

    /// The fallback path: a convention that does not import `StorageConvention`
    /// by name still ends up with the helper defined.
    #[test]
    fn the_helper_import_is_added_when_there_is_none_to_extend() {
        let dir = database_project();
        let template = scaffold();
        let path = dir.path().join("_config").join("storage_convention.py");
        std::fs::write(
            &path,
            concat!(
                "import tumblepipe.storage as storage\n",
                "class ProjectStorageConvention(storage.StorageConvention):\n",
                "    def __init__(self):\n",
                "        self.temp_path = self.project_path.parent / 'scratch'\n",
            ),
        )
        .unwrap();

        assert!(migrate_through(dir.path(), template.path(), BEFORE_V11).is_ok());
        let after = std::fs::read_to_string(&path).unwrap();
        assert!(
            after.starts_with("from tumblepipe.storage import default_temp_path\n"),
            "the helper import should lead the import block:\n{after}"
        );
        assert!(after.contains("        self.temp_path = default_temp_path()"));
    }

    /// v9 re-runs the refresh for a project already past v2, and keeps both
    /// originals: v2's `.bak` is not overwritten, v9's lands beside it.
    #[test]
    fn a_project_past_v2_gets_the_templates_refreshed_again() {
        let dir = database_project();
        let template = scaffold();
        let dst = dir
            .path()
            .join("_config")
            .join("templates")
            .join("assets")
            .join("template.py");
        std::fs::create_dir_all(dst.parent().unwrap()).unwrap();
        std::fs::write(&dst, "# stale\r\n").unwrap();
        std::fs::write(dst.with_file_name("template.py.bak"), "# v2 original\n").unwrap();
        write_version(&dir.path().join("_config"), 8).unwrap();

        let report = migrate(dir.path(), template.path());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.to, latest_version());
        assert_eq!(std::fs::read_to_string(&dst).unwrap(), "# packaged\r\n");
        assert_eq!(
            std::fs::read_to_string(dst.with_file_name("template.py.bak")).unwrap(),
            "# v2 original\n"
        );
        assert_eq!(
            std::fs::read_to_string(dst.with_file_name("template.py.v9.bak")).unwrap(),
            "# stale\r\n"
        );
    }

    #[test]
    fn seeding_ocio_never_clobbers_a_hand_tuned_config() {
        let dir = database_project();
        let template = scaffold();
        let ocio = dir.path().join("_config").join("ocio");
        std::fs::create_dir_all(&ocio).unwrap();
        std::fs::write(ocio.join("tumblehead.ocio"), "MINE\n").unwrap();

        migrate(dir.path(), template.path());
        assert_eq!(
            std::fs::read_to_string(ocio.join("tumblehead.ocio")).unwrap(),
            "MINE\n"
        );
    }

    #[test]
    fn an_identical_template_is_left_alone_with_no_backup() {
        let dir = database_project();
        let template = scaffold();
        let dst = dir
            .path()
            .join("_config")
            .join("templates")
            .join("assets")
            .join("template.py");
        std::fs::create_dir_all(dst.parent().unwrap()).unwrap();
        std::fs::write(&dst, "# packaged\n").unwrap(); // byte-identical to the scaffold

        migrate(dir.path(), template.path());
        assert!(
            !dst.with_file_name("template.py.bak").exists(),
            "an identical template must not be rewritten or backed up"
        );
    }

    /// The render vars section of a project's `root_default_prims.usda` as
    /// the scaffold shipped it before v10 (trimmed to the parts v10 reads).
    const ROOT_DEFAULTS_WITH_ALPHA: &str = concat!(
        "def Scope \"Render\"\n",
        "{\n",
        "    def Scope \"Products\"\n",
        "    {\n",
        "        def Scope \"Vars\"\n",
        "        {\n",
        "            def RenderVar \"beauty\" (\n",
        "                apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "            )\n",
        "            {\n",
        "                uniform token dataType = \"color3f\"\n",
        "                custom token driver:parameters:aov:format = \"color3f\"\n",
        "                string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                custom string driver:parameters:aov:name = \"beauty\"\n",
        "                uniform string sourceName = \"C.*[LO]\"\n",
        "            }\n",
        "\n",
        "            def RenderVar \"alpha\" (\n",
        "                apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "            )\n",
        "            {\n",
        "                uniform token dataType = \"float\"\n",
        "                string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                uniform string sourceName = \"ray:noholdouts;hit\"\n",
        "            }\n",
        "\n",
        "            def RenderVar \"albedo\" (\n",
        "                apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "            )\n",
        "            {\n",
        "                uniform token dataType = \"color3f\"\n",
        "                custom token driver:parameters:aov:format = \"color3h\"\n",
        "            }\n",
        "        }\n",
        "\n",
        "        def RenderProduct \"renderproduct\"\n",
        "        {\n",
        "            rel orderedVars = [\n",
        "                </Render/Products/Vars/beauty>,\n",
        "                </Render/Products/Vars/alpha>,\n",
        "                </Render/Products/Vars/albedo>,\n",
        "            ]\n",
        "        }\n",
        "    }\n",
        "}\n",
    );

    const ROOT_DEFAULTS_RGBA: &str = concat!(
        "def Scope \"Render\"\n",
        "{\n",
        "    def Scope \"Products\"\n",
        "    {\n",
        "        def Scope \"Vars\"\n",
        "        {\n",
        "            def RenderVar \"beauty\" (\n",
        "                apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "            )\n",
        "            {\n",
        "                uniform token dataType = \"color4f\"\n",
        "                custom token driver:parameters:aov:format = \"color4f\"\n",
        "                string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                custom string driver:parameters:aov:name = \"beauty\"\n",
        "                uniform string sourceName = \"C.*[LO]\"\n",
        "            }\n",
        "\n",
        "            def RenderVar \"albedo\" (\n",
        "                apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "            )\n",
        "            {\n",
        "                uniform token dataType = \"color3f\"\n",
        "                custom token driver:parameters:aov:format = \"color3h\"\n",
        "            }\n",
        "        }\n",
        "\n",
        "        def RenderProduct \"renderproduct\"\n",
        "        {\n",
        "            rel orderedVars = [\n",
        "                </Render/Products/Vars/beauty>,\n",
        "                </Render/Products/Vars/albedo>,\n",
        "            ]\n",
        "        }\n",
        "    }\n",
        "}\n",
    );

    #[test]
    fn v10_drops_the_alpha_var_and_makes_the_beauty_rgba() {
        assert_eq!(
            beauty_alpha::root_defaults_edit(ROOT_DEFAULTS_WITH_ALPHA),
            Edit::Changed(ROOT_DEFAULTS_RGBA.to_string())
        );
        // Idempotent: a project already through it is left as it is
        assert_eq!(beauty_alpha::root_defaults_edit(ROOT_DEFAULTS_RGBA), Edit::Unchanged);
    }

    /// A file v10 does not recognise blocks the run up front and is never
    /// written -- here a beauty that is not named `beauty`, which would
    /// otherwise lose the project its alpha with nothing RGBA to replace it.
    #[test]
    fn v10_refuses_an_unrecognised_file_and_writes_nothing() {
        let dir = database_project();
        let template = scaffold();
        let usd = dir.path().join("_config").join("usd");
        std::fs::create_dir_all(&usd).unwrap();
        let odd = ROOT_DEFAULTS_WITH_ALPHA.replace("RenderVar \"beauty\"", "RenderVar \"C\"");
        std::fs::write(usd.join("root_default_prims.usda"), &odd).unwrap();
        write_version(&dir.path().join("_config"), 9).unwrap();

        let (step, why) = first_blocker(dir.path(), template.path()).expect("must be blocked");
        assert_eq!(step, Step::BeautyCarriesAlpha);
        assert!(why.contains("root_default_prims.usda"), "{why}");
        let report = migrate(dir.path(), template.path());
        assert!(!report.is_ok());
        assert_eq!(current_version(dir.path()), 9);
        assert_eq!(std::fs::read_to_string(usd.join("root_default_prims.usda")).unwrap(), odd);
        assert!(!usd.join("root_default_prims.usda.v10.bak").exists());
    }

    /// The packaged scaffold is already what v10 produces, so a new project
    /// and a migrated one agree.
    #[test]
    fn v10_leaves_the_packaged_scaffold_unchanged() {
        let scaffold = include_str!(
            "../../../../scripts/project_template/_config/usd/root_default_prims.usda"
        );
        assert_eq!(beauty_alpha::root_defaults_edit(scaffold), Edit::Unchanged);
        assert!(scaffold.contains("uniform token dataType = \"color4f\""));
        assert!(!scaffold.contains("\"alpha\""));
    }

    /// One-off, read-only check against real project files: set
    /// `TH_V10_SAMPLES=<dir>` holding one folder per project with copies of
    /// its `root_default_prims.usda` / `context.json`, and run with
    /// `--ignored --nocapture`. Each result is written beside the copy as
    /// `*.v10` for review; nothing else is touched.
    #[test]
    #[ignore]
    fn v10_on_sample_files() {
        let root = std::path::PathBuf::from(std::env::var("TH_V10_SAMPLES").expect("set TH_V10_SAMPLES"));
        let mut dirs: Vec<_> = std::fs::read_dir(&root).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).collect();
        dirs.sort();
        for dir in dirs {
            for (name, edit) in [
                ("root_default_prims.usda", beauty_alpha::root_defaults_edit as fn(&str) -> Edit),
                ("context.json", beauty_alpha::context_json_edit),
            ] {
                let path = dir.join(name);
                let Ok(text) = std::fs::read_to_string(&path) else { continue };
                let verdict = match edit(&newlines::normalize(&text)) {
                    Edit::Unchanged => "unchanged".to_string(),
                    Edit::Changed(out) => {
                        std::fs::write(path.with_extension(format!("{}.v10", path.extension().unwrap().to_str().unwrap())), out).unwrap();
                        "migrated".to_string()
                    }
                    Edit::Refused(why) => format!("REFUSED: {why}"),
                };
                println!("V10 {} {name}: {verdict}", dir.file_name().unwrap().to_str().unwrap());
            }
        }
    }

    #[test]
    fn v10_edits_a_project_and_keeps_its_line_endings_and_a_backup() {
        let dir = database_project();
        let template = scaffold();
        let usd = dir.path().join("_config").join("usd");
        std::fs::create_dir_all(&usd).unwrap();
        let crlf = ROOT_DEFAULTS_WITH_ALPHA.replace('\n', "\r\n");
        std::fs::write(usd.join("root_default_prims.usda"), &crlf).unwrap();
        std::fs::write(
            usd.join("context.json"),
            "{\n    \"outputs\": [{\n        \"uri\": \"config:/usd/root_default_prims\",\n        \"parameters\": {\n            \"aov_names\": [\"beauty\", \"alpha\", \"albedo\", \"normal\"]\n        }\n    }]\n}\n",
        )
        .unwrap();
        write_version(&dir.path().join("_config"), 9).unwrap();

        let report = migrate_through(dir.path(), template.path(), BEFORE_V11);
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.to, 10);
        assert_eq!(
            std::fs::read_to_string(usd.join("root_default_prims.usda")).unwrap(),
            ROOT_DEFAULTS_RGBA.replace('\n', "\r\n")
        );
        assert_eq!(
            std::fs::read_to_string(usd.join("root_default_prims.usda.v10.bak")).unwrap(),
            crlf
        );
        let context = load_json(&usd.join("context.json")).unwrap();
        assert_eq!(
            context["outputs"][0]["parameters"]["aov_names"],
            serde_json::json!(["beauty", "albedo", "normal"])
        );
        // A text edit: the file keeps its own (non-serde) layout
        assert!(std::fs::read_to_string(usd.join("context.json"))
            .unwrap()
            .contains("\"outputs\": [{\n"));
        assert!(usd.join("context.json.v10.bak").is_file());
    }

    #[test]
    fn v10_leaves_a_project_without_render_defaults_alone() {
        let dir = database_project();
        let template = scaffold();
        write_version(&dir.path().join("_config"), 9).unwrap();
        let report = migrate(dir.path(), template.path());
        assert!(report.is_ok(), "{report:?}");
        assert!(!dir.path().join("_config").join("usd").exists());
    }

    #[test]
    fn the_stock_engine_and_the_shim_are_both_ready() {
        let template = tempfile::tempdir().unwrap();
        for text in [
            "class ProjectConfigConvention:\n    pass\n",
            "from tumblepipe.config.store import JsonConfigStore\n",
        ] {
            let dir = project(None);
            std::fs::write(
                dir.path().join("_config").join("config_convention.py"),
                text,
            )
            .unwrap();
            assert!(Step::ConventionToPackage
                .readiness(dir.path(), template.path())
                .is_ready());
        }
    }

    /// v11 end to end on a project as the scaffold left it: the table it
    /// writes is the packaged one, and all three modules are retired with
    /// their originals kept.
    #[test]
    fn v11_turns_the_stock_conventions_into_the_packaged_table() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        let fixtures = [
            ("storage_convention.py", include_str!("../tests/fixtures/storage_convention_v8.py")),
            ("naming_convention.py", include_str!("../tests/fixtures/naming_convention_current.py")),
            ("config_convention.py", include_str!("../tests/fixtures/config_convention_shim.py")),
        ];
        for (name, text) in fixtures {
            std::fs::write(cfg.join(name), text).unwrap();
        }
        let pycache = cfg.join("__pycache__");
        std::fs::create_dir_all(&pycache).unwrap();
        std::fs::write(pycache.join("storage_convention.cpython-311.pyc"), "x").unwrap();
        std::fs::write(pycache.join("unrelated.cpython-311.pyc"), "x").unwrap();
        write_version(&cfg, 10).unwrap();

        let report = migrate_through(dir.path(), template.path(), 11);
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.to, 11);
        assert_eq!(
            std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap(),
            SCAFFOLD_STORAGE
        );
        for (name, text) in fixtures {
            assert!(!cfg.join(name).exists(), "{name} was not retired");
            assert_eq!(
                std::fs::read_to_string(cfg.join(format!("{name}.v11.bak"))).unwrap(),
                text
            );
        }
        assert!(!pycache.join("storage_convention.cpython-311.pyc").exists());
        assert!(pycache.join("unrelated.cpython-311.pyc").exists());
    }

    /// A project coming from v5 is preflighted with its modules still in
    /// their pre-v6 shape, and v6-v8 reshape them before v11 reads them.
    #[test]
    fn v11_reads_a_project_coming_from_v5() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        std::fs::write(
            cfg.join("storage_convention.py"),
            include_str!("../tests/fixtures/storage_convention_initial.py"),
        )
        .unwrap();
        std::fs::write(
            cfg.join("naming_convention.py"),
            include_str!("../tests/fixtures/naming_convention_current.py")
                .replace("from tumblepipe.", "from tumblehead."),
        )
        .unwrap();
        write_version(&cfg, 5).unwrap();

        assert_eq!(first_blocker(dir.path(), template.path()), None);
        let report = migrate(dir.path(), template.path());
        assert!(report.is_ok(), "{report:?}");
        // v8 moved temp to local scratch before v11 read it
        assert_eq!(
            std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap(),
            SCAFFOLD_STORAGE
        );
        assert!(!cfg.join("storage_convention.py").exists());
    }

    /// The live share's commonest shape: WSL-era scratch and an edit root on
    /// another drive. Every path is carried over as it resolved.
    #[test]
    fn v11_keeps_a_projects_own_paths() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        let mine = include_str!("../tests/fixtures/storage_convention_v8.py")
            .replace(
                "        project_name = get_project_name()\n",
                concat!(
                    "        project_name = get_project_name()\n",
                    "        _primary_path = Path('/mnt/c')\n",
                    "        _secondary_path = Path('/mnt/e')\n",
                    "        _home = _primary_path / 'users' / 'tumblehead'\n",
                ),
            )
            .replace(
                "self.temp_path = default_temp_path()",
                "self.temp_path = _home / 'th_temp' / project_name",
            )
            .replace(
                "self.edit_path = self.project_path / 'edit'",
                "self.edit_path = _secondary_path / 'Sources' / 'Buzz'",
            )
            .replace('\n', "\r\n");
        std::fs::write(cfg.join("storage_convention.py"), &mine).unwrap();
        write_version(&cfg, 10).unwrap();

        assert!(migrate(dir.path(), template.path()).is_ok());
        let table = load_json(&cfg.join(conventions::STORAGE_FILE)).unwrap();
        assert_eq!(table["temp"], "/mnt/c/users/tumblehead/th_temp/{project_name}");
        assert_eq!(table["edit"], "/mnt/e/Sources/Buzz");
        assert_eq!(table["cache"], "{project_parent}/{project_name}_cache");
        assert_eq!(
            std::fs::read_to_string(cfg.join("storage_convention.py.v11.bak")).unwrap(),
            mine,
            "the original keeps its bytes, CRLF included"
        );
    }

    /// What v11 cannot carry over blocks the run before anything is written.
    #[test]
    fn v11_refuses_what_it_cannot_carry_over_and_writes_nothing() {
        let storage = include_str!("../tests/fixtures/storage_convention_v8.py");
        let naming = include_str!("../tests/fixtures/naming_convention_current.py");
        let cases = [
            (
                "storage_convention.py",
                storage.replace(
                    "self.cache_path = self.project_path.parent / f'{project_name}_cache'",
                    "self.cache_path = Path(os.environ['STUDIO_CACHE'])",
                ),
                "storage_convention.py",
            ),
            (
                "naming_convention.py",
                naming.replace("zfill(4)", "zfill(3)"),
                "naming_convention.py",
            ),
            (
                "config_convention.py",
                "from tumblepipe.config.store import JsonConfigStore\n\nclass Mine(JsonConfigStore):\n    pass\n\ndef create():\n    return Mine()\n".to_string(),
                "config_convention.py",
            ),
        ];
        for (name, text, named) in cases {
            let dir = database_project();
            let template = scaffold();
            let cfg = dir.path().join("_config");
            std::fs::write(cfg.join(name), &text).unwrap();
            write_version(&cfg, 10).unwrap();

            let (step, why) =
                first_blocker(dir.path(), template.path()).expect("must be blocked");
            assert_eq!(step, Step::ConventionsToData);
            assert!(why.contains(named), "{name}: {why}");
            let report = migrate(dir.path(), template.path());
            assert!(!report.is_ok());
            assert_eq!(current_version(dir.path()), 10);
            assert_eq!(std::fs::read_to_string(cfg.join(name)).unwrap(), text);
            assert!(!cfg.join(conventions::STORAGE_FILE).exists());
            assert!(!cfg.join(format!("{name}.v11.bak")).exists());
        }
    }

    /// A project with no storage module resolved nothing; it gets the
    /// scaffold's table. One with a table of its own keeps it.
    #[test]
    fn v11_without_a_storage_module() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        write_version(&cfg, 10).unwrap();
        assert!(migrate(dir.path(), template.path()).is_ok());
        assert_eq!(
            std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap(),
            SCAFFOLD_STORAGE
        );

        let own = SCAFFOLD_STORAGE.replace("{project_path}/edit", "//cutroom/edit");
        std::fs::write(cfg.join(conventions::STORAGE_FILE), &own).unwrap();
        write_version(&cfg, 10).unwrap();
        assert!(migrate(dir.path(), template.path()).is_ok());
        assert_eq!(std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap(), own);
        assert!(!cfg.join("storage.json.v11.bak").exists());
    }

    /// Re-running v11 over its own output (or over a module that came back)
    /// changes nothing and never overwrites the first backup.
    #[test]
    fn v11_is_idempotent() {
        let dir = database_project();
        let template = scaffold();
        let cfg = dir.path().join("_config");
        let original = include_str!("../tests/fixtures/storage_convention_v8.py");
        std::fs::write(cfg.join("storage_convention.py"), original).unwrap();
        write_version(&cfg, 10).unwrap();
        assert!(migrate(dir.path(), template.path()).is_ok());
        let once = std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap();

        std::fs::write(cfg.join("storage_convention.py"), original.replace("self.project_path / 'edit'", "self.project_path / 'cut'"))
            .unwrap();
        write_version(&cfg, 10).unwrap();
        assert!(migrate(dir.path(), template.path()).is_ok());
        assert_eq!(
            std::fs::read_to_string(cfg.join("storage_convention.py.v11.bak")).unwrap(),
            original
        );
        assert!(!cfg.join("storage_convention.py").exists());
        let twice = std::fs::read_to_string(cfg.join(conventions::STORAGE_FILE)).unwrap();
        assert_eq!(twice, once.replace("{project_path}/edit", "{project_path}/cut"));
        assert_eq!(
            std::fs::read_to_string(cfg.join("storage.json.v11.bak")).unwrap(),
            once
        );
    }

    /// The package refusing to ship without its own table blocks v11 rather
    /// than writing a guess.
    #[test]
    fn v11_needs_the_packaged_table() {
        let dir = database_project();
        let template = scaffold();
        std::fs::remove_file(template.path().join("_config").join(conventions::STORAGE_FILE))
            .unwrap();
        write_version(&dir.path().join("_config"), 10).unwrap();
        let (step, why) = first_blocker(dir.path(), template.path()).expect("must be blocked");
        assert_eq!(step, Step::ConventionsToData);
        assert!(why.contains("incomplete"), "{why}");
    }

    // ── v12: the stock templates are refreshed ──────────────────────────

    /// The real packaged scaffold, which v12 refreshes from.
    fn packaged_scaffold() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..")
            .join("scripts")
            .join("project_template")
    }

    /// Every template the scaffold has shipped, as (directory under
    /// `templates/`, fixture file, text).
    fn shipped_templates() -> Vec<(String, PathBuf, String)> {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("templates");
        let mut files = Vec::new();
        find_files(&root, None, &mut files);
        files
            .into_iter()
            .map(|file| {
                let text = std::fs::read_to_string(&file).unwrap();
                (template_key(&root, &file), file, text)
            })
            .collect()
    }

    /// Every packaged template, as (directory under `templates/`, LF text).
    fn packaged_templates() -> Vec<(String, String)> {
        let root = scaffold_templates_dir(&packaged_scaffold());
        let mut files = Vec::new();
        find_files(&root, Some("template.py"), &mut files);
        files
            .into_iter()
            .map(|file| {
                let text = newlines::normalize(&std::fs::read_to_string(&file).unwrap());
                (template_key(&root, &file), text)
            })
            .collect()
    }

    fn project_template(dir: &Path, relative: &str) -> PathBuf {
        let mut path = dir.join("_config").join("templates");
        for part in relative.split('/') {
            path = path.join(part);
        }
        path.join("template.py")
    }

    /// A project through v11 holding `templates` (directory, text).
    fn project_at_v11(templates: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = project(Some(11));
        for (relative, text) in templates {
            let path = project_template(dir.path(), relative);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, text).unwrap();
        }
        dir
    }

    fn packaged_text(relative: &str) -> String {
        packaged_templates()
            .into_iter()
            .find(|(key, _)| key == relative)
            .map(|(_, text)| text)
            .unwrap()
    }

    /// The fixtures and the STOCK table are the same set, so a row cannot be
    /// added without the text it stands for, nor a fixture without its row.
    #[test]
    fn every_shipped_template_has_a_stock_row_and_every_row_a_fixture() {
        let shipped = shipped_templates();
        assert_eq!(shipped.len(), templates::STOCK.len());
        for (relative, file, text) in &shipped {
            assert!(
                templates::is_stock(relative, text),
                "{} has no STOCK row: (\"{relative}\", 0x{:016x})",
                file.display(),
                templates::fingerprint(text)
            );
        }
    }

    /// A packaged template that is not a STOCK row would read as customised
    /// to the next refresh step on every project left on it.
    #[test]
    fn the_packaged_templates_are_stock_and_call_nothing_removed() {
        let packaged = packaged_templates();
        assert!(!packaged.is_empty());
        for (relative, text) in packaged {
            assert!(
                templates::is_stock(&relative, &text),
                "templates/{relative}/template.py changed without a STOCK row: copy it to \
                 src/wizard/core/tests/fixtures/templates/{relative}/<blob>.py and add \
                 (\"{relative}\", 0x{:016x}) to templates::STOCK",
                templates::fingerprint(&text)
            );
            assert_eq!(templates::removed_apis(&text), Vec::<String>::new(), "{relative}");
        }
    }

    /// Each version the scaffold ever shipped, at its own department, is
    /// brought up to the packaged template — line endings kept, no backup.
    #[test]
    fn v12_refreshes_every_shipped_version() {
        let template = packaged_scaffold();
        for (index, (relative, file, text)) in shipped_templates().into_iter().enumerate() {
            let text = if index % 2 == 0 { text } else { text.replace('\n', "\r\n") };
            let dir = project_at_v11(&[(&relative, &text)]);
            let report = migrate(dir.path(), &template);
            assert!(report.is_ok(), "{}: {report:?}", file.display());
            assert_eq!(report.to, latest_version());
            let path = project_template(dir.path(), &relative);
            let after = std::fs::read_to_string(&path).unwrap();
            assert_eq!(newlines::normalize(&after), packaged_text(&relative), "{}", file.display());
            assert_eq!(newlines::style_of(&after), newlines::style_of(&text));
            let mut left = Vec::new();
            find_files(path.parent().unwrap(), None, &mut left);
            assert_eq!(left, vec![path.clone()], "no backup of a stock template");
        }
    }

    /// A template the project lacks is put in place, and every other
    /// department's is too: a v12 project has the whole packaged set.
    #[test]
    fn v12_fills_in_missing_templates() {
        let dir = project_at_v11(&[]);
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        for (relative, text) in packaged_templates() {
            let after = std::fs::read_to_string(project_template(dir.path(), &relative)).unwrap();
            assert_eq!(after, text, "{relative}");
        }
    }

    /// A studio's own template that only uses what still exists is theirs to
    /// keep: not written, not backed up — while a stock one beside it is
    /// still refreshed.
    #[test]
    fn v12_leaves_a_clean_customised_template_alone() {
        let customised = format!("{}\n# studio: our own render column\n", packaged_text("shots/render"));
        let own_department = "from tumblepipe.pipe.houdini.lops import import_shot\n";
        let old_animation = shipped_templates()
            .into_iter()
            .find(|(relative, _, text)| {
                relative == "shots/animation" && *text != packaged_text("shots/animation")
            })
            .unwrap()
            .2;
        let dir = project_at_v11(&[
            ("shots/render", &customised),
            ("shots/stunts", own_department),
            ("shots/animation", &old_animation),
        ]);
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.to, latest_version());
        let render = project_template(dir.path(), "shots/render");
        assert_eq!(std::fs::read_to_string(&render).unwrap(), customised);
        assert_eq!(
            std::fs::read_to_string(project_template(dir.path(), "shots/stunts")).unwrap(),
            own_department
        );
        assert_eq!(
            std::fs::read_to_string(project_template(dir.path(), "shots/animation")).unwrap(),
            packaged_text("shots/animation")
        );
        let mut files = Vec::new();
        find_files(&dir.path().join("_config").join("templates"), None, &mut files);
        assert!(
            files.iter().all(|f| f.file_name().unwrap() == "template.py"),
            "nothing is backed up: {files:?}"
        );
    }

    /// A customised template calling a removed API blocks the step, naming
    /// every such file and what it calls — and nothing at all is written,
    /// not even the stock template beside it.
    #[test]
    fn v12_blocks_a_customised_template_calling_a_removed_api() {
        let old_render = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("572f2ad8d6.py"))
            .unwrap()
            .2;
        let customised = format!("{old_render}\n# studio: tweaked\n");
        let own_department = "from tumblepipe.pipe.houdini.util import disable_layer_save_path\n";
        let old_animation = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("cb38ffa938.py"))
            .unwrap()
            .2;
        let dir = project_at_v11(&[
            ("shots/render", &customised),
            ("shots/stunts", own_department),
            ("shots/animation", &old_animation),
        ]);
        let preflight = preflight(dir.path(), &packaged_scaffold());
        let [
            (Step::RefreshStockTemplates, Readiness::Blocked(why)),
            (Step::ChannelKeys, Readiness::Ready),
            (Step::RefreshStockTemplatesAgain, Readiness::Blocked(_)),
            (Step::LayoutCameraFollowsResolution, Readiness::Blocked(_)),
            (Step::AnimationRigsArePublished, Readiness::Blocked(_)),
        ] = preflight.as_slice()
        else {
            panic!("v12 must be blocked: {preflight:?}");
        };
        for expected in [
            "2 customised template(s)",
            &project_template(dir.path(), "shots/render").display().to_string(),
            "imports tumblepipe.pipe.houdini.lops.render_vars",
            "imports tumblepipe.pipe.houdini.lops.puzzle_mattes",
            "calls .set_shot_uri",
            &project_template(dir.path(), "shots/stunts").display().to_string(),
            "imports tumblepipe.pipe.houdini.util",
            "delete it",
        ] {
            assert!(why.contains(expected), "{expected:?} missing from: {why}");
        }
        assert!(!why.contains("animation"), "a stock template is not reported: {why}");

        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(!report.is_ok());
        assert!(report.applied.is_empty());
        assert_eq!(current_version(dir.path()), 11);
        for (relative, text) in [
            ("shots/render", customised.as_str()),
            ("shots/stunts", own_department),
            ("shots/animation", old_animation.as_str()),
        ] {
            assert_eq!(
                std::fs::read_to_string(project_template(dir.path(), relative)).unwrap(),
                text,
                "{relative} was written"
            );
        }
    }

    /// The fix the message offers: deleting the template takes the packaged one.
    #[test]
    fn v12_after_deleting_the_blocked_template() {
        let dir = project_at_v11(&[("shots/render", "node.set_shot_uri(uri)\n")]);
        assert!(first_blocker(dir.path(), &packaged_scaffold()).is_some());
        std::fs::remove_file(project_template(dir.path(), "shots/render")).unwrap();
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(
            std::fs::read_to_string(project_template(dir.path(), "shots/render")).unwrap(),
            packaged_text("shots/render")
        );
    }

    /// A project still to run v9 is judged as v9 leaves it: v9 replaces every
    /// packaged template (keeping a .v9.bak), so a customised one there does
    /// not block — but one in a department of the project's own still does.
    #[test]
    fn v12_with_v9_still_pending() {
        let dir = project(Some(8));
        let render = project_template(dir.path(), "shots/render");
        std::fs::create_dir_all(render.parent().unwrap()).unwrap();
        std::fs::write(&render, "node.set_shot_uri(uri)\n").unwrap();
        assert!(Step::RefreshStockTemplates
            .readiness(dir.path(), &packaged_scaffold())
            .is_ready());

        let own = project_template(dir.path(), "shots/stunts");
        std::fs::create_dir_all(own.parent().unwrap()).unwrap();
        std::fs::write(&own, "node.set_shot_uri(uri)\n").unwrap();
        let readiness = Step::RefreshStockTemplates.readiness(dir.path(), &packaged_scaffold());
        let Readiness::Blocked(why) = readiness else {
            panic!("a project's own department must still be checked");
        };
        assert!(why.contains("stunts") && !why.contains("render"), "{why}");
    }

    #[test]
    fn v12_is_idempotent() {
        let old_layout = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("cb72b1129e.py"))
            .unwrap()
            .2;
        let customised = "# ours\nfrom tumblepipe.pipe.houdini.lops import import_shot\n";
        let dir = project_at_v11(&[("shots/layout", &old_layout), ("shots/light", customised)]);
        let template = packaged_scaffold();
        assert!(migrate(dir.path(), &template).is_ok());
        let snapshot = |dir: &Path| {
            let mut files = Vec::new();
            find_files(&dir.join("_config").join("templates"), None, &mut files);
            files
                .into_iter()
                .map(|f| (f.clone(), std::fs::read_to_string(&f).unwrap()))
                .collect::<Vec<_>>()
        };
        let first = snapshot(dir.path());

        write_version(&dir.path().join("_config"), 11).unwrap();
        let second = migrate(dir.path(), &template);
        assert!(second.is_ok(), "{second:?}");
        assert_eq!(second.to, latest_version());
        assert_eq!(snapshot(dir.path()), first);
        assert!(v12_plan(&dir.path().join("_config"), &template, false)
            .unwrap()
            .is_empty());
    }

    /// A project made from this package is born at the latest version, with
    /// nothing to do.
    #[test]
    fn a_fresh_scaffold_is_already_at_v16() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("fresh");
        crate::scaffold_project(&packaged_scaffold(), &target, "fresh", 24).unwrap();
        assert_eq!(latest_version(), 16);
        assert_eq!(current_version(&target), latest_version());
        assert!(pending(&target).is_empty());
        assert!(v12_plan(&config_dir(&target), &packaged_scaffold(), false)
            .unwrap()
            .is_empty());
        assert!(v13_plan(&config_dir(&target)).unwrap().is_empty());
    }

    // ── v13: the publish channel is stored as a channel ─────────────────

    /// A v12 project's database as the package wrote it before v13: the
    /// channel spelled `variant` in every place it is stored.
    fn project_at_v12_with_variants() -> tempfile::TempDir {
        let dir = project(Some(12));
        let db = dir.path().join("_config").join("db");
        let files = [
            (
                "schemas.json",
                r#"{
    "properties": {},
    "children": {
        "entity": {
            "properties": {
                "fps": 24,
                "variants": [
                    "default"
                ],
                "departments": []
            },
            "children": {}
        },
        "config": {
            "properties": {},
            "children": {
                "discord": {
                    "properties": {},
                    "children": {
                        "channels": {
                            "properties": {
                                "channel": ""
                            },
                            "children": {}
                        }
                    }
                }
            }
        }
    }
}
"#,
            ),
            (
                "entity.json",
                r#"{
    "properties": {},
    "children": {
        "shots": {
            "properties": {},
            "children": {
                "010": {
                    "properties": {
                        "variants": [
                            "bg"
                        ]
                    },
                    "children": {
                        "020": {
                            "properties": {
                                "frame_start": 1001,
                                "variants": [
                                    "bg",
                                    "fg"
                                ],
                                "frame_end": 1100
                            },
                            "children": {}
                        }
                    }
                }
            }
        }
    }
}
"#,
            ),
            (
                "scenes.json",
                r#"{
    "properties": {},
    "children": {
        "park": {
            "properties": {
                "assets": [
                    {
                        "asset": "entity:/assets/PROP/bench",
                        "instances": 2,
                        "variant": "default"
                    },
                    {
                        "asset": "entity:/assets/CHAR/kid",
                        "instances": 1,
                        "variant": "feathers"
                    }
                ]
            },
            "children": {}
        }
    }
}
"#,
            ),
            (
                "config.json",
                r#"{
    "columns": [
        {
            "key": "variants",
            "label": "Channels",
            "property_path": "variants",
            "tooltip": "Publish channels to render"
        },
        {
            "key": "pool",
            "label": "Pool"
        }
    ]
}
"#,
            ),
        ];
        for (name, text) in files {
            std::fs::write(db.join(name), text).unwrap();
        }
        dir
    }

    fn db_text(dir: &Path, name: &str) -> String {
        std::fs::read_to_string(dir.join("_config").join("db").join(name)).unwrap()
    }

    #[test]
    fn v13_moves_every_stored_channel_to_its_own_name() {
        let dir = project_at_v12_with_variants();
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.applied.len(), 4);
        assert_eq!(report.to, 16);

        let db = dir.path().join("_config").join("db");
        let schemas = load_json(&db.join("schemas.json")).unwrap();
        let entity_props = &schemas["children"]["entity"]["properties"];
        assert_eq!(entity_props["channels"], serde_json::json!(["default"]));
        assert!(entity_props.get("variants").is_none());
        // The discord `channels` node and its `channel` property are a
        // different thing that happens to share the word; untouched.
        assert_eq!(
            schemas["children"]["config"]["children"]["discord"]["children"]["channels"]
                ["properties"]["channel"],
            ""
        );

        let entity = load_json(&db.join("entity.json")).unwrap();
        let seq = &entity["children"]["shots"]["children"]["010"];
        assert_eq!(seq["properties"]["channels"], serde_json::json!(["bg"]));
        let shot = &seq["children"]["020"]["properties"];
        assert_eq!(shot["channels"], serde_json::json!(["bg", "fg"]));
        assert!(shot.get("variants").is_none());
        // Renamed in place: the key keeps its position, so the diff is one line.
        let keys: Vec<&String> = shot.as_object().unwrap().keys().collect();
        assert_eq!(keys, ["frame_start", "channels", "frame_end"]);

        let scenes = load_json(&db.join("scenes.json")).unwrap();
        let assets = scenes["children"]["park"]["properties"]["assets"].as_array().unwrap();
        assert_eq!(assets[0]["channel"], "default");
        assert_eq!(assets[1]["channel"], "feathers");
        assert!(assets.iter().all(|a| a.get("variant").is_none()));

        let config = load_json(&db.join("config.json")).unwrap();
        assert_eq!(config["columns"][0]["key"], "channels");
        assert_eq!(config["columns"][0]["property_path"], "channels");
        assert_eq!(config["columns"][1]["key"], "pool");

        // The originals are kept beside each rewritten file.
        for name in ["schemas.json", "entity.json", "scenes.json", "config.json"] {
            let backup = db.join(format!("{name}.v13.bak"));
            assert!(backup.is_file(), "{name} has no v13 backup");
            assert!(std::fs::read_to_string(backup).unwrap().contains("variant"));
        }
    }

    /// An old project goes all the way through, v13 included.
    #[test]
    fn v13_runs_from_an_unstamped_project() {
        let dir = database_project();
        let db = dir.path().join("_config").join("db");
        std::fs::write(
            db.join("entity.json"),
            "{\n    \"properties\": {\n        \"variants\": [\n            \"bg\"\n        ]\n    },\n    \"children\": {}\n}\n",
        )
        .unwrap();
        let template = scaffold();
        let report = migrate(dir.path(), template.path());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(current_version(dir.path()), 16);
        let entity = load_json(&db.join("entity.json")).unwrap();
        assert_eq!(entity["properties"]["channels"], serde_json::json!(["bg"]));
        let config = load_json(&db.join("config.json")).unwrap();
        assert_eq!(config["columns"][0]["key"], "channels");
        assert_eq!(config["columns"][0]["label"], "Channels", "v5 still relabels first");
    }

    #[test]
    fn v13_is_idempotent() {
        let dir = project_at_v12_with_variants();
        assert!(migrate(dir.path(), &packaged_scaffold()).is_ok());
        let names = ["schemas.json", "entity.json", "scenes.json", "config.json"];
        let first: Vec<String> = names.iter().map(|n| db_text(dir.path(), n)).collect();

        write_version(&dir.path().join("_config"), 12).unwrap();
        let second = migrate(dir.path(), &packaged_scaffold());
        assert!(second.is_ok(), "{second:?}");
        assert_eq!(second.to, 16);
        let again: Vec<String> = names.iter().map(|n| db_text(dir.path(), n)).collect();
        assert_eq!(again, first);
        assert!(v13_plan(&dir.path().join("_config")).unwrap().is_empty());
    }

    /// A project that never stored a channel has nothing to rewrite: no
    /// file is written and no backup appears.
    #[test]
    fn v13_with_nothing_to_do_writes_nothing() {
        let dir = project(Some(12));
        let db = dir.path().join("_config").join("db");
        let entity = "{\n    \"properties\": {\n        \"fps\": 24\n    },\n    \"children\": {}\n}\n";
        std::fs::write(db.join("entity.json"), entity).unwrap();
        assert!(v13_plan(&dir.path().join("_config")).unwrap().is_empty());

        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.to, 16);
        assert_eq!(db_text(dir.path(), "entity.json"), entity);
        let mut files = Vec::new();
        find_files(&db, None, &mut files);
        assert_eq!(files, vec![db.join("entity.json")], "nothing else may be written");
    }

    /// Both spellings with the same value fold to the new one; with
    /// different values the run is refused and nothing is written.
    #[test]
    fn v13_folds_agreeing_spellings_and_refuses_disagreeing_ones() {
        let dir = project(Some(12));
        let db = dir.path().join("_config").join("db");
        std::fs::write(
            db.join("entity.json"),
            r#"{"properties": {"variants": ["bg"], "channels": ["bg"]}, "children": {}}"#,
        )
        .unwrap();
        assert!(migrate(dir.path(), &packaged_scaffold()).is_ok());
        let entity = load_json(&db.join("entity.json")).unwrap();
        assert_eq!(
            entity["properties"],
            serde_json::json!({"channels": ["bg"]})
        );

        let dir = project(Some(12));
        let db = dir.path().join("_config").join("db");
        let clash = r#"{"properties": {}, "children": {"park": {"properties": {"assets": [{"asset": "a", "variant": "bg", "channel": "fg"}]}, "children": {}}}}"#;
        std::fs::write(db.join("scenes.json"), clash).unwrap();
        let report = migrate(dir.path(), &packaged_scaffold());
        let (step, why) = report.refused.expect("a disagreeing record must refuse the run");
        assert_eq!(step, Step::ChannelKeys);
        assert!(why.contains("/park assets[0]"), "{why}");
        assert_eq!(current_version(dir.path()), 12);
        assert_eq!(db_text(dir.path(), "scenes.json"), clash);
    }

    /// v14 reaches a project v12 already refreshed: HideAndReek, RND and
    /// SideFXrig2 sat at v13 on the animation template that raised
    /// `KeyError: 'uri'`, which is stock (67282f6a0f), so v14 replaces it.
    /// A customised template beside it is left as it is.
    #[test]
    fn v14_refreshes_a_stock_template_v12_left_behind() {
        let shipped_at_v13 = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("67282f6a0f.py"))
            .unwrap()
            .2;
        assert!(shipped_at_v13.contains("asset_info['uri']"));
        let customised = "# studio layout\n";
        let dir = project(Some(13));
        for (relative, text) in [
            ("shots/animation", shipped_at_v13.replace('\n', "\r\n")),
            ("shots/layout", customised.to_string()),
        ] {
            let path = project_template(dir.path(), relative);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, text).unwrap();
        }
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(current_version(dir.path()), 16);
        let animation =
            std::fs::read_to_string(project_template(dir.path(), "shots/animation")).unwrap();
        assert_eq!(
            animation.replace("\r\n", "\n"),
            packaged_text("shots/animation").replace("\r\n", "\n")
        );
        assert!(animation.contains("\r\n"), "a CRLF template stays CRLF");
        assert!(!animation.contains("asset_info['uri']"));
        assert_eq!(
            std::fs::read_to_string(project_template(dir.path(), "shots/layout")).unwrap(),
            customised
        );
    }

    /// v15 brings a project on the previous stock layout template (e5a3cf17a6,
    /// whose render camera kept a 16:9 aspect) and blendshape template
    /// (886fc6b725, which left `blshp` an Xform) up to the packaged ones.
    #[test]
    fn v15_refreshes_the_stock_layout_and_blendshape_templates() {
        let shipped_at_v14 = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("e5a3cf17a6.py"))
            .unwrap()
            .2;
        assert!(!shipped_at_v14.contains("bind_aspect"));
        let dir = project(Some(14));
        let path = project_template(dir.path(), "shots/layout");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, &shipped_at_v14).unwrap();
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(current_version(dir.path()), 16);
        let layout = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            layout.replace("
", "
"),
            packaged_text("shots/layout").replace("
", "
")
        );
        assert!(layout.contains("bind_aspect(camera_node, entity_uri)"));

        let blendshape_at_v14 = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("886fc6b725.py"))
            .unwrap()
            .2;
        let dir = project(Some(14));
        let path = project_template(dir.path(), "assets/blendshape");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, &blendshape_at_v14).unwrap();
        assert!(migrate(dir.path(), &packaged_scaffold()).is_ok());
        assert!(std::fs::read_to_string(&path).unwrap().contains("UsdGeomScope"));
    }

    /// v16 brings a project on the previous stock animation template
    /// (b023f4189b, a rig row for every asset in the shot, no playblast in a
    /// Multi) up to the packaged one.
    #[test]
    fn v16_refreshes_the_stock_animation_template() {
        let shipped_at_v15 = shipped_templates()
            .into_iter()
            .find(|(_, file, _)| file.ends_with("b023f4189b.py"))
            .unwrap()
            .2;
        assert!(!shipped_at_v15.contains("has_rig_export"));
        let dir = project(Some(15));
        let path = project_template(dir.path(), "shots/animation");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, &shipped_at_v15).unwrap();
        let report = migrate(dir.path(), &packaged_scaffold());
        assert!(report.is_ok(), "{report:?}");
        assert_eq!(report.applied.len(), 1);
        assert_eq!(current_version(dir.path()), 16);
        let animation = std::fs::read_to_string(&path).unwrap();
        assert_eq!(animation, packaged_text("shots/animation"));
        assert!(animation.contains("import_rigs.has_rig_export(asset_uri)"));
        assert!(animation.contains("_add_member_playblast(animate_node, member_uri)"));
    }

    /// A CRLF database stays CRLF.
    #[test]
    fn v13_keeps_line_endings() {
        let dir = project_at_v12_with_variants();
        let db = dir.path().join("_config").join("db");
        let crlf = db_text(dir.path(), "entity.json").replace('\n', "\r\n");
        std::fs::write(db.join("entity.json"), crlf).unwrap();
        assert!(migrate(dir.path(), &packaged_scaffold()).is_ok());
        let after = db_text(dir.path(), "entity.json");
        assert!(after.contains("\"channels\"") && after.contains("\r\n"));
        assert!(!after.replace("\r\n", "").contains('\n'));
    }
}
