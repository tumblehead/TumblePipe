//! Core entity-URI resolution rules.
//!
//! Every function here is pure aside from two inputs: the `TH_EXPORT_PATH`
//! env var (read via [`crate::env::export_base`]) and the filesystem
//! (read by [`crate::version::find_latest`] when "latest" discovery is
//! needed). Paths are returned with forward slashes on every platform.

use std::path::{Path, PathBuf};

use crate::env::{export_base, latest_mode};
use crate::error::{ResolveError, ResolveResult};
use crate::uri::{EntityUri, SHARED_VARIANT};
use crate::version::find_latest;

pub const ROOT_DEPARTMENT: &str = "root";
const STAGED_DIR: &str = "_staged";
const ROOT_DIR: &str = "_root";
const SCENES_DIR: &str = "scenes";

pub fn resolve(uri: &EntityUri) -> ResolveResult<String> {
    let base = export_base().ok_or(ResolveError::MissingExportPath)?;

    let path = if uri.is_scene() {
        resolve_scene(&base, &uri.segments[1..], uri.version.as_deref())?
    } else if let Some(dept) = uri.department.as_deref() {
        if dept == ROOT_DEPARTMENT {
            resolve_root(&base, &uri.segments, uri.version.as_deref())?
        } else {
            resolve_department(
                &base,
                &uri.segments,
                dept,
                &uri.variant,
                uri.version.as_deref(),
            )?
        }
    } else {
        resolve_staged(&base, &uri.segments, &uri.variant, uri.version.as_deref())?
    };

    Ok(to_forward_slashes(path))
}

fn resolve_department(
    base: &Path,
    segments: &[String],
    department: &str,
    variant: &str,
    version: Option<&str>,
) -> ResolveResult<PathBuf> {
    let mut dir = base.to_path_buf();
    for seg in segments {
        dir.push(seg);
    }
    dir.push(variant);
    dir.push(department);

    let version_name = pick_version(&dir, version)?;
    let version_dir = dir.join(&version_name);

    let entity_name = segments.join("_");
    let file_name = if variant == SHARED_VARIANT {
        format!("{entity_name}_shared_{department}_{version_name}.usd")
    } else {
        format!("{entity_name}_{variant}_{department}_{version_name}.usd")
    };

    Ok(version_dir.join(file_name))
}

fn resolve_staged(
    base: &Path,
    segments: &[String],
    variant: &str,
    version: Option<&str>,
) -> ResolveResult<PathBuf> {
    let mut dir = base.to_path_buf();
    for seg in segments {
        dir.push(seg);
    }
    dir.push(STAGED_DIR);
    dir.push(variant);

    let version_name = pick_version(&dir, version)?;
    let version_dir = dir.join(&version_name);

    // Staged filenames drop the first segment (typically the entity type
    // like "assets") from the entity name.
    let entity_name = segments
        .get(1..)
        .unwrap_or(&[])
        .join("_");
    let file_name = format!("{entity_name}_{version_name}.usda");

    Ok(version_dir.join(file_name))
}

fn resolve_root(
    base: &Path,
    segments: &[String],
    version: Option<&str>,
) -> ResolveResult<PathBuf> {
    let mut dir = base.to_path_buf();
    for seg in segments {
        dir.push(seg);
    }
    dir.push(ROOT_DIR);

    let version_name = pick_version(&dir, version)?;
    let version_dir = dir.join(&version_name);

    let entity_name = segments.join("_");
    let file_name = format!("{entity_name}_root_{version_name}.usda");

    Ok(version_dir.join(file_name))
}

fn resolve_scene(
    base: &Path,
    scene_segments: &[String],
    version: Option<&str>,
) -> ResolveResult<PathBuf> {
    if scene_segments.is_empty() {
        return Err(ResolveError::EmptyScenePath);
    }

    let mut dir = base.join(SCENES_DIR);
    for seg in scene_segments {
        dir.push(seg);
    }
    dir.push(STAGED_DIR);

    let version_name = pick_version(&dir, version)?;
    let version_dir = dir.join(&version_name);

    let scene_name = scene_segments.last().expect("checked non-empty above");
    let file_name = format!("{scene_name}_{version_name}.usda");

    Ok(version_dir.join(file_name))
}

/// Apply the latest-mode override: when enabled, an explicit version is
/// ignored and the filesystem is scanned for the highest `vNNNN`.
fn pick_version(dir: &Path, requested: Option<&str>) -> ResolveResult<String> {
    if let Some(v) = requested {
        if !latest_mode() {
            return Ok(v.to_owned());
        }
    }
    find_latest(dir).ok_or_else(|| ResolveError::NoVersions {
        searched: to_forward_slashes(dir.to_path_buf()),
    })
}

fn to_forward_slashes(path: PathBuf) -> String {
    path.to_string_lossy().replace('\\', "/")
}
