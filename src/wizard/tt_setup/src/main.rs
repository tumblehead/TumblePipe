//! TumblePipe tt_setup wizard — native (Rust/egui) replacement for the
//! PySide6 wizard that used to live at `scripts/tt_setup.py`.
//!
//! Runs when the user clicks Configure on the TumblePipe package card in
//! TumbleTrove. Asks for one folder:
//!
//!   - a project (has `_config/db/entity.json`) — TumblePipe is pointed at it;
//!   - an empty or not-yet-existing folder — the bundled `project_template/`
//!     is copied into it, the JSON databases customized, and the standard
//!     top-level subdirs created. Its name (the farm pool) is the desktop's
//!     `TT_PROJECT_NAME` when passed, else the folder's name; only FPS is
//!     asked.
//!
//! Anything else is refused, and the commonest wrong picks (the folder that
//! holds projects, a folder inside one) come with one-click fixes.
//!
//! On accept the wizard prints a single JSON object to stdout describing the
//! project-scope env var overrides:
//!
//!     {"envVars":{"TH_PROJECT_PATH":"/abs/path/to/project"}}
//!
//! On cancel it exits 0 with no stdout — "no env var changes" (see `main`).
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

use th_project_core::{
    classify_folder, enclosing_project, expanduser, home_dir, project_name_for, projects_in,
    sanitize_project_name, scaffold_project, FolderKind, TOP_LEVEL_DIRS,
};

/// How many projects found inside a picked folder are offered as fixes.
const MAX_SUGGESTED_PROJECTS: usize = 5;

/// Status colors chosen to contrast on both light and dark themes (egui
/// follows the host theme). Mirror the old wizard's STATUS_ERROR/STATUS_OK.
const STATUS_ERROR: egui::Color32 = egui::Color32::from_rgb(0xe0, 0x6c, 0x75);
const STATUS_OK: egui::Color32 = egui::Color32::from_rgb(0x98, 0xc3, 0x79);

/// What the wizard produces. `Cancelled` is the default so closing the window
/// via the [x] (or Cancel) is treated as a cancel.
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
            .with_inner_size([680.0, 420.0])
            .with_min_inner_size([560.0, 360.0]),
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
            // Declining is a choice, not a failure. TumbleTrove's hook
            // contract treats "empty stdout + zero exit" as "no env var
            // changes", which is exactly what a cancel means — whereas any
            // non-zero exit is reported to the user as "Configure failed for
            // tumblehead/tumblepipe", so exiting 1 here (inherited from the
            // PySide wizard this replaced) turned every Cancel click into a
            // red error toast. That matters more since TumbleTrove started
            // running this wizard unprompted when TH_PROJECT_PATH is unfilled:
            // the error arrives for a window the user never asked to open.
            // The stderr line stays, so a decline is still traceable in the
            // desktop log under [tt_setup:TumblePipe].
            eprintln!("tt_setup: cancelled by user.");
            ExitCode::SUCCESS
        }
    }
}

/// Locate the bundled `project_template/` directory: an explicit
/// `--template-dir` if it exists, else [`th_project_core::bundled_template_dir`]'s
/// search, shared with `tt_prepare`.
fn resolve_template_dir() -> PathBuf {
    let mut explicit = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--template-dir" {
            explicit = args.next().map(PathBuf::from);
            break;
        } else if let Some(value) = arg.strip_prefix("--template-dir=") {
            explicit = Some(PathBuf::from(value));
            break;
        }
    }
    th_project_core::bundled_template_dir(explicit.as_deref())
}

// ---------- GUI -------------------------------------------------------------

/// One page with one path. Pointing at a project uses it; pointing at an
/// empty or not-yet-existing folder creates one there. It replaced a
/// Use-existing / Create-new choice followed by a page per flow (and a
/// project-name field), which users found confusing: the folder already says
/// which of the two they mean, and they had named the project in TumbleTrove.
struct WizardApp {
    template_dir: PathBuf,
    outcome: Arc<Mutex<Outcome>>,
    /// `TT_PROJECT_NAME`, when the desktop passes it.
    desktop_name: Option<String>,
    path: String,
    /// Only asked for when the folder will become a new project.
    new_fps: i64,
    /// Non-empty when a scaffold error should be shown as a modal.
    error: Option<String>,
}

/// What the accept button would do with the current path, or why it can't.
enum Plan {
    Use(PathBuf),
    Create { target: PathBuf, name: String },
    /// `fixes` are one-click replacements for the path. Users were unsure
    /// whether to pick the project folder or the folder it goes in, so the
    /// commonest wrong picks (the folder above a project, a folder inside
    /// one) name the folder they most likely meant.
    Invalid { reason: String, fixes: Vec<Fix> },
}

struct Fix {
    label: String,
    path: PathBuf,
}

fn invalid(reason: impl Into<String>) -> Plan {
    Plan::Invalid { reason: reason.into(), fixes: Vec::new() }
}

impl WizardApp {
    fn new(template_dir: PathBuf, outcome: Arc<Mutex<Outcome>>) -> Self {
        let desktop_name = std::env::var("TT_PROJECT_NAME")
            .ok()
            .filter(|name| !name.trim().is_empty());
        Self {
            template_dir,
            outcome,
            desktop_name,
            path: String::new(),
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

    fn plan(&self) -> Option<Plan> {
        let text = self.path.trim();
        if text.is_empty() {
            return None;
        }
        let path = expanduser(text);
        Some(match classify_folder(&path) {
            FolderKind::Project => Plan::Use(path),
            FolderKind::NewProject => match project_name_for(self.desktop_name.as_deref(), &path) {
                Some(name) => Plan::Create { target: path, name },
                None => invalid("A new project's folder name needs at least one letter or digit."),
            },
            FolderKind::NotADirectory => invalid("That is a file, not a folder."),
            FolderKind::NotEmpty => self.not_empty_plan(&path),
            FolderKind::ParentMissing => invalid(
                "Neither this folder nor the folder it would go in exists. A new project \
                 folder can be created, but only inside a folder that already exists.",
            ),
        })
    }

    /// A folder with other things in it that isn't a project: almost always
    /// one inside a project, or the folder that holds projects.
    fn not_empty_plan(&self, path: &std::path::Path) -> Plan {
        if let Some(project) = enclosing_project(path) {
            return Plan::Invalid {
                reason: format!(
                    "This folder is inside the TumblePipe project {}. Pick the project's \
                     own folder, the one that contains _config.",
                    project.display()
                ),
                fixes: vec![Fix { label: format!("Use {}", project.display()), path: project }],
            };
        }

        let mut fixes: Vec<Fix> = projects_in(path)
            .into_iter()
            .take(MAX_SUGGESTED_PROJECTS)
            .map(|project| Fix { label: format!("Use the project {}", folder_name(&project)), path: project })
            .collect();
        let new_folder = self
            .desktop_name
            .as_deref()
            .and_then(sanitize_project_name)
            .map(|name| path.join(name))
            .filter(|candidate| classify_folder(candidate) == FolderKind::NewProject);
        let mut reason = String::from(
            "This folder already has other things in it and isn't a TumblePipe project, \
             so a project can't be set up directly in it. If this is the folder your \
             projects go in, pick the project's own folder instead.",
        );
        match new_folder {
            Some(candidate) => fixes.push(Fix {
                label: format!("Create a new project folder {} here", folder_name(&candidate)),
                path: candidate,
            }),
            None => reason.push_str(&format!(
                " To start a new project here, add a new folder name to the end of the \
                 path, such as {}.",
                path.join("MyProject").display()
            )),
        }
        Plan::Invalid { reason, fixes }
    }

    fn accept(&mut self, ctx: &egui::Context, plan: Plan) {
        match plan {
            Plan::Use(path) => self.finish(ctx, path),
            Plan::Create { target, name } => {
                match scaffold_project(&self.template_dir, &target, &name, self.new_fps) {
                    Ok(()) => self.finish(ctx, target),
                    Err(err) => {
                        self.error = Some(format!("Could not create the project:\n\n{err}"))
                    }
                }
            }
            Plan::Invalid { .. } => {}
        }
    }
}

impl eframe::App for WizardApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Error modal takes precedence and keeps the user on the page after a
        // failed scaffold.
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
        let plan = self.plan();

        egui::TopBottomPanel::bottom("buttons").show(ctx, |ui| {
            ui.add_enabled_ui(!modal_open, |ui| {
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if ui.button("Cancel").clicked() {
                        self.cancel(ctx);
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let (label, enabled) = match &plan {
                            Some(Plan::Create { .. }) => ("Create project", true),
                            Some(Plan::Use(_)) => ("Use project", true),
                            _ => ("Use project", false),
                        };
                        if ui.add_enabled(enabled, egui::Button::new(label)).clicked() {
                            if let Some(plan) = self.plan() {
                                self.accept(ctx, plan);
                            }
                        }
                    });
                });
                ui.add_space(6.0);
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.add_enabled_ui(!modal_open, |ui| self.ui_page(ui, plan.as_ref()));
            });
        });
    }
}

impl WizardApp {
    fn ui_page(&mut self, ui: &mut egui::Ui, plan: Option<&Plan>) {
        ui.add_space(8.0);
        ui.heading("Where is your TumblePipe project?");
        ui.add_space(4.0);
        ui.label(
            "Pick the project folder itself: the folder that holds (or will hold) the \
             project's _config, assets and shots folders. Not the folder above it — \
             TumblePipe puts its files straight into the folder you pick and does not \
             make a sub-folder.",
        );
        ui.add_space(6.0);
        ui.label("•  Existing project: pick the folder that contains _config.");
        ui.label(
            "•  New project: pick an empty folder, or type the path of a folder that \
             doesn't exist yet and it is created.",
        );
        ui.add_space(14.0);

        let hint = self.example_path();
        ui.horizontal(|ui| {
            if ui.button("Browse…").clicked() {
                let mut dialog = rfd::FileDialog::new()
                    .set_title("Choose the project folder (not the folder above it)");
                if let Some(start) = existing_start_dir(&self.path) {
                    dialog = dialog.set_directory(start);
                }
                if let Some(chosen) = dialog.pick_folder() {
                    self.path = chosen.to_string_lossy().into_owned();
                }
            }
            ui.add(
                egui::TextEdit::singleline(&mut self.path)
                    .hint_text(hint)
                    .desired_width(f32::INFINITY),
            );
        });

        ui.add_space(12.0);
        match plan {
            None => {}
            Some(Plan::Use(path)) => {
                ui.colored_label(
                    STATUS_OK,
                    format!("Existing TumblePipe project — TumblePipe will use {}.", path.display()),
                );
            }
            Some(Plan::Create { target, name }) => {
                let what = if target.is_dir() {
                    "This empty folder becomes the new project."
                } else {
                    "This folder will be created and becomes the new project."
                };
                ui.colored_label(STATUS_OK, what);
                ui.add_space(4.0);
                ui.label("The project's files go directly inside it:");
                ui.monospace(format!("{}", target.display()));
                let children: Vec<String> = std::iter::once("_config")
                    .chain(TOP_LEVEL_DIRS)
                    .map(|dir| format!("{dir}/"))
                    .collect();
                ui.monospace(format!("    {}", children.join("  ")));
                ui.add_space(8.0);
                ui.label(format!("Project name (its farm pool): {name}"));
                ui.horizontal(|ui| {
                    ui.label("FPS");
                    ui.add(egui::DragValue::new(&mut self.new_fps).range(1..=240));
                });
            }
            Some(Plan::Invalid { reason, fixes }) => {
                ui.colored_label(STATUS_ERROR, reason);
                if !fixes.is_empty() {
                    ui.add_space(6.0);
                    ui.label("Did you mean:");
                    for fix in fixes {
                        let button = ui.button(&fix.label).on_hover_text(fix.path.display().to_string());
                        if button.clicked() {
                            self.path = fix.path.to_string_lossy().into_owned();
                        }
                    }
                }
            }
        }
    }

    /// A hint in the path field that shows a project folder, not a root.
    fn example_path(&self) -> String {
        let name = self
            .desktop_name
            .as_deref()
            .and_then(sanitize_project_name)
            .unwrap_or_else(|| "MyProject".into());
        if cfg!(windows) {
            format!("e.g. P:\\Projects\\{name}")
        } else {
            format!("e.g. /projects/{name}")
        }
    }
}

fn folder_name(path: &std::path::Path) -> String {
    path.file_name().map_or_else(|| path.display().to_string(), |n| n.to_string_lossy().into_owned())
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
