//! File > Export movie…'s dialog (#61): where the movie goes (typed, or
//! chosen in the system's Save dialog with Browse…), what it will be, then
//! the export's progress with Cancel, then Play and Show in folder.
//! `crate::export` does the export, on a thread of its own.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use primer::prelude::*;
use th_pipe::movie;

use super::{Dialog, Editor, Message, waker};
use crate::export::{self, Exported, Progress, Request, Shared};
use crate::look;
use crate::save_dialog;

/// The Export movie dialog, as it stands.
#[derive(Debug, Clone)]
pub(super) enum ExportDialog {
    /// Choosing where the movie goes. `choosing`: the Save dialog is open.
    Setup { path: String, summary: String, hffmpeg: Result<PathBuf, String>, problem: Option<String>, choosing: bool },
    /// Exporting into `path`.
    Running { path: PathBuf, progress: Shared },
    /// Written.
    Done(Exported),
}

/// What the user (and the export) does in the dialog.
#[derive(Debug, Clone)]
pub enum ExportMessage {
    Open,
    Path(String),
    Browse,
    Chosen(Result<Option<PathBuf>, String>),
    Start,
    Cancel,
    Finished(Result<Option<Exported>, String>),
    Play,
    Reveal,
    Close,
}

fn wrap(m: ExportMessage) -> Message {
    Message::Export(m)
}

/// The hffmpeg to export with, or why there is none.
fn hffmpeg() -> Result<PathBuf, String> {
    movie::find_hffmpeg(|v| std::env::var(v).ok(), movie::houdini_bins, Path::is_file).ok_or_else(|| {
        format!(
            "No Houdini hffmpeg found to encode with: start the editor from Houdini, install Houdini, or set {} to an FFmpeg with openh264.",
            movie::HFFMPEG_OVERRIDE
        )
    })
}

/// Why a movie cannot be written at `path` now; None when it can.
pub(super) fn start_problem(path: &str, hffmpeg: &Result<PathBuf, String>, frames: i64) -> Result<PathBuf, String> {
    if frames <= 0 {
        return Err("The edit is empty: there is nothing to export.".into());
    }
    if let Err(why) = hffmpeg {
        return Err(why.clone());
    }
    let movie = movie::movie_path(path)?;
    match movie.parent().filter(|p| !p.as_os_str().is_empty()) {
        Some(folder) if folder.is_dir() => Ok(movie),
        Some(folder) => Err(format!("{} is not a folder here", folder.display())),
        None => Err("Give the movie's whole path, its folder too".into()),
    }
}

/// The dialog holds the window: an export or the Save dialog is under way,
/// so Escape and a click outside do not close it.
pub(super) fn holds(dialog: &Option<Dialog>) -> bool {
    matches!(dialog, Some(Dialog::Export(ExportDialog::Running { .. } | ExportDialog::Setup { choosing: true, .. })))
}

/// Ask an export under way to stop. True when there was one.
pub(super) fn cancel(dialog: &Option<Dialog>) -> bool {
    if let Some(Dialog::Export(ExportDialog::Running { progress, .. })) = dialog {
        progress.cancel();
        return true;
    }
    false
}

/// Open `file` in the system's player.
fn play(file: &Path) -> Result<(), String> {
    let program = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    let native = if cfg!(windows) { file.to_string_lossy().replace('/', "\\").into() } else { file.as_os_str().to_owned() };
    std::process::Command::new(program).arg(native).spawn().map(|_| ()).map_err(|e| format!("{program}: {e}"))
}

impl Editor {
    pub(super) fn export_update(&mut self, message: ExportMessage) -> Command<Message> {
        match message {
            ExportMessage::Open => {
                if self.dialog.is_none() {
                    let path = export::default_movie(&self.doc.origin).to_string_lossy().into_owned();
                    let summary = export::summary(&self.doc.edit, &self.display.display, &self.display.view);
                    self.dialog =
                        Some(Dialog::Export(ExportDialog::Setup { path, summary, hffmpeg: hffmpeg(), problem: None, choosing: false }));
                }
            }
            ExportMessage::Path(value) => {
                if let Some(Dialog::Export(ExportDialog::Setup { path, problem, .. })) = &mut self.dialog {
                    *path = value;
                    *problem = None;
                }
            }
            ExportMessage::Browse => {
                if let Some(Dialog::Export(ExportDialog::Setup { path, choosing: choosing @ false, .. })) = &mut self.dialog {
                    *choosing = true;
                    let offered = movie::movie_path(path).unwrap_or_else(|_| export::default_movie(&self.doc.origin));
                    return Command::perform_blocking(move || save_dialog::save_movie(&offered), |r| wrap(ExportMessage::Chosen(r)));
                }
            }
            ExportMessage::Chosen(chosen) => {
                if let Some(Dialog::Export(ExportDialog::Setup { path, problem, choosing, .. })) = &mut self.dialog {
                    *choosing = false;
                    match chosen {
                        Ok(Some(movie)) => {
                            *path = movie.to_string_lossy().into_owned();
                            *problem = None;
                        }
                        Ok(None) => {}
                        Err(e) => *problem = Some(e),
                    }
                }
            }
            ExportMessage::Start => {
                let Some(Dialog::Export(ExportDialog::Setup { path, hffmpeg, choosing: false, .. })) = &self.dialog else {
                    return Command::none();
                };
                let output = match start_problem(path, hffmpeg, self.doc.edit.length()) {
                    Ok(output) => output,
                    Err(why) => {
                        if let Some(Dialog::Export(ExportDialog::Setup { problem, .. })) = &mut self.dialog {
                            *problem = Some(why);
                        }
                        return Command::none();
                    }
                };
                let request = Request {
                    paths: self.paths.clone(),
                    ocio: self.ocio.clone(),
                    edit: self.doc.edit.clone(),
                    display: self.display.display.clone(),
                    view: self.display.view.clone(),
                    hffmpeg: hffmpeg.clone().expect("checked above"),
                    output: output.clone(),
                };
                export::remember_folder(&output);
                self.set_playing(false);
                let progress = Arc::new(Progress::default());
                let shared = Arc::clone(&progress);
                let wake = waker(&self.wake_slot);
                self.dialog = Some(Dialog::Export(ExportDialog::Running { path: output, progress }));
                return Command::perform_blocking(move || export::export(&request, &shared, wake), |r| wrap(ExportMessage::Finished(r)));
            }
            ExportMessage::Cancel => {
                if !cancel(&self.dialog) && !holds(&self.dialog) {
                    self.dialog = None;
                }
            }
            ExportMessage::Finished(result) => {
                let shown = matches!(self.dialog, Some(Dialog::Export(ExportDialog::Running { .. })));
                if shown {
                    self.dialog = None;
                }
                match result {
                    Ok(Some(exported)) => {
                        self.status = Some((format!("Exported {}", exported.path.display()), false));
                        if shown {
                            self.dialog = Some(Dialog::Export(ExportDialog::Done(exported)));
                        }
                    }
                    Ok(None) => self.status = Some(("Export cancelled: nothing written".into(), false)),
                    Err(e) => self.status = Some((format!("Not exported: {e}"), true)),
                }
            }
            ExportMessage::Play | ExportMessage::Reveal => {
                if let Some(Dialog::Export(ExportDialog::Done(exported))) = &self.dialog {
                    let opened = if matches!(message, ExportMessage::Play) {
                        play(&exported.path)
                    } else {
                        exported.path.parent().ok_or_else(|| "the movie has no folder".to_string()).and_then(crate::reveal::reveal)
                    };
                    if let Err(e) = opened {
                        self.status = Some((e, true));
                    }
                }
            }
            ExportMessage::Close => {
                if !holds(&self.dialog) {
                    self.dialog = None;
                }
            }
        }
        Command::none()
    }
}

/// The dialog's page.
pub(super) fn element(dialog: &ExportDialog) -> Element<Message> {
    let heading = |w: &str| text(w.to_owned()).font_size(16.0).color(look::text());
    let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
    let choice = |id: &'static str, w: &str, m: ExportMessage| {
        look::button(Id::new(id), text(w.to_owned()).font_size(13.0)).padding(Padding::from([5.0, 14.0])).on_press(move || wrap(m.clone()))
    };
    let mut page = column().spacing(8.0).padding(16.0).width(Length::fixed(560.0));
    let mut buttons = row().spacing(8.0);
    match dialog {
        ExportDialog::Setup { path, summary, hffmpeg, problem, choosing } => {
            page = page.push(heading("Export movie")).push(label("The edit as it plays here, saved or not."));
            let field = text_input(Id::new("export path"), path.clone())
                .placeholder("C:/Users/me/Videos/edit.mp4".to_owned())
                .on_input(|s| wrap(ExportMessage::Path(s)))
                .on_submit(|| wrap(ExportMessage::Start))
                .width(Length::Fill);
            page = page
                .push(label("Movie file (.mp4 or .mov)"))
                .push(row().spacing(8.0).align_y(Vertical::Center).push(field).push(choice("browse", "Browse…", ExportMessage::Browse)))
                .push(text(summary.clone()).font_size(12.0).color(look::text()));
            if movie::movie_path(path).is_ok_and(|p| p.is_file()) {
                page = page.push(text("A file is there already: the export replaces it.").font_size(12.0).color(look::caution()));
            }
            if let Err(why) = hffmpeg {
                page = page.push(text(why.clone()).font_size(12.0).color(look::error()));
            }
            if let Some(problem) = problem {
                page = page.push(text(problem.clone()).font_size(12.0).color(look::error()));
            }
            if *choosing {
                page = page.push(label("Choose where in the Save dialog…"));
            } else {
                buttons = buttons.push(choice("ok", "Export", ExportMessage::Start));
            }
            buttons = buttons.push(choice("cancel", "Cancel", ExportMessage::Close));
        }
        ExportDialog::Running { path, progress } => {
            page = page
                .push(heading("Exporting movie"))
                .push(label(&path.display().to_string()))
                .push(
                    container(progress_bar(progress.fraction()).width(Length::Fill))
                        .width(Length::Fill)
                        .border(1.0, look::quiet())
                        .border_radius(4.0),
                )
                .push(text(export::progress_line(progress)).font_size(12.0).color(look::text()));
            let words = if progress.cancelled() { "Cancelling…" } else { "Cancel" };
            buttons = buttons.push(choice("cancel", words, ExportMessage::Cancel));
        }
        ExportDialog::Done(e) => {
            let sound = if e.audio { ", with sound" } else { ", no sound" };
            page = page.push(heading("Movie exported")).push(label(&e.path.display().to_string())).push(
                text(format!(
                    "{}x{}, {} fps, {} frames ({}){sound}",
                    e.width,
                    e.height,
                    e.fps,
                    e.frames,
                    export::duration(e.frames, e.fps)
                ))
                .font_size(12.0)
                .color(look::text()),
            );
            buttons = buttons
                .push(choice("play", "Play", ExportMessage::Play))
                .push(choice("reveal", "Show in Folder", ExportMessage::Reveal))
                .push(choice("ok", "Close", ExportMessage::Close));
        }
    }
    container(page.push(buttons)).surface().border_radius(6.0).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Export renders the edit document, the cut with its own takes, as
    /// Render Edit does: never what the window only shows (View > Show).
    #[test]
    fn export_renders_the_edit_document() {
        let source = include_str!("export_dialog.rs");
        let request = source.split("let request = Request {").nth(1).and_then(|rest| rest.split("};").next()).unwrap_or_default();
        assert!(request.contains(concat!("edit: self.doc", ".edit.clone(),")), "{request}");
    }

    proptest! {
        /// Export starts only with an encoder, a movie path in a folder
        /// that is there, and something to export; else it says which.
        #[test]
        fn export_starts_only_when_it_can(frames in -2i64..3, has_encoder in any::<bool>(), kind in 0usize..4) {
            let dir = tempfile::tempdir().unwrap();
            let folder = dir.path().to_string_lossy().replace('\\', "/");
            let path = match kind {
                0 => format!("{folder}/cut.mp4"),
                1 => format!("{folder}/missing/cut.mp4"),
                2 => format!("{folder}/cut.png"),
                _ => String::new(),
            };
            let encoder = if has_encoder { Ok(PathBuf::from("hffmpeg")) } else { Err("no hffmpeg".to_owned()) };
            let got = start_problem(&path, &encoder, frames);
            if frames > 0 && has_encoder && kind == 0 {
                prop_assert_eq!(got.unwrap(), PathBuf::from(&path));
            } else {
                let why = got.unwrap_err();
                if frames <= 0 { prop_assert!(why.contains("empty"), "{}", why); }
                else if !has_encoder { prop_assert_eq!(why, "no hffmpeg"); }
            }
        }
    }

    /// Escape and a click outside leave a running export (and an open Save
    /// dialog) be, and Cancel reaches the running export.
    #[test]
    fn a_running_export_holds_the_window() {
        let progress: Shared = Arc::new(Progress::default());
        let running = Some(Dialog::Export(ExportDialog::Running { path: "a.mp4".into(), progress: Arc::clone(&progress) }));
        assert!(holds(&running));
        assert!(cancel(&running) && progress.cancelled());
        let setup = |choosing| {
            Some(Dialog::Export(ExportDialog::Setup {
                path: String::new(),
                summary: String::new(),
                hffmpeg: Ok(PathBuf::new()),
                problem: None,
                choosing,
            }))
        };
        assert!(holds(&setup(true)));
        assert!(!holds(&setup(false)) && !cancel(&setup(false)));
        assert!(!holds(&None) && !cancel(&None));
    }
}
