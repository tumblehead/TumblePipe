//! File > Export movie…'s dialog (#61): where the movie goes (typed, or
//! chosen in the system's Save dialog with Browse…), what it will be (its
//! size, which frames, the codec, the sound, burn-ins: `export_settings`,
//! kept for this user between sessions), then the export's progress with
//! Cancel, then Play and Show in folder. `crate::export` does the export,
//! on a thread of its own. The agent's `export_movie` asks for the same.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use primer::prelude::*;
use serde_json::{Value, json};
use th_edit::document::WorkingEdit;
use th_pipe::movie::{self, Codec, Quality};

use super::file_dialogs::Dialog;
use super::{Editor, Message, waker};
use crate::export::{self, Exported, Progress, Request, Shared};
use crate::export_settings::{self, Form, Plan, Settings, SizeKind, SpanKind, Spans};
use crate::look;
use crate::rpc::{self, Call, Refusal};
use crate::save_dialog;

/// The Export movie dialog, as it stands.
#[derive(Debug, Clone)]
pub(super) enum ExportDialog {
    /// Choosing where the movie goes. `choosing`: the Save dialog is open.
    /// `form` is what is asked for of the cut's `spans`; `summary` what
    /// that comes to, or why it cannot be made.
    Setup {
        path: String,
        /// Boxed: far larger than the dialog's other states.
        form: Box<Form>,
        spans: Spans,
        summary: Result<String, String>,
        hffmpeg: Result<PathBuf, String>,
        problem: Option<String>,
        choosing: bool,
    },
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
    /// The size, by its place in `SizeKind::ALL`.
    Size(usize),
    Width(String),
    Height(String),
    /// The range, by its place in the kinds the cut offers.
    Span(usize),
    First(String),
    Last(String),
    /// By their places in `Codec::ALL` and `Quality::ALL`.
    Codec(usize),
    Quality(usize),
    Audio(bool),
    Burnins(bool),
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
pub(super) fn start_problem(path: &str, hffmpeg: &Result<PathBuf, String>, frames: i64, codec: Codec) -> Result<PathBuf, String> {
    if frames <= 0 {
        return Err("The edit is empty: there is nothing to export.".into());
    }
    if let Err(why) = hffmpeg {
        return Err(why.clone());
    }
    let movie = movie::movie_path(path)?;
    if movie::fitting_extension(&movie, codec) != movie {
        return Err(format!("{} goes in a .{} file", codec.label(), codec.extensions().join(" or .")));
    }
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

/// What `form` comes to for `edit`, in a line; or why it cannot be made.
pub(super) fn described(form: &Form, edit: &WorkingEdit, spans: &Spans, display: &str, view: &str) -> Result<String, String> {
    let plan = form.settings()?.plan(edit.resolution, spans)?;
    Ok(export::summary(edit, display, view, &plan))
}

/// `path` as typed, its extension changed to one `codec` goes in when it
/// names a movie of another; left as typed when it names none yet.
pub(super) fn path_for(path: &str, codec: Codec) -> String {
    match movie::movie_path(path).map(|movie| (movie::fitting_extension(&movie, codec), movie)) {
        Ok((fitting, movie)) if fitting != movie => fitting.to_string_lossy().into_owned(),
        _ => path.to_owned(),
    }
}

/// An export's answer to an agent.
fn exported_json(exported: &Exported) -> Value {
    json!({
        "path": exported.path.to_string_lossy(),
        "frames": exported.frames,
        "width": exported.width,
        "height": exported.height,
        "fps": exported.fps,
        "audio": exported.audio,
    })
}

impl Editor {
    /// The spans of the cut an export can be of: all of it, its selected
    /// clips' extent, the range that loops.
    pub(super) fn export_spans(&self) -> Spans {
        let edit = &self.doc.edit;
        let selected = self.selection.iter().filter_map(|clip| edit.spans(clip.track).get(clip.index).copied());
        Spans { length: edit.length().max(0) as usize, selection: export_settings::extent(selected), looped: self.loop_range() }
    }

    /// This user's export settings file; None when prefs are not kept.
    fn export_prefs(&self) -> Option<PathBuf> {
        self.layout_path.as_deref().map(export_settings::path)
    }

    /// The export of `output` that `plan` asks for.
    fn export_request(&self, output: PathBuf, hffmpeg: PathBuf, plan: Plan) -> Request {
        Request {
            paths: self.paths.clone(),
            ocio: self.ocio.clone(),
            edit: self.doc.edit.clone(),
            display: self.display.display.clone(),
            view: self.display.view.clone(),
            hffmpeg,
            output,
            plan,
        }
    }

    /// The agent's `export_movie`: File > Export Movie… with the same
    /// settings, each one not given as the dialog first offers it (the
    /// whole cut at the edit's size, standard H.264, with sound, clean).
    /// Encoded off the UI thread; answered when the movie is written.
    pub(super) fn agent_export_movie(&mut self, call: Call, params: &Value) -> Command<Message> {
        let edit = &self.doc.edit;
        let asked = Settings::parse(params, edit.resolution).and_then(|settings| {
            let plan = settings.plan(edit.resolution, &self.export_spans())?;
            let path = params.get("path").and_then(Value::as_str).unwrap_or_default();
            let output = start_problem(path, &hffmpeg(), edit.length(), plan.codec)?;
            Ok((output, plan))
        });
        let (output, plan) = match asked {
            Ok(asked) => asked,
            Err(why) => {
                call.answer(Err(Refusal::new(rpc::BAD_PARAMS, why)));
                return Command::none();
            }
        };
        let request = self.export_request(output, hffmpeg().expect("checked above"), plan);
        Command::perform_blocking(
            move || {
                let exported = export::export(&request, &Progress::default(), || {}).and_then(|e| e.ok_or_else(|| "cancelled".to_owned()));
                call.answer(exported.map(|e| exported_json(&e)).map_err(|e| Refusal::new(rpc::REFUSED, e)));
            },
            |()| Message::Wake,
        )
    }

    /// Say again what the dialog's form comes to.
    fn export_describe(&mut self) {
        let (display, view) = (self.display.display.clone(), self.display.view.clone());
        if let Some(Dialog::Export(ExportDialog::Setup { form, spans, summary, problem, .. })) = &mut self.dialog {
            *summary = described(form, &self.doc.edit, spans, &display, &view);
            *problem = None;
        }
    }

    pub(super) fn export_update(&mut self, message: ExportMessage) -> Command<Message> {
        let resolution = self.doc.edit.resolution;
        match message {
            ExportMessage::Open => {
                if self.dialog.is_none() {
                    let spans = self.export_spans();
                    let kept = self.export_prefs().map(|path| export_settings::load(&path, resolution)).unwrap_or_default();
                    let form = Box::new(Form::new(&kept, resolution, &spans));
                    let path = path_for(&export::default_movie(&self.doc.origin).to_string_lossy(), form.codec);
                    let summary = Ok(String::new());
                    self.dialog = Some(Dialog::Export(ExportDialog::Setup {
                        path,
                        form,
                        spans,
                        summary,
                        hffmpeg: hffmpeg(),
                        problem: None,
                        choosing: false,
                    }));
                    self.export_describe();
                }
            }
            ExportMessage::Path(value) => {
                if let Some(Dialog::Export(ExportDialog::Setup { path, problem, .. })) = &mut self.dialog {
                    *path = value;
                    *problem = None;
                }
            }
            ExportMessage::Size(_)
            | ExportMessage::Width(_)
            | ExportMessage::Height(_)
            | ExportMessage::Span(_)
            | ExportMessage::First(_)
            | ExportMessage::Last(_)
            | ExportMessage::Codec(_)
            | ExportMessage::Quality(_)
            | ExportMessage::Audio(_)
            | ExportMessage::Burnins(_) => {
                if let Some(Dialog::Export(ExportDialog::Setup { form, spans, path, .. })) = &mut self.dialog {
                    match message {
                        ExportMessage::Size(i) => form.size = SizeKind::ALL.get(i).copied().unwrap_or(form.size),
                        ExportMessage::Width(typed) => form.type_width(typed, resolution),
                        ExportMessage::Height(typed) => form.type_height(typed, resolution),
                        ExportMessage::Span(i) => form.span = spans.kinds().get(i).copied().unwrap_or(form.span),
                        ExportMessage::First(typed) => form.first = typed,
                        ExportMessage::Last(typed) => form.last = typed,
                        ExportMessage::Codec(i) => {
                            form.codec = Codec::ALL.get(i).copied().unwrap_or(form.codec);
                            *path = path_for(path, form.codec);
                        }
                        ExportMessage::Quality(i) => form.quality = Quality::ALL.get(i).copied().unwrap_or(form.quality),
                        ExportMessage::Audio(on) => form.audio = on,
                        ExportMessage::Burnins(on) => form.burnins = on,
                        _ => {}
                    }
                }
                self.export_describe();
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
                let Some(Dialog::Export(ExportDialog::Setup { path, form, spans, hffmpeg, choosing: false, .. })) = &self.dialog else {
                    return Command::none();
                };
                let asked = form.settings().and_then(|settings| {
                    let plan = settings.plan(resolution, spans)?;
                    let output = start_problem(path, hffmpeg, self.doc.edit.length(), plan.codec)?;
                    Ok((settings, plan, output))
                });
                let (settings, plan, output) = match asked {
                    Ok(asked) => asked,
                    Err(why) => {
                        if let Some(Dialog::Export(ExportDialog::Setup { problem, .. })) = &mut self.dialog {
                            *problem = Some(why);
                        }
                        return Command::none();
                    }
                };
                let request = self.export_request(output.clone(), hffmpeg.clone().expect("checked above"), plan);
                // Kept for the next export, this session's or a later one's.
                if let Some(prefs) = self.export_prefs()
                    && let Err(e) = export_settings::save(&prefs, &settings)
                {
                    eprintln!("cinedex: the export settings were not kept: {e}");
                }
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
        ExportDialog::Setup { path, form, spans, summary, hffmpeg, problem, choosing } => {
            page = page.push(heading("Export movie")).push(label("The edit as it plays here, saved or not."));
            let field = text_input(Id::new("export path"), path.clone())
                .placeholder("C:/Users/me/Videos/edit.mp4".to_owned())
                .on_input(|s| wrap(ExportMessage::Path(s)))
                .on_submit(|| wrap(ExportMessage::Start))
                .width(Length::Fill);
            page = page
                .push(label("Movie file (.mp4 or .mov)"))
                .push(row().spacing(8.0).align_y(Vertical::Center).push(field).push(choice("browse", "Browse…", ExportMessage::Browse)));
            let line = |name: &str| row().spacing(8.0).align_y(Vertical::Center).push(container(label(name)).width(Length::fixed(64.0)));
            let number = |id: &'static str, value: &str, on: fn(String) -> ExportMessage| {
                text_input(Id::new(id), value.to_owned())
                    .on_input(move |s| wrap(on(s)))
                    .on_submit(|| wrap(ExportMessage::Start))
                    .width(Length::fixed(72.0))
            };
            // Size: the edit's, half, or another of its shape.
            let size_at = SizeKind::ALL.iter().position(|k| *k == form.size).unwrap_or(0);
            let sizes = segmented(Id::new("export size"), size_at)
                .push("Edit's")
                .push("Half")
                .push("Custom")
                .on_select(|i| wrap(ExportMessage::Size(i)));
            let mut size = line("Size").push(sizes);
            if form.size == SizeKind::Custom {
                size = size
                    .push(number("export width", &form.width, ExportMessage::Width))
                    .push(label("x"))
                    .push(number("export height", &form.height, ExportMessage::Height))
                    .push(label("the edit's shape is kept"));
            }
            // Frames: all, the selection or loop when there is one, or typed.
            let kinds = spans.kinds();
            let span_at = kinds.iter().position(|k| *k == form.span).unwrap_or(0);
            let ranges = kinds
                .iter()
                .fold(segmented(Id::new("export range"), span_at), |s, kind| {
                    s.push(match kind {
                        SpanKind::Whole => "Whole cut",
                        SpanKind::Selection => "Selection",
                        SpanKind::Loop => "Loop",
                        SpanKind::Custom => "Custom",
                    })
                })
                .on_select(|i| wrap(ExportMessage::Span(i)));
            let mut range = line("Frames").push(ranges);
            if form.span == SpanKind::Custom {
                range = range
                    .push(number("export first", &form.first, ExportMessage::First))
                    .push(label("to"))
                    .push(number("export last", &form.last, ExportMessage::Last))
                    .push(label(&format!("of 1 to {}", spans.length)));
            }
            let codec_at = Codec::ALL.iter().position(|c| *c == form.codec).unwrap_or(0);
            let codecs = Codec::ALL
                .iter()
                .fold(segmented(Id::new("export codec"), codec_at), |s, c| s.push(c.label()))
                .on_select(|i| wrap(ExportMessage::Codec(i)));
            let quality_at = Quality::ALL.iter().position(|q| *q == form.quality).unwrap_or(0);
            let qualities = Quality::ALL
                .iter()
                .fold(segmented(Id::new("export quality"), quality_at), |s, q| s.push(q.label()))
                .on_select(|i| wrap(ExportMessage::Quality(i)));
            let tick = |id: &'static str, on: bool, words: &str, m: fn(bool) -> ExportMessage| {
                row()
                    .spacing(8.0)
                    .align_y(Vertical::Center)
                    .push(look::tick_box(Id::new(id), on, move |on| wrap(m(on))))
                    .push(text(words.to_owned()).font_size(12.0).color(look::text()))
            };
            page = page
                .push(size)
                .push(range)
                .push(line("Codec").push(codecs).push(label(&format!(".{}", form.codec.extensions().join(" or .")))))
                .push(line("Quality").push(qualities))
                .push(row().spacing(20.0).push(tick("export audio", form.audio, "Sound", ExportMessage::Audio)).push(tick(
                    "export burnins",
                    form.burnins,
                    "Burn-ins, as the viewer shows them",
                    ExportMessage::Burnins,
                )));
            page = match summary {
                Ok(summary) => page.push(text(summary.clone()).font_size(12.0).color(look::text())),
                Err(why) => page.push(text(why.clone()).font_size(12.0).color(look::error())),
            };
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
        // The one place a request is made, for the dialog and for agents.
        let request =
            source.split(concat!("fn export", "_request(")).nth(1).and_then(|rest| rest.split("\n    }\n").next()).unwrap_or_default();
        assert_eq!(source.matches(concat!("Request", " {")).count(), 2, "requests are made in one place: its signature, its literal");
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
            let got = start_problem(&path, &encoder, frames, Codec::H264);
            if frames > 0 && has_encoder && kind == 0 {
                prop_assert_eq!(got.unwrap(), PathBuf::from(&path));
            } else {
                let why = got.unwrap_err();
                if frames <= 0 { prop_assert!(why.contains("empty"), "{}", why); }
                else if !has_encoder { prop_assert_eq!(why, "no hffmpeg"); }
            }
        }
    }

    proptest! {
        /// A movie's extension is one its codec goes in: choosing Motion
        /// JPEG turns a .mp4 into a .mov, H.264 leaves either, a name not
        /// typed yet stays as it is; and Export refuses a mismatch typed
        /// afterwards.
        #[test]
        fn the_path_follows_the_codec(stem in "[a-z]{1,8}", ext in prop::option::of("(mp4|mov)"), mjpeg in any::<bool>()) {
            let dir = tempfile::tempdir().unwrap();
            let folder = dir.path().to_string_lossy().replace('\\', "/");
            let typed = match &ext { Some(e) => format!("{folder}/{stem}.{e}"), None => format!("{folder}/{stem}") };
            let codec = if mjpeg { Codec::Mjpeg } else { Codec::H264 };
            let got = path_for(&typed, codec);
            let want = match (ext.as_deref(), mjpeg) {
                (_, true) => format!("{folder}/{stem}.mov"),
                (Some(e), false) => format!("{folder}/{stem}.{e}"),
                (None, false) => typed.clone(),
            };
            prop_assert_eq!(PathBuf::from(&got), PathBuf::from(&want));
            let encoder = Ok(PathBuf::from("hffmpeg"));
            prop_assert!(start_problem(&got, &encoder, 5, codec).is_ok());
            let mp4 = format!("{folder}/{stem}.mp4");
            prop_assert_eq!(start_problem(&mp4, &encoder, 5, codec).is_ok(), !mjpeg);
            prop_assert_eq!(path_for("", codec), "");
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
                form: Box::new(Form::new(&Settings::default(), [16, 16], &Spans::default())),
                spans: Spans::default(),
                summary: Ok(String::new()),
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
