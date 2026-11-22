//! The document the editor opens: an edit by name, the asked-for shots,
//! or the project's shots; and what an edit's format and listing are.

use std::path::Path;

use th_edit::document::WorkingEdit;
use th_pipe::{Project, edit_files};

use crate::editing::{Document, Origin};
use crate::rpc::Refusal;
use crate::{editing, rpc};

use super::ProjectPaths;
use super::file_dialogs::EditListing;

/// The rate and size a new edit takes: `shot`'s, else the project's
/// (24 fps, 1920x1080 when it says none).
fn edit_format(project: &Project, shot: Option<&th_pipe::Uri>) -> Result<(u32, [u32; 2]), String> {
    let root = th_pipe::Uri::parse("entity:/shots").map_err(|e| e.to_string())?;
    let entity = shot.unwrap_or(&root);
    let fps = th_pipe::timeline::get_fps(&project.config, entity)?.and_then(|f| u32::try_from(f).ok()).filter(|&f| f > 0).unwrap_or(24);
    let properties = project.config.get_inherited_properties(entity).map_err(|e| e.to_string())?.unwrap_or_default();
    let resolution = properties
        .get("render")
        .and_then(|r| r.get("resolution"))
        .and_then(|r| r.as_array())
        .and_then(|r| Some([r.first()?.as_u64()? as u32, r.get(1)?.as_u64()? as u32]))
        .filter(|r| r[0] > 0 && r[1] > 0)
        .unwrap_or([1920, 1080]);
    Ok((fps, resolution))
}

/// The document the arguments ask for, and an autosave newer than it:
/// `edit` (`entity:/edits/<name>`, its newest working version; with
/// `@vNNNN` that published version, read-only), else `shots` end to end in
/// an untitled edit. An edit with nothing saved yet, and an untitled edit
/// with no `shots`, open as every shot of the project ([`project_fill`]).
/// The arguments that open `edit` (`entity:/edits/<name>[@vNNNN]`) of the
/// project at `root`, shown through `ocio`.
pub fn open_arguments(root: &Path, ocio: &str, edit: &str) -> Vec<String> {
    vec!["--project".into(), root.to_string_lossy().into_owned(), "--ocio".into(), ocio.into(), "--edit".into(), edit.into()]
}

/// The project's edits by name, each with its newest working and published
/// versions.
pub(super) fn edit_listings(project: &Project) -> Result<Vec<EditListing>, String> {
    let mut out = Vec::new();
    for name in edit_files::list_edits(project)? {
        let working = edit_files::working_versions(project, &name)?.pop();
        let published = edit_files::published_versions(project, &name)?.pop();
        out.push(EditListing { name, working, published });
    }
    Ok(out)
}

/// [`editing::project_fill`] of `project`, at the project's format.
fn project_fill(project: &Project) -> Result<WorkingEdit, String> {
    let (fps, resolution) = edit_format(project, None)?;
    Ok(editing::project_fill(&editing::project_shots(project)?, fps, resolution))
}

pub fn open_document(paths: &ProjectPaths, edit: Option<&str>, shots: &[String]) -> Result<(Document, Option<WorkingEdit>), String> {
    let project = paths.open()?;
    if let Some(edit) = edit {
        let (uri, published) = match edit.rsplit_once('@') {
            Some((uri, version)) => (uri, Some(version.to_owned())),
            None => (edit, None),
        };
        let name = edit_files::edit_name(&th_pipe::Uri::parse_address(uri).map_err(|e| e.to_string())?)?;
        if !edit_files::list_edits(&project)?.contains(&name) {
            return Err(format!("there is no edit {name}"));
        }
        if let Some(version) = published {
            let frozen = edit_files::load_published(&project, &name, &version)?;
            return Ok((Document::new(th_edit::freeze::thaw(&frozen), Origin::Published { name, version }), None));
        }
        let (working, version) = match edit_files::working_versions(&project, &name)?.pop() {
            Some(last) => (edit_files::load_working(&project, &name, &last)?, Some(last)),
            None => {
                // Never saved: it starts as the whole project.
                let working = project_fill(&project)?;
                let recovered = edit_files::recoverable(&project, &name)?.filter(|r| *r != working);
                return Ok((Document::filled(working, Origin::Working { name, version: None }), recovered));
            }
        };
        let recovered = edit_files::recoverable(&project, &name)?.filter(|r| *r != working);
        return Ok((Document::new(working, Origin::Working { name, version }), recovered));
    }
    if shots.is_empty() {
        return Ok((Document::filled(project_fill(&project)?, Origin::Untitled), None));
    }
    let mut ranges = Vec::new();
    for shot in shots {
        let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
        let range = th_pipe::timeline::get_frame_range(&project.config, &uri)?.ok_or_else(|| format!("{shot} has no frame range"))?;
        let play = range.play_range();
        ranges.push((uri.to_string(), play.first_frame, play.last_frame));
    }
    let first = shots.first().map(|s| th_pipe::Uri::parse_address(s)).transpose().map_err(|e| e.to_string())?;
    let (fps, resolution) = edit_format(&project, first.as_ref())?;
    Ok((Document::new(editing::of_shots(&ranges, fps, resolution), Origin::Untitled), None))
}

/// Why a window on edit `open` (None: untitled) is not the one to show a
/// Live view, the project's master edit being `master`; None when it is.
/// Only a window on the master edit shows it: any other is NOT_HERE (ask
/// another, or start one), and a master edit that cannot be read is a
/// refusal (starting a player would not read it either).
pub(super) fn master_refusal(open: Option<&str>, master: Result<Option<String>, String>) -> Option<Refusal> {
    match master {
        Err(e) => Some(Refusal::new(rpc::REFUSED, e)),
        Ok(None) => Some(Refusal::new(rpc::NOT_HERE, "the project has no master edit")),
        Ok(Some(wanted)) if open == Some(wanted.as_str()) => None,
        Ok(Some(wanted)) => Some(Refusal::new(rpc::NOT_HERE, format!("this window is not on the master edit ({wanted})"))),
    }
}
