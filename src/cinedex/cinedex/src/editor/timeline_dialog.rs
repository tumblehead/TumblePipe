//! File > Export Timeline… (and the agent's `export_timeline`): the cut as
//! a timeline DaVinci Resolve and Adobe Premiere Pro import.
//!
//! The dialog asks for the format (Final Cut Pro 7 XML, which both read,
//! or OpenTimelineIO for Resolve), the file, whether the movies are
//! collected beside it, and for XML which NLE image sequences are written
//! for; then shows what was carried and what was dropped or approximated.
//! `th_edit::xmeml` and `th_edit::otio` make the timeline; here the edit
//! is frozen (every take the version it shows now), the movies are asked
//! where their frames start ([`DiskProbe`]), and the file is written: to a
//! partial file beside it, then renamed, so a failed export leaves no
//! half-written timeline. Nothing in the project is written or changed.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use primer::prelude::*;
use serde_json::{Value, json};
use th_edit::document::{Frame, FrozenEdit, WorkingEdit};
use th_edit::timeline_export::{Movie, Probe, Summary, otio_summary};
use th_edit::xmeml::{self, Sequences};
use th_media::MovieIndex;
use th_media::sound::Sound;
use th_pipe::Project;
use th_pipe::edits::EditSources;

use super::{Dialog, Editor, Message, ProjectPaths};
use crate::export;
use crate::look;
use crate::rpc::{self, Call, Refusal};
use crate::save_dialog;

/// A timeline format.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Format {
    /// Final Cut Pro 7 XML: Resolve and Premiere Pro.
    Xml,
    /// OpenTimelineIO: Resolve.
    Otio,
}

const FORMATS: [Format; 2] = [Format::Xml, Format::Otio];

impl Format {
    pub(crate) fn extension(self) -> &'static str {
        match self {
            Format::Xml => "xml",
            Format::Otio => "otio",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Format::Xml => "Final Cut Pro 7 XML (Resolve, Premiere)",
            Format::Otio => "OpenTimelineIO (Resolve)",
        }
    }

    /// Whether its movies are collected beside the file unless the user
    /// says otherwise: Resolve's OTIO import takes movies of one file name
    /// for one movie, and every playblast is `vNNNN.mp4`.
    pub(crate) fn collects(self) -> bool {
        self == Format::Otio
    }

    /// The format an agent names (`fcp7xml`, `otio`).
    pub(crate) fn named(name: &str) -> Option<Self> {
        match name {
            "fcp7xml" => Some(Format::Xml),
            "otio" => Some(Format::Otio),
            _ => None,
        }
    }
}

/// The Save dialog for a timeline: both formats, the default first.
const TIMELINE: save_dialog::Kind = save_dialog::Kind {
    title: "Export timeline",
    files: "Timelines",
    kinds: &[("Final Cut Pro 7 XML", "xml"), ("OpenTimelineIO", "otio")],
};

/// What an export is asked to write.
#[derive(Debug, Clone)]
pub(crate) struct Request {
    pub paths: ProjectPaths,
    pub edit: WorkingEdit,
    pub output: PathBuf,
    pub format: Format,
    pub collect: bool,
    pub sequences: Sequences,
}

/// A timeline written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Exported {
    pub path: PathBuf,
    pub format: Format,
    /// What was carried, in a line.
    pub carried: String,
    /// What was dropped or approximated, a line each.
    pub notes: Vec<String>,
}

/// The Export Timeline dialog, as it stands.
#[derive(Debug, Clone)]
pub(super) enum TimelineDialog {
    /// `collect`: None until the box is ticked or unticked (the format's
    /// own default then). `choosing`: the Save dialog is open.
    Setup {
        path: String,
        format: Format,
        collect: Option<bool>,
        sequences: Sequences,
        problem: Option<String>,
        choosing: bool,
    },
    Running {
        path: PathBuf,
    },
    Done(Exported),
}

#[derive(Debug, Clone)]
pub enum TimelineMessage {
    Open,
    Path(String),
    Format(usize),
    Collect(bool),
    Sequences(usize),
    Browse,
    Chosen(Result<Option<PathBuf>, String>),
    Start,
    Finished(Result<Exported, String>),
    Reveal,
    Close,
}

fn wrap(m: TimelineMessage) -> Message {
    Message::Timeline(m)
}

/// The file a typed `path` names for `format`: its extension added when
/// it has none. An error when it is empty, has another extension, or its
/// folder is not there.
pub(crate) fn output_path(path: &str, format: Format) -> Result<PathBuf, String> {
    let typed = path.trim().trim_matches('"');
    if typed.is_empty() {
        return Err("Give the file to write".into());
    }
    let ext = format.extension();
    let file = match Path::new(typed).extension() {
        None => PathBuf::from(format!("{typed}.{ext}")),
        Some(e) if e.eq_ignore_ascii_case(ext) => PathBuf::from(typed),
        Some(e) => return Err(format!("{} is written as a .{ext} file, not .{}", format.label(), e.to_string_lossy())),
    };
    match file.parent().filter(|p| !p.as_os_str().is_empty()) {
        Some(folder) if folder.is_dir() => Ok(file),
        Some(folder) => Err(format!("{} is not a folder here", folder.display())),
        None => Err("Give the file's whole path, its folder too".into()),
    }
}

/// `path` with `to`'s extension when it has `from`'s: the format changed.
pub(crate) fn with_format(path: &str, from: Format, to: Format) -> String {
    let typed = Path::new(path);
    match typed.extension() {
        Some(e) if e.eq_ignore_ascii_case(from.extension()) => typed.with_extension(to.extension()).to_string_lossy().into_owned(),
        _ => path.to_owned(),
    }
}

/// The folder an export's movies are collected in: beside the file, named
/// after it (`cut.xml`: `cut_media`).
pub(crate) fn media_folder(output: &Path) -> PathBuf {
    let stem = output.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    output.with_file_name(format!("{stem}_media"))
}

/// The disk and the project, answering an export's questions.
pub(crate) struct DiskProbe<'a> {
    pub project: &'a Project,
}

/// The movie or sound file at `path`: None when it is not there or neither
/// its picture nor its sound can be read. Reads its packets and decodes
/// its sound, so it runs off the UI thread.
pub(crate) fn probe_movie(path: &str) -> Option<Movie> {
    if !Path::new(path).is_file() {
        return None;
    }
    let index = MovieIndex::read(path).ok();
    let sound = Sound::read(path).ok().flatten();
    let seconds = match (&index, &sound) {
        (Some(i), _) => {
            let (num, den) = i.rate();
            i.frame_count() as f64 * f64::from(den) / f64::from(num)
        }
        (None, Some(s)) => s.seconds(),
        (None, None) => return None,
    };
    let list = index.as_ref().and_then(|i| i.frame_ranges());
    Some(Movie {
        start_frame: index.as_ref().and_then(|i| list.and_then(|l| l.frame_number(0)).or(i.start_frame())),
        frames: index.as_ref().map(|i| i.frame_count() as Frame),
        rate: index.as_ref().map(|i| (i64::from(i.rate().0), i64::from(i.rate().1))),
        audio: sound.is_some(),
        seconds,
        gaps: list.is_some_and(|l| l.ranges().len() > 1),
    })
}

impl Probe for DiskProbe<'_> {
    fn movie(&self, path: &str) -> Option<Movie> {
        probe_movie(path)
    }

    fn exists(&self, path: &str) -> bool {
        Path::new(path).is_file()
    }

    /// As the editor counts a take's movie that records no start frame
    /// (`super::resolve`): from the shot's range with its roll, 1001 when
    /// it has none.
    fn shot_first(&self, shot: &str) -> Frame {
        th_pipe::Uri::parse_address(shot)
            .ok()
            .and_then(|uri| th_pipe::timeline::get_frame_range(&self.project.config, &uri).ok().flatten())
            .map_or(1001, |range| range.full_range().first_frame as Frame)
    }
}

/// Hard-link every movie `edit` plays into `folder` under a name of its
/// own (`th_edit::otio::movie_links`): the links made, each movie's path
/// to its link's, and the movies that could not be linked, with why. A
/// movie is never copied (a cut's movies are many gigabytes) and the
/// originals are not touched.
fn collect(edit: &FrozenEdit, folder: &Path) -> Result<(BTreeMap<String, String>, Vec<String>), String> {
    let names = th_edit::otio::movie_links(edit);
    let mut links = BTreeMap::new();
    let mut failed = Vec::new();
    if names.is_empty() {
        return Ok((links, failed));
    }
    std::fs::create_dir_all(folder).map_err(|e| format!("{}: {e}", folder.display()))?;
    for (path, name) in names {
        let link = folder.join(&name);
        // A link from an earlier export of this file is made again, so it
        // is this movie's as it is now; removing a hard link leaves the
        // movie. Never when the movie itself is at that path.
        let itself = same_path(Path::new(&path), &link);
        if link.is_file() && !itself {
            let _ = std::fs::remove_file(&link);
        }
        if itself || std::fs::hard_link(&path, &link).is_ok() {
            links.insert(path, link.to_string_lossy().replace('\\', "/"));
        } else {
            failed.push(path);
        }
    }
    Ok((links, failed))
}

/// Whether two paths name one place on disk.
fn same_path(a: &Path, b: &Path) -> bool {
    matches!((std::fs::canonicalize(a), std::fs::canonicalize(b)), (Ok(a), Ok(b)) if a == b)
}

/// Write `contents` as `output`: to a partial file beside it, renamed
/// once it is whole.
fn write_whole(output: &Path, contents: &[u8]) -> Result<(), String> {
    let name = output.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let partial = output.with_file_name(format!("{name}.partial"));
    let fail = |e: std::io::Error| format!("{}: {e}", output.display());
    std::fs::write(&partial, contents).map_err(fail)?;
    if let Err(e) = std::fs::rename(&partial, output) {
        let _ = std::fs::remove_file(&partial);
        return Err(fail(e));
    }
    Ok(())
}

/// Write the frozen `edit` as `output` in `format`, its movies collected
/// beside it when `collecting`.
pub(crate) fn write(
    edit: &FrozenEdit,
    output: &Path,
    format: Format,
    collecting: bool,
    sequences: Sequences,
    probe: &impl Probe,
) -> Result<Exported, String> {
    let name = output.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let folder = media_folder(output);
    let (links, failed) = if collecting { collect(edit, &folder)? } else { (BTreeMap::new(), Vec::new()) };
    let (contents, summary): (String, Summary) = match format {
        Format::Xml => xmeml::sequence(edit, &name, probe, &xmeml::Options { sequences, links: links.clone() }),
        Format::Otio => {
            let mut otio = th_edit::otio::timeline(edit, &name);
            let summary = otio_summary(edit, &otio, probe);
            th_edit::otio::relink(&mut otio, &links);
            (serde_json::to_string_pretty(&otio).expect("OTIO serializes"), summary)
        }
    };
    write_whole(output, contents.as_bytes())?;
    let mut notes = Vec::new();
    if !links.is_empty() {
        notes.push(format!(
            "{} movie{} collected in {} as hard links under names of their own (no extra disk space; the originals are untouched).",
            links.len(),
            if links.len() == 1 { "" } else { "s" },
            folder.display()
        ));
    }
    if !failed.is_empty() {
        notes.push(format!(
            "{} movie{} could not be hard-linked into {} (another drive, a share without links, or not on disk) and {} referred to where {} (not copied). Movies that share a file name may then conform as one movie in Resolve: export to the drive the media is on.",
            failed.len(),
            if failed.len() == 1 { "" } else { "s" },
            folder.display(),
            if failed.len() == 1 { "is" } else { "are" },
            if failed.len() == 1 { "it is" } else { "they are" },
        ));
    }
    notes.extend(summary.dropped());
    Ok(Exported { path: output.to_path_buf(), format, carried: summary.carried(), notes })
}

/// Freeze `request`'s edit against the project as it is now and write it.
pub(crate) fn export_timeline(request: &Request) -> Result<Exported, String> {
    let project = request.paths.open()?;
    let frozen = th_edit::freeze::freeze(&request.edit, &EditSources::new(&project, request.edit.fps.max(1)))?;
    write(&frozen, &request.output, request.format, request.collect, request.sequences, &DiskProbe { project: &project })
}

/// The dialog holds the window: an export or the Save dialog is under way,
/// so Escape and a click outside do not close it.
pub(super) fn holds(dialog: &Option<Dialog>) -> bool {
    matches!(dialog, Some(Dialog::Timeline(TimelineDialog::Running { .. } | TimelineDialog::Setup { choosing: true, .. })))
}

/// What an agent's `export_timeline` asks for, or why it is refused.
pub(crate) fn agent_request(params: &Value) -> Result<(PathBuf, Format, bool, Sequences), String> {
    let format = match params.get("format").and_then(Value::as_str) {
        None => Format::Xml,
        Some(name) => Format::named(name).ok_or_else(|| format!("format is \"fcp7xml\" or \"otio\", not {name:?}"))?,
    };
    let sequences = match params.get("sequences").and_then(Value::as_str) {
        None | Some("resolve") => Sequences::Resolve,
        Some("premiere") => Sequences::Premiere,
        Some(other) => return Err(format!("sequences is \"resolve\" or \"premiere\", not {other:?}")),
    };
    let collect = params.get("collect_media").and_then(Value::as_bool).unwrap_or(format.collects());
    let output = output_path(params.get("path").and_then(Value::as_str).unwrap_or_default(), format)?;
    Ok((output, format, collect, sequences))
}

/// An export's answer to an agent.
fn exported_json(exported: &Exported) -> Value {
    json!({
        "path": exported.path.to_string_lossy(),
        "format": if exported.format == Format::Xml { "fcp7xml" } else { "otio" },
        "carried": exported.carried,
        "notes": exported.notes,
    })
}

impl Editor {
    fn timeline_request(&self, output: PathBuf, format: Format, collect: bool, sequences: Sequences) -> Request {
        Request { paths: self.paths.clone(), edit: self.doc.edit.clone(), output, format, collect, sequences }
    }

    pub(super) fn timeline_update(&mut self, message: TimelineMessage) -> Command<Message> {
        match message {
            TimelineMessage::Open => {
                if self.dialog.is_none() {
                    let format = Format::Xml;
                    let path = export::default_movie(&self.doc.origin).with_extension(format.extension()).to_string_lossy().into_owned();
                    self.dialog = Some(Dialog::Timeline(TimelineDialog::Setup {
                        path,
                        format,
                        collect: None,
                        sequences: Sequences::Resolve,
                        problem: None,
                        choosing: false,
                    }));
                }
            }
            TimelineMessage::Path(value) => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { path, problem, .. })) = &mut self.dialog {
                    *path = value;
                    *problem = None;
                }
            }
            TimelineMessage::Format(index) => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { path, format, problem, .. })) = &mut self.dialog {
                    let to = FORMATS[index.min(FORMATS.len() - 1)];
                    *path = with_format(path, *format, to);
                    *format = to;
                    *problem = None;
                }
            }
            TimelineMessage::Collect(on) => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { collect, .. })) = &mut self.dialog {
                    *collect = Some(on);
                }
            }
            TimelineMessage::Sequences(index) => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { sequences, .. })) = &mut self.dialog {
                    *sequences = if index == 0 { Sequences::Resolve } else { Sequences::Premiere };
                }
            }
            TimelineMessage::Browse => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { path, format, choosing: choosing @ false, .. })) = &mut self.dialog {
                    *choosing = true;
                    let fallback = export::default_movie(&self.doc.origin).with_extension(format.extension());
                    let offered = output_path(path, *format).unwrap_or(fallback);
                    return Command::perform_blocking(move || save_dialog::save(&offered, &TIMELINE), |r| wrap(TimelineMessage::Chosen(r)));
                }
            }
            TimelineMessage::Chosen(chosen) => {
                if let Some(Dialog::Timeline(TimelineDialog::Setup { path, format, problem, choosing, .. })) = &mut self.dialog {
                    *choosing = false;
                    match chosen {
                        Ok(Some(file)) => {
                            // The kind picked in the Save dialog is the format.
                            if let Some(picked) =
                                FORMATS.into_iter().find(|f| file.extension().is_some_and(|e| e.eq_ignore_ascii_case(f.extension())))
                            {
                                *format = picked;
                            }
                            *path = file.to_string_lossy().into_owned();
                            *problem = None;
                        }
                        Ok(None) => {}
                        Err(e) => *problem = Some(e),
                    }
                }
            }
            TimelineMessage::Start => {
                let Some(Dialog::Timeline(TimelineDialog::Setup { path, format, collect, sequences, choosing: false, .. })) = &self.dialog
                else {
                    return Command::none();
                };
                let (format, collect, sequences) = (*format, collect.unwrap_or(format.collects()), *sequences);
                let checked = if self.doc.edit.length() <= 0 {
                    Err("The edit is empty: there is nothing to export.".to_owned())
                } else {
                    output_path(path, format)
                };
                let output = match checked {
                    Ok(output) => output,
                    Err(why) => {
                        if let Some(Dialog::Timeline(TimelineDialog::Setup { problem, .. })) = &mut self.dialog {
                            *problem = Some(why);
                        }
                        return Command::none();
                    }
                };
                export::remember_folder(&output);
                let request = self.timeline_request(output.clone(), format, collect, sequences);
                self.dialog = Some(Dialog::Timeline(TimelineDialog::Running { path: output }));
                return Command::perform_blocking(move || export_timeline(&request), |r| wrap(TimelineMessage::Finished(r)));
            }
            TimelineMessage::Finished(result) => {
                let shown = matches!(self.dialog, Some(Dialog::Timeline(TimelineDialog::Running { .. })));
                if shown {
                    self.dialog = None;
                }
                match result {
                    Ok(exported) => {
                        self.status = Some((format!("Exported {}", exported.path.display()), false));
                        if shown {
                            self.dialog = Some(Dialog::Timeline(TimelineDialog::Done(exported)));
                        }
                    }
                    Err(e) => self.status = Some((format!("Not exported: {e}"), true)),
                }
            }
            TimelineMessage::Reveal => {
                if let Some(Dialog::Timeline(TimelineDialog::Done(exported))) = &self.dialog {
                    let opened = exported.path.parent().ok_or_else(|| "the file has no folder".to_string()).and_then(crate::reveal::reveal);
                    if let Err(e) = opened {
                        self.status = Some((e, true));
                    }
                }
            }
            TimelineMessage::Close => {
                if !holds(&self.dialog) {
                    self.dialog = None;
                }
            }
        }
        Command::none()
    }

    /// The agent's `export_timeline`: written off the UI thread, answered
    /// when it is.
    pub(super) fn agent_export_timeline(&mut self, call: Call, params: &Value) -> Command<Message> {
        let asked = if self.doc.edit.length() <= 0 {
            Err("the edit is empty: there is nothing to export".to_owned())
        } else {
            agent_request(params)
        };
        let (output, format, collect, sequences) = match asked {
            Ok(asked) => asked,
            Err(why) => {
                call.answer(Err(Refusal::new(rpc::BAD_PARAMS, why)));
                return Command::none();
            }
        };
        let request = self.timeline_request(output, format, collect, sequences);
        Command::perform_blocking(
            move || {
                call.answer(export_timeline(&request).map(|e| exported_json(&e)).map_err(|e| Refusal::new(rpc::REFUSED, e)));
            },
            |()| Message::Wake,
        )
    }
}

/// The dialog's page.
pub(super) fn element(dialog: &TimelineDialog) -> Element<Message> {
    let heading = |w: &str| text(w.to_owned()).font_size(16.0).color(look::text());
    let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
    let choice = |id: &'static str, w: &str, m: TimelineMessage| {
        look::button(Id::new(id), text(w.to_owned()).font_size(13.0)).padding(Padding::from([5.0, 14.0])).on_press(move || wrap(m.clone()))
    };
    let mut page = column().spacing(8.0).padding(16.0).width(Length::fixed(620.0));
    let mut buttons = row().spacing(8.0);
    match dialog {
        TimelineDialog::Setup { path, format, collect, sequences, problem, choosing } => {
            let index = FORMATS.iter().position(|f| f == format).unwrap_or(0);
            let formats = FORMATS
                .iter()
                .fold(segmented(Id::new("timeline format"), index), |s, f| s.push(f.label()))
                .on_select(|i| wrap(TimelineMessage::Format(i)));
            let field = text_input(Id::new("timeline path"), path.clone())
                .placeholder(format!("C:/Users/me/Videos/edit.{}", format.extension()))
                .on_input(|s| wrap(TimelineMessage::Path(s)))
                .on_submit(|| wrap(TimelineMessage::Start))
                .width(Length::Fill);
            let collecting = collect.unwrap_or(format.collects());
            let folder =
                output_path(path, *format).map(|o| media_folder(&o).file_name().unwrap_or_default().to_string_lossy().into_owned());
            let collect_words = format!(
                "Collect media beside the file (hard links in {}: no extra disk space)",
                folder.unwrap_or_else(|_| "<name>_media".to_owned())
            );
            page = page
                .push(heading("Export timeline"))
                .push(label("The cut as it is here, saved or not, every take the version it shows now. Nothing in the project changes."))
                .push(label("Format"))
                .push(formats)
                .push(label(&format!("File (.{})", format.extension())))
                .push(row().spacing(8.0).align_y(Vertical::Center).push(field).push(choice("browse", "Browse…", TimelineMessage::Browse)))
                .push(
                    row()
                        .spacing(8.0)
                        .align_y(Vertical::Center)
                        .push(look::tick_box(Id::new("timeline collect"), collecting, |on| wrap(TimelineMessage::Collect(on))))
                        .push(text(collect_words).font_size(12.0).color(look::text())),
                );
            match format {
                Format::Xml => {
                    let which = usize::from(*sequences == Sequences::Premiere);
                    let nle = segmented(Id::new("timeline sequences"), which)
                        .push("Resolve")
                        .push("Premiere Pro")
                        .on_select(|i| wrap(TimelineMessage::Sequences(i)));
                    page = page.push(
                        row().spacing(8.0).align_y(Vertical::Center).push(label("Image sequences (EXR renders) written for")).push(nle),
                    );
                }
                Format::Otio if !collecting => {
                    page = page.push(
                        text("Resolve takes movies that share a file name for one movie, and every playblast is vNNNN.mp4: collect them.")
                            .font_size(12.0)
                            .color(look::caution()),
                    );
                }
                Format::Otio => {}
            }
            if output_path(path, *format).is_ok_and(|p| p.is_file()) {
                page = page.push(text("A file is there already: the export replaces it.").font_size(12.0).color(look::caution()));
            }
            if let Some(problem) = problem {
                page = page.push(text(problem.clone()).font_size(12.0).color(look::error()));
            }
            if *choosing {
                page = page.push(label("Choose where in the Save dialog…"));
            } else {
                buttons = buttons.push(choice("ok", "Export", TimelineMessage::Start));
            }
            buttons = buttons.push(choice("cancel", "Cancel", TimelineMessage::Close));
        }
        TimelineDialog::Running { path } => {
            page = page
                .push(heading("Exporting timeline…"))
                .push(label(&path.display().to_string()))
                .push(label("Reading where each movie's frames start."));
        }
        TimelineDialog::Done(exported) => {
            page = page
                .push(heading("Timeline exported"))
                .push(label(&exported.path.display().to_string()))
                .push(text(exported.carried.clone()).font_size(12.0).color(look::text()));
            if exported.notes.is_empty() {
                page = page.push(label("Nothing was dropped or approximated."));
            }
            for note in &exported.notes {
                page = page.push(text(note.clone()).font_size(12.0).color(look::caution()));
            }
            let import = match exported.format {
                Format::Xml => "Resolve: File > Import > Timeline. Premiere Pro: File > Import.",
                Format::Otio => "Resolve: File > Import > Timeline.",
            };
            page = page.push(label(import));
            buttons = buttons.push(choice("reveal", "Show in Folder", TimelineMessage::Reveal)).push(choice(
                "ok",
                "Close",
                TimelineMessage::Close,
            ));
        }
    }
    container(page.push(buttons)).surface().border_radius(6.0).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{FrozenSource, FrozenTake, TakeKind, Track, Version, VideoClip};
    use th_playlist::Media;

    /// A disk where every file that is there is a 100-frame movie from
    /// frame 1001, with no sound.
    struct Plain;

    impl Probe for Plain {
        fn movie(&self, path: &str) -> Option<Movie> {
            Path::new(path).is_file().then(|| Movie {
                start_frame: Some(1001),
                frames: Some(100),
                rate: Some((24, 1)),
                audio: false,
                seconds: 100.0 / 24.0,
                gaps: false,
            })
        }
        fn exists(&self, path: &str) -> bool {
            Path::new(path).is_file()
        }
        fn shot_first(&self, _: &str) -> Frame {
            1001
        }
    }

    fn shot(name: &str, path: &Path) -> FrozenSource {
        FrozenSource::Shot {
            shot: format!("entity:/shots/S/{name}"),
            take: Some(FrozenTake {
                department: "anim".into(),
                kind: TakeKind::Playblast,
                layer: None,
                version: Version::try_from("v0001".to_string()).unwrap(),
                media: Media::Movie { path: path.to_string_lossy().replace('\\', "/") },
            }),
        }
    }

    /// `count` shots' playblasts under `root`, each `<shot>/v0001.mp4`
    /// with bytes of its own, and an edit of `clips` (indices into them).
    fn cut(root: &Path, count: usize, clips: &[usize]) -> (FrozenEdit, Vec<PathBuf>) {
        let movies: Vec<PathBuf> = (0..count)
            .map(|i| {
                let folder = root.join(format!("{:03}", (i + 1) * 10));
                std::fs::create_dir_all(&folder).unwrap();
                let movie = folder.join("v0001.mp4");
                std::fs::write(&movie, format!("movie {i}")).unwrap();
                movie
            })
            .collect();
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let clips = clips
            .iter()
            .enumerate()
            .map(|(n, &i)| VideoClip::new(n as Frame * 10, 10, 1001, shot(&format!("{:03}", (i % count + 1) * 10), &movies[i % count])))
            .collect();
        edit.video.push(Track { name: "V1".into(), clips });
        (edit, movies)
    }

    /// Every file a written timeline refers to, in order: an xmeml's
    /// `pathurl`s, an OTIO's `target_url`s.
    fn referred(contents: &str, format: Format) -> Vec<String> {
        let (open, close) = match format {
            Format::Xml => ("<pathurl>", "</pathurl>"),
            Format::Otio => ("\"target_url\": \"", "\""),
        };
        contents.split(open).skip(1).map(|rest| th_edit::timeline_export::url_path(&rest[..rest.find(close).unwrap()]).unwrap()).collect()
    }

    proptest! {
        /// In either format, collected or not: the timeline is written
        /// whole under its name with no partial file left; every clip
        /// refers to a file that is there and holds its own shot's movie;
        /// collected, those files are in `<name>_media` beside the
        /// timeline, each movie under a name no other has, and are the
        /// same files on disk as the originals (links, not copies), which
        /// are untouched; not collected, no folder is made. Exporting
        /// again over the same file gives the same timeline.
        #[test]
        fn a_timeline_is_written_whole_and_refers_to_each_clips_movie(
            count in 1usize..5,
            clips in prop::collection::vec(0usize..5, 1..8),
            otio in any::<bool>(),
            collecting in any::<bool>(),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let (edit, movies) = cut(&dir.path().join("project"), count, &clips);
            let out = dir.path().join("out");
            std::fs::create_dir_all(&out).unwrap();
            let format = if otio { Format::Otio } else { Format::Xml };
            let output = out.join(format!("my cut.{}", format.extension()));
            let exported = write(&edit, &output, format, collecting, Sequences::Resolve, &Plain).unwrap();
            prop_assert_eq!(&exported.path, &output);
            let contents = std::fs::read_to_string(&output).unwrap();
            let left: Vec<String> = std::fs::read_dir(&out).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
            prop_assert!(!left.iter().any(|n| n.ends_with(".partial")), "{:?}", left);
            let folder = media_folder(&output);
            prop_assert_eq!(folder.file_name().unwrap().to_string_lossy(), "my cut_media");
            prop_assert_eq!(folder.is_dir(), collecting);
            let files = referred(&contents, format);
            // xmeml writes a file once; OTIO once per clip.
            let used: std::collections::BTreeSet<usize> = clips.iter().map(|i| i % count).collect();
            prop_assert_eq!(files.len(), if otio { clips.len() } else { used.len() });
            let mut seen = std::collections::BTreeMap::new();
            for file in &files {
                let bytes = std::fs::read_to_string(file).unwrap();
                let index: usize = bytes.strip_prefix("movie ").unwrap().parse().unwrap();
                prop_assert!(used.contains(&index));
                let original = movies[index].to_string_lossy().replace('\\', "/");
                if collecting {
                    prop_assert_eq!(Path::new(file).parent().unwrap(), folder.as_path());
                    // A name of its own, and the same file on disk as the
                    // original: what is written to one reads from the other.
                    let first = *seen.entry(file.to_lowercase()).or_insert(index);
                    prop_assert_eq!(first, index);
                    std::fs::write(&movies[index], "changed").unwrap();
                    prop_assert_eq!(std::fs::read_to_string(file).unwrap(), "changed");
                    std::fs::write(&movies[index], &bytes).unwrap();
                } else {
                    prop_assert_eq!(file, &original);
                }
            }
            if otio {
                // Each clip, in order, plays its own shot's movie.
                let order: Vec<usize> = files.iter().map(|f| std::fs::read_to_string(f).unwrap()["movie ".len()..].parse().unwrap()).collect();
                prop_assert_eq!(order, clips.iter().map(|i| i % count).collect::<Vec<_>>());
            }
            prop_assert_eq!(seen.len(), if collecting { used.len() } else { 0 });
            prop_assert_eq!(exported.notes.iter().filter(|n| n.contains("collected in")).count(), usize::from(collecting));
            for movie in &movies {
                prop_assert!(movie.is_file(), "an original is gone");
            }
            let again = write(&edit, &output, format, collecting, Sequences::Resolve, &Plain).unwrap();
            prop_assert_eq!(again, exported);
            prop_assert_eq!(std::fs::read_to_string(&output).unwrap(), contents);
        }

        /// A cut whose clips are moved, scaled or turned, in either format:
        /// neither timeline carries a transform, and the export says so,
        /// counting the clips (and says nothing of fades or opacities the
        /// cut has none of); a cut as fitted says nothing of transforms.
        /// The XML is the file of the same cut as fitted.
        #[test]
        fn an_export_says_which_clips_lost_their_transform(
            clips in prop::collection::vec((0usize..3, prop::option::of((-10f64..=10.0, 0.01f64..=100.0, -3600f64..=3600.0))), 1..6),
            otio in any::<bool>(),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let (plain, _) = cut(&dir.path().join("project"), 3, &clips.iter().map(|c| c.0).collect::<Vec<_>>());
            let mut edit = plain.clone();
            let mut moved = 0;
            for (clip, (_, transform)) in edit.video[0].clips.iter_mut().zip(&clips) {
                if let Some((x, scale, rotation)) = *transform {
                    clip.transform = th_edit::Transform { x, y: x / 3.0, scale, rotation };
                    moved += usize::from(!clip.transform.is_identity());
                }
            }
            let format = if otio { Format::Otio } else { Format::Xml };
            let output = dir.path().join(format!("cut.{}", format.extension()));
            let as_fitted = write(&plain, &output, format, false, Sequences::Resolve, &Plain).unwrap();
            let fitted_file = std::fs::read_to_string(&output).unwrap();
            prop_assert!(!as_fitted.notes.iter().any(|n| n.contains("moved, scaled or turned")), "{:?}", as_fitted.notes);
            let exported = write(&edit, &output, format, false, Sequences::Resolve, &Plain).unwrap();
            let said: Vec<&String> = exported.notes.iter().filter(|n| n.contains("moved, scaled or turned")).collect();
            if moved == 0 {
                prop_assert!(said.is_empty(), "{:?}", exported.notes);
            } else {
                prop_assert_eq!(said.len(), 1, "{:?}", exported.notes);
                prop_assert!(said[0].starts_with(&format!("{moved} clip")), "{} for {} clips", said[0], moved);
            }
            prop_assert!(!exported.notes.iter().any(|n| n.contains("fade") || n.contains("opacity")), "{:?}", exported.notes);
            prop_assert_eq!(&exported.carried, &as_fitted.carried);
            if !otio {
                prop_assert_eq!(std::fs::read_to_string(&output).unwrap(), fitted_file);
            }
        }

        /// A timeline that cannot take its place (a folder is in the way)
        /// is an error naming the file, and no partial file is left
        /// beside it.
        #[test]
        fn a_timeline_that_cannot_be_written_leaves_no_partial_file(otio in any::<bool>(), collecting in any::<bool>()) {
            let dir = tempfile::tempdir().unwrap();
            let (edit, _) = cut(&dir.path().join("project"), 2, &[0, 1]);
            let out = dir.path().join("out");
            let format = if otio { Format::Otio } else { Format::Xml };
            let output = out.join(format!("cut.{}", format.extension()));
            std::fs::create_dir_all(output.join("in the way")).unwrap();
            let error = write(&edit, &output, format, collecting, Sequences::Resolve, &Plain).unwrap_err();
            prop_assert!(error.contains("cut."), "{}", error);
            let left: Vec<String> = std::fs::read_dir(&out).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
            prop_assert!(!left.iter().any(|n| n.ends_with(".partial")), "{:?}", left);
            prop_assert!(output.join("in the way").is_dir(), "what was there is gone");
        }

        /// A movie that cannot be linked (here: it is not on disk) is
        /// referred to where it is and said so; the timeline is written.
        #[test]
        fn a_movie_that_cannot_be_linked_is_referred_to_where_it_is(count in 2usize..5, gone in 0usize..5, otio in any::<bool>()) {
            let dir = tempfile::tempdir().unwrap();
            let all: Vec<usize> = (0..count).collect();
            let (edit, movies) = cut(&dir.path().join("project"), count, &all);
            let gone = gone % count;
            std::fs::remove_file(&movies[gone]).unwrap();
            let format = if otio { Format::Otio } else { Format::Xml };
            let output = dir.path().join(format!("cut.{}", format.extension()));
            let exported = write(&edit, &output, format, true, Sequences::Resolve, &Plain).unwrap();
            let files = referred(&std::fs::read_to_string(&output).unwrap(), format);
            prop_assert_eq!(files.len(), count);
            let missing = movies[gone].to_string_lossy().replace('\\', "/");
            prop_assert_eq!(files.iter().filter(|f| **f == missing).count(), 1);
            prop_assert_eq!(files.iter().filter(|f| Path::new(f).is_file()).count(), count - 1);
            prop_assert!(exported.notes.iter().any(|n| n.starts_with("1 movie could not be hard-linked")), "{:?}", exported.notes);
            prop_assert!(exported.notes.iter().any(|n| n.contains("not on disk")), "{:?}", exported.notes);
        }

        /// A typed path is a file of the format in a folder that is
        /// there: one without an extension gains the format's, another
        /// format's is refused, as is no folder or one that is not there.
        #[test]
        fn a_typed_path_is_a_file_of_the_format(stem in "[a-z]{1,8}( [a-z]{1,4})?", kind in 0usize..5, otio in any::<bool>(), quoted in any::<bool>()) {
            let dir = tempfile::tempdir().unwrap();
            let folder = dir.path().to_string_lossy().replace('\\', "/");
            let format = if otio { Format::Otio } else { Format::Xml };
            let (ext, other) = (format.extension(), if otio { "xml" } else { "otio" });
            let typed = match kind {
                0 => format!("{folder}/{stem}.{ext}"),
                1 => format!("{folder}/{stem}"),
                2 => format!("{folder}/{stem}.{other}"),
                3 => format!("{folder}/missing/{stem}.{ext}"),
                _ => format!("{stem}.{ext}"),
            };
            let typed = if quoted { format!(" \"{typed}\" ") } else { typed };
            let got = output_path(&typed, format);
            if kind <= 1 {
                prop_assert_eq!(got.unwrap(), PathBuf::from(format!("{folder}/{stem}.{ext}")));
            } else {
                prop_assert!(got.is_err(), "{:?}", got);
            }
            prop_assert!(output_path("  ", format).is_err());
            // Changing the format changes the extension, and back.
            let named = format!("{folder}/{stem}.{ext}");
            let to = if otio { Format::Xml } else { Format::Otio };
            let changed = with_format(&named, format, to);
            prop_assert_eq!(&changed, &format!("{folder}/{stem}.{other}"));
            prop_assert_eq!(with_format(&changed, to, format), named);
            prop_assert_eq!(with_format(&format!("{folder}/{stem}"), format, to), format!("{folder}/{stem}"));
        }

        /// An agent's request: XML unless it names OTIO, movies collected
        /// by the format's default unless it says, sequences for Resolve
        /// unless it says Premiere; anything else it names is refused.
        #[test]
        fn an_agents_request_reads_as_the_dialog_would(format in prop::option::of(0usize..3), collect in prop::option::of(any::<bool>()), sequences in prop::option::of(0usize..3)) {
            let dir = tempfile::tempdir().unwrap();
            let mut params = json!({"path": format!("{}/cut", dir.path().to_string_lossy().replace('\\', "/"))});
            if let Some(f) = format { params["format"] = json!(["fcp7xml", "otio", "edl"][f]); }
            if let Some(c) = collect { params["collect_media"] = json!(c); }
            if let Some(s) = sequences { params["sequences"] = json!(["resolve", "premiere", "avid"][s]); }
            let got = agent_request(&params);
            if format == Some(2) || sequences == Some(2) {
                prop_assert!(got.is_err());
            } else {
                let (output, got_format, got_collect, got_sequences) = got.unwrap();
                let want = if format == Some(1) { Format::Otio } else { Format::Xml };
                prop_assert_eq!(got_format, want);
                prop_assert_eq!(output.extension().unwrap().to_string_lossy(), want.extension());
                prop_assert_eq!(got_collect, collect.unwrap_or(want == Format::Otio));
                prop_assert_eq!(got_sequences, if sequences == Some(1) { Sequences::Premiere } else { Sequences::Resolve });
            }
            let no_path = agent_request(&Value::Object(Default::default()));
            prop_assert!(no_path.is_err());
        }
    }

    /// Escape and a click outside leave a running export (and an open Save
    /// dialog) be.
    #[test]
    fn a_running_export_holds_the_window() {
        let setup = |choosing| {
            Some(Dialog::Timeline(TimelineDialog::Setup {
                path: String::new(),
                format: Format::Xml,
                collect: None,
                sequences: Sequences::Resolve,
                problem: None,
                choosing,
            }))
        };
        assert!(holds(&Some(Dialog::Timeline(TimelineDialog::Running { path: "a.xml".into() }))));
        assert!(holds(&setup(true)));
        assert!(!holds(&setup(false)) && !holds(&None));
        assert!(Format::Otio.collects() && !Format::Xml.collects());
    }
}
