//! TumblePipe tt_setup wizard — native (Rust/egui) replacement for the
//! PySide6 wizard that used to live at `scripts/tt_setup.py`.
//!
//! Runs when the user clicks Configure on the TumblePipe package card in
//! TumbleTrove. Presents a small wizard with two flows:
//!
//!   1. Use existing project — browse to a project root that already has a
//!      valid `_config/` directory. Emits the env var pointing TumblePipe at
//!      it.
//!
//!   2. Create new project — collect name + fps + parent directory, copy the
//!      bundled `project_template/` into `<parent>/<name>/`, customize the
//!      JSON databases, and create the standard top-level subdirs.
//!
//! On accept the wizard prints a single JSON object to stdout describing the
//! project-scope env var overrides:
//!
//!     {"envVars":{"TH_PROJECT_PATH":"/abs/path/to/project"}}
//!
//! On cancel it exits non-zero with no stdout, which causes TumbleTrove to
//! surface the error and block the configure action — intentional, the
//! package can't function without TH_PROJECT_PATH.
//!
//! Stdout is reserved for the JSON payload. All UI and diagnostics go through
//! the GUI or stderr.
//!
//! ## Why a native binary
//!
//! The Python wizard was provisioned on demand through hpm's uv-managed venv
//! (`[scripts.tt_setup]` with `requirements = ["PySide6>=6.6"]`). The very
//! first Configure click had to fetch a CPython interpreter and build a venv
//! with PySide6 (~100 MB) before the window could appear. This binary ships
//! prebuilt in the package archive (like the resolver), so launch is instant
//! with no runtime download.
//!
//! ## Console/stdout note
//!
//! This is a normal console-subsystem binary (NOT `windows_subsystem =
//! "windows"`). hpm captures the script's stdout through a pipe to read the
//! JSON payload, exactly as it did for `python scripts/tt_setup.py`; a GUI
//! (`windows` subsystem) binary would detach stdout and break that contract.
//! Whatever console-suppression hpm/Desktop applies when spawning python.exe
//! applies equally here.

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::{Arc, Mutex};

use serde_json::json;

use th_project_wizard::{
    expanduser, home_dir, is_valid_project_name, looks_like_project, scaffold_project,
};

/// Status colors chosen to contrast on both light and dark themes (egui
/// follows the host theme). Mirror the old wizard's STATUS_ERROR/STATUS_OK.
const STATUS_ERROR: egui::Color32 = egui::Color32::from_rgb(0xe0, 0x6c, 0x75);
const STATUS_OK: egui::Color32 = egui::Color32::from_rgb(0x98, 0xc3, 0x79);

/// What the wizard produces. `Cancelled` is the default so closing the window
/// via the [x] (or Cancel) is treated as a cancel, matching the Python exit-1
/// path.
#[derive(Clone, Default)]
enum Outcome {
    #[default]
    Cancelled,
    Accepted(PathBuf),
}

fn main() -> ExitCode {
    let template_dir = resolve_template_dir();

    let outcome: Arc<Mutex<Outcome>> = Arc::new(Mutex::new(Outcome::default()));
    let app_outcome = Arc::clone(&outcome);

    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([640.0, 480.0])
            .with_min_inner_size([560.0, 420.0]),
        // Leave follow_system_theme at its default (true) so the wizard adopts
        // the host light/dark theme — the reason the Qt version had to fight
        // the white banner panel.
        ..Default::default()
    };

    let run = eframe::run_native(
        "TumblePipe — Project Setup",
        native_options,
        Box::new(move |_cc| Ok(Box::new(WizardApp::new(template_dir, app_outcome)))),
    );

    if let Err(err) = run {
        eprintln!("tt_setup: failed to start the wizard: {err}");
        return ExitCode::from(2);
    }

    let final_outcome = outcome.lock().unwrap().clone();
    match final_outcome {
        Outcome::Accepted(project_path) => {
            // Compact JSON; TumbleTrove parses it, so spacing is irrelevant.
            // On Windows the path's backslashes are JSON-escaped, matching
            // Python's json.dumps(str(WindowsPath(...))).
            let payload = json!({
                "envVars": { "TH_PROJECT_PATH": project_path.to_string_lossy() }
            });
            println!("{}", serde_json::to_string(&payload).unwrap());
            ExitCode::SUCCESS
        }
        Outcome::Cancelled => {
            eprintln!("tt_setup: cancelled by user.");
            ExitCode::FAILURE
        }
    }
}

/// Locate the bundled `project_template/` directory.
///
/// hpm invokes this binary from the package root, and its `[scripts.tt_setup]`
/// command passes `--template-dir scripts/project_template` explicitly. Fall
/// back to a couple of sensible locations (cwd, exe-relative) so the binary is
/// still runnable standalone during development.
fn resolve_template_dir() -> PathBuf {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--template-dir" {
            if let Some(value) = args.next() {
                return PathBuf::from(value);
            }
        } else if let Some(value) = arg.strip_prefix("--template-dir=") {
            return PathBuf::from(value);
        }
    }

    // cwd-relative (hpm runs from the package root).
    let cwd_candidate = PathBuf::from("scripts/project_template");
    if cwd_candidate.is_dir() {
        return cwd_candidate;
    }

    // exe-relative: bin/<platform>/tt_setup -> <root>/scripts/project_template.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(root) = exe.parent().and_then(|p| p.parent()).and_then(|p| p.parent()) {
            let candidate = root.join("scripts/project_template");
            if candidate.is_dir() {
                return candidate;
            }
        }
    }

    cwd_candidate
}

// ---------- GUI -------------------------------------------------------------

#[derive(PartialEq)]
enum Page {
    Mode,
    Existing,
    New,
}

struct WizardApp {
    template_dir: PathBuf,
    outcome: Arc<Mutex<Outcome>>,
    page: Page,
    /// Mode page: true = existing, false = new.
    mode_existing: bool,
    existing_path: String,
    new_name: String,
    new_parent: String,
    new_fps: i64,
    /// Non-empty when a scaffold error should be shown as a modal.
    error: Option<String>,
}

impl WizardApp {
    fn new(template_dir: PathBuf, outcome: Arc<Mutex<Outcome>>) -> Self {
        Self {
            template_dir,
            outcome,
            page: Page::Mode,
            mode_existing: true,
            existing_path: String::new(),
            new_name: String::new(),
            new_parent: String::new(),
            new_fps: 24,
            error: None,
        }
    }

    fn finish(&self, ctx: &egui::Context, project_path: PathBuf) {
        *self.outcome.lock().unwrap() = Outcome::Accepted(project_path);
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }

    fn cancel(&self, ctx: &egui::Context) {
        *self.outcome.lock().unwrap() = Outcome::Cancelled;
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }

    fn existing_target(&self) -> Option<PathBuf> {
        let text = self.existing_path.trim();
        if text.is_empty() {
            return None;
        }
        Some(expanduser(text))
    }

    fn existing_is_complete(&self) -> bool {
        match self.existing_target() {
            Some(path) => path.is_dir() && looks_like_project(&path),
            None => false,
        }
    }

    fn new_target(&self) -> Option<PathBuf> {
        let name = self.new_name.trim();
        let parent = self.new_parent.trim();
        if name.is_empty() || parent.is_empty() {
            return None;
        }
        Some(expanduser(parent).join(name))
    }

    fn new_is_complete(&self) -> bool {
        let name = self.new_name.trim();
        let parent = self.new_parent.trim();
        if !is_valid_project_name(name) || parent.is_empty() {
            return false;
        }
        if !expanduser(parent).is_dir() {
            return false;
        }
        match self.new_target() {
            Some(target) => !target.exists(),
            None => false,
        }
    }
}

impl eframe::App for WizardApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Error modal takes precedence: mirror the Qt QMessageBox.critical that
        // kept the user on the page after a failed scaffold.
        if let Some(message) = self.error.clone() {
            egui::Window::new("Project creation failed")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::CENTER_CENTER, egui::Vec2::ZERO)
                .show(ctx, |ui| {
                    ui.label(message);
                    ui.add_space(8.0);
                    if ui.button("OK").clicked() {
                        self.error = None;
                    }
                });
        }

        let modal_open = self.error.is_some();

        egui::TopBottomPanel::bottom("buttons").show(ctx, |ui| {
            ui.add_enabled_ui(!modal_open, |ui| {
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if ui.button("Cancel").clicked() {
                        self.cancel(ctx);
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        match self.page {
                            Page::Mode => {
                                if ui.button("Next").clicked() {
                                    self.page = if self.mode_existing {
                                        Page::Existing
                                    } else {
                                        Page::New
                                    };
                                }
                            }
                            Page::Existing => {
                                let complete = self.existing_is_complete();
                                if ui.add_enabled(complete, egui::Button::new("Finish")).clicked() {
                                    if let Some(path) = self.existing_target() {
                                        self.finish(ctx, path);
                                    }
                                }
                                if ui.button("Back").clicked() {
                                    self.page = Page::Mode;
                                }
                            }
                            Page::New => {
                                let complete = self.new_is_complete();
                                if ui.add_enabled(complete, egui::Button::new("Finish")).clicked() {
                                    if let Some(target) = self.new_target() {
                                        match scaffold_project(
                                            &self.template_dir,
                                            &target,
                                            self.new_name.trim(),
                                            self.new_fps,
                                        ) {
                                            Ok(()) => self.finish(ctx, target),
                                            Err(err) => {
                                                self.error = Some(format!(
                                                    "Could not scaffold project:\n\n{err}"
                                                ));
                                            }
                                        }
                                    }
                                }
                                if ui.button("Back").clicked() {
                                    self.page = Page::Mode;
                                }
                            }
                        }
                    });
                });
                ui.add_space(6.0);
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.add_enabled_ui(!modal_open, |ui| match self.page {
                Page::Mode => self.ui_mode_page(ui),
                Page::Existing => self.ui_existing_page(ui),
                Page::New => self.ui_new_page(ui),
            });
        });
    }
}

impl WizardApp {
    fn ui_mode_page(&mut self, ui: &mut egui::Ui) {
        ui.add_space(8.0);
        ui.heading("TumblePipe Project");
        ui.label(
            "Point TumblePipe at an existing project on disk, or create a new one \
             from the bundled template.",
        );
        ui.add_space(16.0);

        ui.radio_value(&mut self.mode_existing, true, "Use an existing project");
        ui.indent("existing_help", |ui| {
            ui.label(
                egui::RichText::new(
                    "Choose this if you already have a project folder containing a \
                     _config/ directory.",
                )
                .weak(),
            );
        });

        ui.add_space(8.0);

        ui.radio_value(&mut self.mode_existing, false, "Create a new project");
        ui.indent("new_help", |ui| {
            ui.label(
                egui::RichText::new(
                    "Choose this to scaffold a new project from the TumblePipe template \
                     (config databases, conventions, USD context).",
                )
                .weak(),
            );
        });
    }

    fn ui_existing_page(&mut self, ui: &mut egui::Ui) {
        ui.add_space(8.0);
        ui.heading("Select Existing Project");
        ui.label("Browse to the root of a project that already has a _config/ directory.");
        ui.add_space(16.0);

        ui.horizontal(|ui| {
            ui.label("Project root");
            ui.add(
                egui::TextEdit::singleline(&mut self.existing_path)
                    .hint_text("Path to project root…")
                    .desired_width(f32::INFINITY),
            );
        });
        ui.add_space(4.0);
        if ui.button("Browse…").clicked() {
            let mut dialog = rfd::FileDialog::new();
            if let Some(start) = existing_start_dir(&self.existing_path) {
                dialog = dialog.set_directory(start);
            }
            if let Some(chosen) = dialog.pick_folder() {
                self.existing_path = chosen.to_string_lossy().into_owned();
            }
        }

        ui.add_space(12.0);
        self.ui_existing_status(ui);
    }

    fn ui_existing_status(&self, ui: &mut egui::Ui) {
        let text = self.existing_path.trim();
        if text.is_empty() {
            return;
        }
        let path = expanduser(text);
        if !path.exists() {
            ui.colored_label(STATUS_ERROR, "Path doesn't exist.");
        } else if !path.is_dir() {
            ui.colored_label(STATUS_ERROR, "Path is not a directory.");
        } else if !looks_like_project(&path) {
            ui.colored_label(
                STATUS_ERROR,
                "Couldn't find _config/db/entity.json inside this folder. \
                 Pick the project root, not a sub-folder.",
            );
        } else {
            ui.colored_label(
                STATUS_OK,
                format!("Looks valid — will set TH_PROJECT_PATH to {}.", path.display()),
            );
        }
    }

    fn ui_new_page(&mut self, ui: &mut egui::Ui) {
        ui.add_space(8.0);
        ui.heading("Create New Project");
        ui.label(
            "Choose where the project goes and a few defaults. TumblePipe's project \
             template will be copied to <parent>/<name>/.",
        );
        ui.add_space(16.0);

        egui::Grid::new("new_project_form")
            .num_columns(2)
            .spacing([12.0, 10.0])
            .show(ui, |ui| {
                ui.label("Project name");
                ui.add(
                    egui::TextEdit::singleline(&mut self.new_name)
                        .hint_text("alphanumeric, e.g. myfilm")
                        .desired_width(f32::INFINITY),
                );
                ui.end_row();

                ui.label("Parent directory");
                ui.horizontal(|ui| {
                    if ui.button("Browse…").clicked() {
                        let mut dialog = rfd::FileDialog::new();
                        if let Some(start) = existing_start_dir(&self.new_parent) {
                            dialog = dialog.set_directory(start);
                        }
                        if let Some(chosen) = dialog.pick_folder() {
                            self.new_parent = chosen.to_string_lossy().into_owned();
                        }
                    }
                    ui.add(
                        egui::TextEdit::singleline(&mut self.new_parent)
                            .hint_text("Parent directory…")
                            .desired_width(f32::INFINITY),
                    );
                });
                ui.end_row();

                ui.label("FPS");
                ui.add(egui::DragValue::new(&mut self.new_fps).range(1..=240));
                ui.end_row();
            });

        ui.add_space(12.0);
        self.ui_new_status(ui);
    }

    fn ui_new_status(&self, ui: &mut egui::Ui) {
        let name = self.new_name.trim();
        let parent = self.new_parent.trim();

        if !name.is_empty() && !is_valid_project_name(name) {
            ui.colored_label(
                STATUS_ERROR,
                "Project name must be alphanumeric (no spaces or dashes).",
            );
            return;
        }
        if !parent.is_empty() {
            let parent_path = expanduser(parent);
            if !parent_path.exists() {
                ui.colored_label(STATUS_ERROR, "Parent directory doesn't exist.");
                return;
            }
            if !parent_path.is_dir() {
                ui.colored_label(STATUS_ERROR, "Parent path is not a directory.");
                return;
            }
        }
        if let Some(target) = self.new_target() {
            if target.exists() {
                ui.colored_label(
                    STATUS_ERROR,
                    format!(
                        "{} already exists — pick a different name or parent.",
                        target.display()
                    ),
                );
            } else {
                ui.colored_label(STATUS_OK, format!("Will create {}", target.display()));
            }
        }
    }
}

/// Directory to open the native picker at: the current text if it's a real
/// directory, else the user's home, else the OS default.
fn existing_start_dir(text: &str) -> Option<PathBuf> {
    let text = text.trim();
    if !text.is_empty() {
        let path = expanduser(text);
        if path.is_dir() {
            return Some(path);
        }
    }
    home_dir()
}
