//! A shot's review media, as takes the player can switch between
//! (`tumblepipe.pipe.review_playlist`, `paths.review`, `paths.render`).
//!
//! A shot's takes are its playblasts and then its renders, each by
//! department in pipeline order and version oldest first: every playblast
//! (`render:/playblast/<shot>/<dept>/<shot>_<dept>_vNNNN.mp4`, or the bare
//! `vNNNN.mp4` written up to 1.87), and every render version whose frames
//! are all there (`render:/render/<shot>/<dept>/<layer>/vNNNN/<aov>/`), as
//! the frames of its beauty AOV, else its first. A department's *stack*
//! takes (its render layers on top of each other) follow its plain takes of
//! the same kind. [`get_shot`] is Python's `get_shot`, as the player's
//! [`th_playlist::Shot`].

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use th_playlist::{Frames, Media, Shot, StackLayer, Take};

use crate::Project;
use crate::departments::{SLAPCOMP, department_names, list_channels};
use crate::naming::{entity_stem, is_valid_version_name, version_key};
use crate::storage::slashed;
use crate::timeline::{BlockRange, get_frame_range, require_fps};
use crate::uri::Uri;
use crate::versions::list_version_paths;

/// The AOV a render take shows when it has one.
pub const BEAUTY: &str = "beauty";
/// What a stack take's label ends in.
pub const STACK_LABEL: &str = "layers";
/// Digits in a rendered frame's number (`$F4`).
pub const FRAME_PADDING: usize = 4;
/// Render versions checked at once: each check lists a folder, slow over
/// the network one at a time.
pub const CHECKS: usize = 16;

const MOVIE_EXTENSION: &str = "mp4";
const LAYER_MOVIE_EXTENSION: &str = "mov";

fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Python's `PurePath.stem` and `.suffix` (3.13): the suffix is from the
/// last `.`, unless that is the first or the last character.
fn split_suffix(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => (&name[..i], &name[i..]),
        _ => (name, ""),
    }
}

/// Names compare as the platform's glob does: without case on Windows.
fn same_name(a: &str, b: &str) -> bool {
    if cfg!(windows) { a.eq_ignore_ascii_case(b) } else { a == b }
}

fn list_dir(path: &Path) -> Vec<PathBuf> {
    match std::fs::read_dir(path) {
        Ok(entries) => entries.filter_map(Result::ok).map(|e| e.path()).collect(),
        Err(_) => Vec::new(),
    }
}

/// Where the playblasts of `entity`'s `department` (and render `layer`) are.
pub fn playblast_dir(project: &Project, entity: &Uri, department: &str, layer: Option<&str>) -> Result<PathBuf, String> {
    let mut uri = Uri::root("render").join("playblast").join_all(&entity.segments).join(department);
    if let Some(layer) = layer {
        uri = uri.join(layer);
    }
    project.storage.resolve(&uri).map_err(|e| e.to_string())
}

fn playblast_stem(entity: &Uri, department: &str, layer: Option<&str>) -> String {
    match layer {
        Some(layer) => entity_stem(&entity.segments, &[department, layer]),
        None => entity_stem(&entity.segments, &[department]),
    }
}

/// The version a playblast file is: `<stem>_<version>`, or a bare `<version>`.
fn version_of_movie(path: &Path, stem: &str) -> Option<String> {
    let name = file_name(path);
    let (file_stem, _) = split_suffix(&name);
    if is_valid_version_name(file_stem) {
        return Some(file_stem.to_string());
    }
    let (prefix, version) = file_stem.rsplit_once('_')?;
    (prefix == stem && is_valid_version_name(version)).then(|| version.to_string())
}

/// A folder's playblasts by version; the shot-named file wins over a bare one.
fn movie_versions(directory: &Path, stem: &str) -> HashMap<String, PathBuf> {
    let mut found: HashMap<String, PathBuf> = HashMap::new();
    if !directory.is_dir() {
        return found;
    }
    for path in list_dir(directory) {
        let name = file_name(&path);
        let (file_stem, suffix) = split_suffix(&name);
        if !same_name(suffix, &format!(".{MOVIE_EXTENSION}")) {
            continue;
        }
        let Some(version) = version_of_movie(&path, stem) else { continue };
        if found.contains_key(&version) && file_stem == version {
            continue;
        }
        found.insert(version, path);
    }
    found
}

fn sorted_versions<'a>(versions: impl IntoIterator<Item = &'a String>) -> Vec<String> {
    let mut sorted: Vec<String> = versions.into_iter().cloned().collect();
    sorted.sort_by_key(|v| version_key(v));
    sorted
}

/// The version names of `department`'s playblasts of `entity`, ascending.
pub fn list_playblast_versions(project: &Project, entity: &Uri, department: &str) -> Result<Vec<String>, String> {
    let directory = playblast_dir(project, entity, department, None)?;
    Ok(sorted_versions(movie_versions(&directory, &playblast_stem(entity, department, None)).keys()))
}

/// A version's playblast: the file on disk under either naming, else the
/// shot-named file to write.
pub fn playblast_path(project: &Project, entity: &Uri, department: &str, version: &str) -> Result<PathBuf, String> {
    let directory = playblast_dir(project, entity, department, None)?;
    let stem = playblast_stem(entity, department, None);
    let named = directory.join(format!("{stem}_{version}.{MOVIE_EXTENSION}"));
    if named.exists() {
        return Ok(named);
    }
    let bare = directory.join(format!("{version}.{MOVIE_EXTENSION}"));
    if bare.exists() {
        return Ok(bare);
    }
    Ok(named)
}

/// The layer movies a playblast version has, in `layers` order, each once.
pub fn layer_playblast_movies(
    project: &Project,
    entity: &Uri,
    department: &str,
    version: &str,
    layers: &[String],
) -> Result<Vec<(String, PathBuf)>, String> {
    let mut found: Vec<(String, PathBuf)> = Vec::new();
    for layer in layers {
        if found.iter().any(|(name, _)| name == layer) {
            continue;
        }
        let directory = playblast_dir(project, entity, department, Some(layer))?;
        let stem = playblast_stem(entity, department, Some(layer));
        let path = directory.join(format!("{stem}_{version}.{LAYER_MOVIE_EXTENSION}"));
        if path.is_file() {
            found.push((layer.clone(), path));
        }
    }
    Ok(found)
}

/// Every playblast of the shot: departments in pipeline order, versions
/// ascending, each department's layered versions (as stacks) after its
/// plain ones.
pub fn playblast_takes(project: &Project, entity: &Uri) -> Result<Vec<Take>, String> {
    let channels = list_channels(&project.config, entity)?;
    let mut takes = Vec::new();
    for department in department_names(&project.config, "shots")? {
        let mut stacks = Vec::new();
        for version in list_playblast_versions(project, entity, &department)? {
            let path = playblast_path(project, entity, &department, &version)?;
            takes.push(Take {
                label: format!("{department} {version}"),
                department: department.clone(),
                version: version.clone(),
                media: Media::Movie { path: slashed(&path) },
            });
            let movies = layer_playblast_movies(project, entity, &department, &version, &channels)?;
            if movies.is_empty() {
                continue;
            }
            stacks.push(Take {
                label: format!("{department} {version} {STACK_LABEL}"),
                department: department.clone(),
                version,
                media: Media::Stack {
                    layers: movies
                        .into_iter()
                        .map(|(name, path)| StackLayer { name, media: Media::Movie { path: slashed(&path) } })
                        .collect(),
                },
            });
        }
        takes.extend(stacks);
    }
    Ok(takes)
}

/// One AOV of a render version: its folder and its frames' names
/// (`<name>.<frame>.<suffix>`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aov {
    pub path: PathBuf,
    pub name: String,
    pub suffix: String,
}

impl Aov {
    /// Whether its frames are exactly `range`'s (`AOV.is_complete`).
    pub fn is_complete(&self, range: &BlockRange) -> bool {
        let prefix = format!("{}.", self.name);
        let suffix = format!(".{}", self.suffix);
        let mut found: Vec<i64> = Vec::new();
        for path in list_dir(&self.path) {
            let name = file_name(&path);
            if name.len() < prefix.len() + suffix.len()
                || !same_name(&name[..prefix.len()], &prefix)
                || !same_name(&name[name.len() - suffix.len()..], &suffix)
            {
                continue;
            }
            let (stem, _) = split_suffix(&name);
            match stem.rsplit('.').next().and_then(|n| n.trim().parse().ok()) {
                Some(number) => found.push(number),
                // Python's int() raises here; such a version cannot play.
                None => return false,
            }
        }
        found.sort();
        found == range.frames().collect::<Vec<_>>()
    }
}

/// One render version of one layer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RenderVersion {
    pub path: PathBuf,
    pub layer: String,
    pub version: String,
    /// By name, in folder order.
    pub aovs: Vec<(String, Aov)>,
    /// The layer's own frame name, for a layer without AOVs.
    pub name: String,
    pub suffix: String,
}

impl RenderVersion {
    /// The range its `context.json` records; None without one (or one that
    /// does not say first, last and step).
    pub fn frame_range(&self) -> Option<BlockRange> {
        let context = crate::io::load_json(&self.path.join("context.json")).ok()?;
        let number = |key: &str| context.get(key).and_then(serde_json::Value::as_i64);
        BlockRange::new(number("first_frame")?, number("last_frame")?, number("step_size")?).ok()
    }

    /// What its take shows, and the AOV's name: its beauty, else its first
    /// by name, else (no AOVs) the layer's own frames.
    pub fn shown(&self) -> (Aov, Option<String>) {
        if self.aovs.is_empty() {
            return (Aov { path: self.path.clone(), name: self.name.clone(), suffix: self.suffix.clone() }, None);
        }
        let (name, aov) = self
            .aovs
            .iter()
            .find(|(name, _)| name == BEAUTY)
            .or_else(|| self.aovs.iter().min_by(|a, b| a.0.cmp(&b.0)))
            .expect("aovs is not empty");
        (aov.clone(), Some(name.clone()))
    }

    /// Its range when it can play: it records one and every frame of what
    /// it shows is there.
    pub fn playable_range(&self) -> Option<BlockRange> {
        let range = self.frame_range()?;
        self.shown().0.is_complete(&range).then_some(range)
    }
}

/// A render layer's name and its versions.
pub type RenderLayer = (String, Vec<RenderVersion>);

/// A render department's layers (the shot's channels and the slapcomp) and
/// their versions oldest first; None when it never rendered (`get_render`).
pub fn get_render(project: &Project, entity: &Uri, department: &str) -> Result<Option<Vec<RenderLayer>>, String> {
    let root = project
        .storage
        .resolve(&Uri::root("render").join("render").join_all(&entity.segments).join(department))
        .map_err(|e| e.to_string())?;
    if !root.exists() {
        return Ok(None);
    }
    let mut names = list_channels(&project.config, entity)?;
    names.push(SLAPCOMP.to_string());
    let mut layers = Vec::new();
    for layer_path in list_dir(&root) {
        let layer = file_name(&layer_path);
        if !names.contains(&layer) {
            continue;
        }
        let mut versions = Vec::new();
        for version_path in list_version_paths(&layer_path) {
            let version = file_name(&version_path);
            let aovs = list_dir(&version_path)
                .into_iter()
                .filter(|aov_path| split_suffix(&file_name(aov_path)).1.is_empty())
                .map(|aov_path| {
                    let aov = file_name(&aov_path);
                    let name = entity_stem(&entity.segments, &[&layer, &aov, &version]);
                    (aov, Aov { path: aov_path, name, suffix: "exr".into() })
                })
                .collect();
            versions.push(RenderVersion {
                name: entity_stem(&entity.segments, &[&layer, &version]),
                path: version_path,
                layer: layer.clone(),
                version,
                aovs,
                suffix: "exr".into(),
            });
        }
        layers.push((layer, versions));
    }
    Ok(Some(layers))
}

/// `f` over `items`, up to [`CHECKS`] at once, results in order.
fn parallel_map<T: Sync, R: Send>(items: &[T], f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    if items.len() <= 1 {
        return items.iter().map(&f).collect();
    }
    let chunk = items.len().div_ceil(CHECKS);
    std::thread::scope(|scope| {
        let handles: Vec<_> = items.chunks(chunk).map(|part| scope.spawn(|| part.iter().map(&f).collect::<Vec<_>>())).collect();
        handles.into_iter().flat_map(|h| h.join().expect("a check panicked")).collect()
    })
}

fn exr_media(layer: &str, aov: &Aov, range: &BlockRange) -> Media {
    Media::Exr {
        layer: layer.to_string(),
        frames: Frames {
            directory: slashed(&aov.path),
            prefix: format!("{}.", aov.name),
            suffix: format!(".{}", aov.suffix),
            padding: FRAME_PADDING,
            first: range.first_frame,
            last: range.last_frame,
            step: range.step_size,
        },
    }
}

fn plain_render_takes(project: &Project, entity: &Uri) -> Result<Vec<Take>, String> {
    let mut versions: Vec<(String, RenderVersion)> = Vec::new();
    for department in department_names(&project.config, "render")? {
        let Some(mut layers) = get_render(project, entity, &department)? else { continue };
        layers.sort_by(|a, b| (a.0 == SLAPCOMP, &a.0).cmp(&(b.0 == SLAPCOMP, &b.0)));
        for (_, mut layer_versions) in layers {
            layer_versions.sort_by_key(|v| version_key(&v.version));
            versions.extend(layer_versions.into_iter().map(|v| (department.clone(), v)));
        }
    }
    let ranges = parallel_map(&versions, |(_, version)| version.playable_range());
    let mut takes = Vec::new();
    for ((department, version), range) in versions.into_iter().zip(ranges) {
        let Some(range) = range else { continue };
        let (aov, aov_name) = version.shown();
        let mut label = format!("{department} {} {}", version.version, version.layer);
        if let Some(aov_name) = aov_name {
            label = format!("{label} {aov_name}");
        }
        takes.push(Take { label, department, version: version.version.clone(), media: exr_media(&version.layer, &aov, &range) });
    }
    Ok(takes)
}

/// A department's render stacks, versions oldest first: each version's
/// takes of the shot's channels (not the slapcomp), in channel order, that
/// share the bottom one's frame range, when there are two or more.
fn render_stacks(department_takes: &[Take], channels: &[String]) -> Vec<Take> {
    let mut by_version: Vec<(String, Vec<(String, Media)>)> = Vec::new();
    for take in department_takes {
        let Media::Exr { layer, .. } = &take.media else { continue };
        let index = match by_version.iter().position(|(v, _)| *v == take.version) {
            Some(index) => index,
            None => {
                by_version.push((take.version.clone(), Vec::new()));
                by_version.len() - 1
            }
        };
        let found = &mut by_version[index].1;
        match found.iter_mut().find(|(name, _)| name == layer) {
            Some(slot) => slot.1 = take.media.clone(),
            None => found.push((layer.clone(), take.media.clone())),
        }
    }
    by_version.sort_by_key(|(version, _)| version_key(version));
    let span = |media: &Media| match media {
        Media::Exr { frames, .. } => (frames.first, frames.last, frames.step),
        _ => unreachable!("render takes are EXRs"),
    };
    let mut stacks = Vec::new();
    for (version, found) in by_version {
        let layers: Vec<(String, Media)> = channels
            .iter()
            .filter(|name| name.as_str() != SLAPCOMP)
            .filter_map(|name| found.iter().find(|(n, _)| n == name).map(|(_, media)| (name.clone(), media.clone())))
            .collect();
        let Some(bottom) = layers.first().map(|(_, media)| span(media)) else { continue };
        let layers: Vec<StackLayer> =
            layers.into_iter().filter(|(_, media)| span(media) == bottom).map(|(name, media)| StackLayer { name, media }).collect();
        if layers.len() < 2 {
            continue;
        }
        let department = department_takes[0].department.clone();
        stacks.push(Take { label: format!("{department} {version} {STACK_LABEL}"), department, version, media: Media::Stack { layers } });
    }
    stacks
}

/// Every render of the shot that can play: render departments in pipeline
/// order, then layers (the slapcomp last), then versions oldest first; each
/// department's stacks after its plain takes.
pub fn render_takes(project: &Project, entity: &Uri) -> Result<Vec<Take>, String> {
    let takes = plain_render_takes(project, entity)?;
    let channels = list_channels(&project.config, entity)?;
    let mut departments: Vec<&String> = Vec::new();
    for take in &takes {
        if !departments.contains(&&take.department) {
            departments.push(&take.department);
        }
    }
    let mut ordered = Vec::new();
    for department in departments {
        let department_takes: Vec<Take> = takes.iter().filter(|t| &t.department == department).cloned().collect();
        ordered.extend(department_takes.iter().cloned());
        ordered.extend(render_stacks(&department_takes, &channels));
    }
    Ok(ordered)
}

/// When a movie take's file was written, in nanoseconds since the epoch;
/// the earliest possible when it cannot be read.
fn written_at(take: &Take) -> i128 {
    let Media::Movie { path } = &take.media else { return i128::MIN };
    match std::fs::metadata(path).and_then(|m| m.modified()) {
        Ok(time) => match time.duration_since(UNIX_EPOCH) {
            Ok(d) => d.as_nanos() as i128,
            Err(e) => -(e.duration().as_nanos() as i128),
        },
        Err(_) => i128::MIN,
    }
}

/// The take a shot opens on: the playblast written last, whichever
/// department (a tie goes to the later take), else the last plain take,
/// else (only stacks) the last take.
pub fn default_take(takes: &[Take]) -> usize {
    let plain: Vec<usize> = (0..takes.len()).filter(|&i| !takes[i].is_stack()).collect();
    let movies: Vec<usize> = plain.iter().copied().filter(|&i| matches!(takes[i].media, Media::Movie { .. })).collect();
    if let Some(best) = movies.iter().copied().max_by_key(|&i| (written_at(&takes[i]), i)) {
        return best;
    }
    if let Some(&last) = plain.last() {
        return last;
    }
    takes.len().saturating_sub(1)
}

/// The sequence a shot is in: its path between `shots` and its name.
pub fn sequence_of(entity: &Uri) -> String {
    let segments = &entity.segments;
    if segments.len() < 2 {
        return String::new();
    }
    segments[1..segments.len() - 1].join("/")
}

/// The shot and its takes (possibly none). Refused when it has no frame
/// range or fps: it cannot be placed.
pub fn get_shot(project: &Project, entity: &Uri) -> Result<Shot, String> {
    let range = get_frame_range(&project.config, entity)?.ok_or_else(|| format!("No frame range configured for {entity}"))?;
    let mut takes = playblast_takes(project, entity)?;
    takes.extend(render_takes(project, entity)?);
    let fps = require_fps(&project.config, entity)?;
    Ok(Shot {
        name: entity.last().unwrap_or_default().to_string(),
        sequence: sequence_of(entity),
        fps: u32::try_from(fps).map_err(|_| format!("{entity} has fps {fps}"))?,
        length: range.play_range().len() as u32,
        take: default_take(&takes),
        takes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suffixes_like_python() {
        assert_eq!(split_suffix("a.mp4"), ("a", ".mp4"));
        assert_eq!(split_suffix("a.b.mp4"), ("a.b", ".mp4"));
        assert_eq!(split_suffix(".hidden"), (".hidden", ""));
        assert_eq!(split_suffix("a."), ("a.", ""));
        assert_eq!(split_suffix("beauty"), ("beauty", ""));
    }

    #[test]
    fn versions_of_movies() {
        let stem = "010_sh020_animation";
        assert_eq!(version_of_movie(Path::new("v0003.mp4"), stem).as_deref(), Some("v0003"));
        assert_eq!(version_of_movie(Path::new("010_sh020_animation_v0003.mp4"), stem).as_deref(), Some("v0003"));
        assert_eq!(version_of_movie(Path::new("010_sh030_animation_v0003.mp4"), stem), None);
        assert_eq!(version_of_movie(Path::new("notes.mp4"), stem), None);
    }
}
