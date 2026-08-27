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

use crate::load_json;

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
pub const STEPS: [Step; 8] = [
    Step::ConventionToPackage,
    Step::RefreshTemplates,
    Step::AddEntityDepartments,
    Step::SeedOcio,
    Step::RelabelVariantsAsChannels,
    Step::DropKitsFromStorageConvention,
    Step::FixConventionImports,
    Step::TempToLocalScratch,
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
    let at = current_version(project_path);
    STEPS.iter().copied().filter(|s| s.version() > at).collect()
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
            Step::RefreshTemplates => {
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
        }
    }
}

/// Readiness of every pending step, in order. Empty when nothing is pending.
pub fn preflight(project_path: &Path, template_dir: &Path) -> Vec<(Step, Readiness)> {
    pending(project_path)
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
    preflight(project_path, template_dir)
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
    let style = newlines::style_of_file(path);
    if let Ok(existing) = std::fs::read_to_string(path) {
        let backup = path.with_file_name(format!(
            "{}.bak",
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
            Step::RefreshTemplates => {
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
                        // Identical bar line endings — leave it, and leave its
                        // mtime alone. Comparing raw bytes would rewrite and
                        // back up every CRLF template on every run.
                        Ok(old)
                            if newlines::normalize(&old) == newlines::normalize(&new_text) =>
                        {
                            continue
                        }
                        _ => write_preserving(&dst, &new_text)?,
                    }
                }
                Ok(())
            }
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
        }
    }
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
    let from = current_version(project_path);
    let mut report = Report {
        from,
        to: from,
        applied: Vec::new(),
        refused: None,
    };

    if let Some((step, why)) = first_blocker(project_path, template_dir) {
        report.refused = Some((step, why));
        return report;
    }

    let cfg = config_dir(project_path);
    for step in pending(project_path) {
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
        dir
    }

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

    /// The v5 relabel: the label and tooltip move, the property key does not.
    /// The key is what every project's database already stores and what the
    /// published path and URI wire format spell.
    #[test]
    fn relabelling_moves_the_label_but_never_the_key() {
        let dir = database_project();
        let template = scaffold();
        migrate(dir.path(), template.path());

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

        migrate(dir.path(), template.path());
        let backup = convention.with_file_name("config_convention.py.bak");
        assert_eq!(std::fs::read_to_string(&backup).unwrap(), original);
        let after_first = std::fs::read_to_string(&convention).unwrap();

        // Rewind the stamp so every step runs a second time against current files.
        write_version(&dir.path().join("_config"), 0).unwrap();
        let second = migrate(dir.path(), template.path());
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

        let report = migrate(dir.path(), template.path());
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

        let report = migrate(dir.path(), template.path());
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

        assert!(migrate(dir.path(), template.path()).is_ok());
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

        let report = migrate(dir.path(), template.path());
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

        assert!(migrate(dir.path(), template.path()).is_ok());
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

        let report = migrate(dir.path(), template.path());
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

        assert!(migrate(dir.path(), template.path()).is_ok());
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

        assert!(migrate(dir.path(), template.path()).is_ok());
        let once = std::fs::read_to_string(&path).unwrap();

        // Wind the stamp back so the step runs a second time against its own
        // output — what a re-run after a part-migrated project looks like.
        write_version(&dir.path().join("_config"), 7).unwrap();
        assert!(migrate(dir.path(), template.path()).is_ok());
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

        assert!(migrate(dir.path(), template.path()).is_ok());
        let after = std::fs::read_to_string(&path).unwrap();
        assert!(
            after.starts_with("from tumblepipe.storage import default_temp_path\n"),
            "the helper import should lead the import block:\n{after}"
        );
        assert!(after.contains("        self.temp_path = default_temp_path()"));
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
}
