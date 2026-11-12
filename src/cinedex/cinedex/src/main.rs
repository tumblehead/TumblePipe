//! Cinedex: TumblePipe's editor. Edits are pipeline entities, versioned
//! and published (`designs/edit-entity.md`); it plays them frame-accurately,
//! and shots end to end as an untitled edit (the asset browser's Play).
//!
//! ```text
//! cinedex [--project <root>] [--ocio <config>] --edit entity:/edits/<name>[@vNNNN]
//! cinedex [--project <root>] [--ocio <config>] --shots <shot uri>...
//! cinedex [--project <root>] [--ocio <config>]
//! cinedex [--project <root>] [--ocio <config>] --render entity:/edits/<name>@vNNNN --out <dir>
//! cinedex [--project <root>] --mcp
//! cinedex [--ocio <config>] --frames <batch folder | frames folder | frame>
//! ```
//!
//! `--edit` opens the edit's newest working version (offering an autosave
//! newer than it back), or with `@vNNNN` that published version, read-only;
//! `--shots` an untitled edit of those shots end to end; neither, an
//! untitled edit of every shot of the project (as is an edit never saved);
//! `--render`, no window: the published version's frames, mix
//! and `render.json` written into `--out` for the farm (see `render`);
//! `--mcp`, no window: an MCP server on stdin/stdout giving an agent the
//! running players' commands (see `mcp` and `agent`);
//! `--frames`, a window of preview frames (#75): a local farm batch's ready
//! previews, a folder's frames, or one frame, stepped through as stills
//! (see `previews` and `frames_view`). The
//! project is `--project`, else `$TH_PROJECT_PATH`; the
//! OCIO config `--ocio`, else `$OCIO`. When either is missing, or the edit
//! does not open, the window says why. See `editor` for the window and its
//! keys.

mod agent;
mod audio;
mod board;
mod burnin;
mod colour;
mod display;
mod editing;
mod editor;
mod engine;
mod export;
mod frames_view;
mod journal;
mod layer_view;
mod live;
mod live_view;
mod look;
mod mcp;
mod previews;
mod raise;
mod render;
mod reveal;
mod save_dialog;
use th_pipe::player_rpc as rpc;
mod take_menu;
mod tracks;
mod video;

use std::path::PathBuf;

use primer::prelude::*;

use colour::Colour;

/// The id the Linux desktop knows Cinedex's windows by: the Wayland
/// `app_id` and the X11 `WM_CLASS`. GNOME names the application by it (in
/// the "ready" notification a Live view raises on Wayland), and by a
/// `cinedex.desktop` file's Name when there is one.
pub(crate) const APP_ID: &str = "cinedex";

/// The window shown when there is nothing to open, saying why.
struct Failed {
    message: String,
}

impl Application for Failed {
    type Message = ();

    fn windows(&self) -> Vec<window::Window> {
        vec![
            window::Window::new(window::Id::MAIN)
                .title("Cinedex")
                .app_id(crate::APP_ID)
                .size(Size::new(720.0, 240.0))
                .theme(display::theme()),
        ]
    }

    fn update(&mut self, _message: ()) -> Command<()> {
        Command::none()
    }

    fn view(&self, _window: window::Id) -> Element<()> {
        let words = text(format!("Nothing to open: {}", self.message)).font_size(14.0).color(look::error());
        container(words).padding(16.0).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center).into()
    }
}

const USAGE: &str = "usage: cinedex [--project <root>] [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] [--view <shot uri> [--at <frame>]] | --shots <shot uri>... | --render entity:/edits/<name>@vNNNN --out <dir> | --mcp | --frames <batch folder | frames folder | frame>]";

/// What the arguments ask the editor for.
#[derive(Debug, Default, PartialEq, Eq)]
struct Args {
    project: Option<PathBuf>,
    ocio: Option<String>,
    edit: Option<String>,
    shots: Vec<String>,
    render: Option<String>,
    out: Option<PathBuf>,
    /// Live view: the shot to show on the edit, looping its neighbours.
    view: Option<String>,
    /// The shot frame the Live view's playhead goes to.
    at: Option<i64>,
    /// Serve MCP on stdin/stdout (no window).
    mcp: bool,
    /// Preview frames to show (#75): a batch folder, a frames folder or a frame.
    frames: Option<PathBuf>,
}

fn parse_args(args: impl IntoIterator<Item = String>) -> Result<Args, String> {
    let mut out = Args::default();
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        let mut value = |name: &str| args.next().ok_or_else(|| format!("{name} wants a value"));
        match arg.as_str() {
            "--project" => out.project = Some(PathBuf::from(value("--project")?)),
            "--ocio" => out.ocio = Some(value("--ocio")?),
            "--edit" => out.edit = Some(value("--edit")?),
            "--render" => out.render = Some(value("--render")?),
            "--out" => out.out = Some(PathBuf::from(value("--out")?)),
            "--view" => out.view = Some(value("--view")?),
            "--mcp" => out.mcp = true,
            "--frames" => out.frames = Some(PathBuf::from(value("--frames")?)),
            "--at" => out.at = Some(value("--at")?.parse().map_err(|_| "--at wants a frame number".to_string())?),
            "--shots" => {
                out.shots.extend(args.by_ref());
                if out.shots.is_empty() {
                    return Err("--shots wants shot URIs".into());
                }
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if out.edit.is_some() && !out.shots.is_empty() {
        return Err("--edit and --shots do not go together".into());
    }
    if out.render.is_some() != out.out.is_some() {
        return Err("--render and --out go together".into());
    }
    if out.render.is_some() && (out.edit.is_some() || !out.shots.is_empty()) {
        return Err("--render opens no window: no --edit or --shots with it".into());
    }
    if out.view.is_some() && out.edit.is_none() {
        return Err("--view shows a shot on an edit: it wants --edit".into());
    }
    if out.at.is_some() && out.view.is_none() {
        return Err("--at goes with --view".into());
    }
    if out.mcp && (out.edit.is_some() || !out.shots.is_empty() || out.render.is_some() || out.ocio.is_some()) {
        return Err("--mcp opens no window: only --project goes with it".into());
    }
    if out.frames.is_some() && (out.edit.is_some() || !out.shots.is_empty() || out.render.is_some() || out.mcp || out.view.is_some()) {
        return Err("--frames opens preview frames on their own: only --ocio and --project go with it".into());
    }
    Ok(out)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = match parse_args(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(e) => {
            eprintln!("cinedex: {e}\n{USAGE}");
            std::process::exit(2);
        }
    };
    let paths_and_ocio = || {
        let root = match &args.project {
            Some(root) => root.clone(),
            None => std::env::var_os("TH_PROJECT_PATH")
                .filter(|v| !v.is_empty())
                .map(PathBuf::from)
                .ok_or("no --project and $TH_PROJECT_PATH is not set")?,
        };
        let ocio = match &args.ocio {
            Some(ocio) => ocio.clone(),
            None => std::env::var("OCIO").ok().filter(|v| !v.trim().is_empty()).ok_or("no --ocio and $OCIO is not set")?,
        };
        Ok::<_, String>((editor::ProjectPaths::at(&root), ocio))
    };
    if args.mcp {
        let project = args.project.clone().or_else(|| std::env::var_os("TH_PROJECT_PATH").filter(|v| !v.is_empty()).map(PathBuf::from));
        mcp::run(project.as_deref())?;
        return Ok(());
    }
    if let (Some(edit), Some(out)) = (&args.render, &args.out) {
        let rendered = paths_and_ocio().and_then(|(paths, ocio)| {
            render::render(&paths, &ocio, edit, out, |at, frames| {
                if at % 24 == 0 || at + 1 == frames {
                    println!("frame {} of {frames}", at + 1);
                }
            })
        });
        match rendered {
            Ok(r) => {
                println!(
                    "rendered {} frames, {}x{} at {} fps{}",
                    r.frames,
                    r.width,
                    r.height,
                    r.fps,
                    if r.audio { ", with sound" } else { "" }
                );
                return Ok(());
            }
            Err(e) => {
                eprintln!("cinedex: {e}");
                std::process::exit(1);
            }
        }
    }
    if let Some(path) = &args.frames {
        let opened = (|| {
            let ocio = match &args.ocio {
                Some(ocio) => ocio.clone(),
                None => std::env::var("OCIO").ok().filter(|v| !v.trim().is_empty()).ok_or("no --ocio and $OCIO is not set")?,
            };
            let colour = Colour::open(&ocio).map_err(|e| format!("the OCIO config: {e}"))?;
            Ok::<_, String>(frames_view::FramesView::new(previews::open_frames(path)?, colour))
        })();
        match opened {
            Ok(view) => run(view, Setup::default())?,
            Err(message) => {
                eprintln!("cinedex: {message}");
                run(Failed { message }, Setup::default())?;
            }
        }
        return Ok(());
    }
    let opened = (|| {
        let (paths, ocio) = paths_and_ocio()?;
        let colour = Colour::open(&ocio).map_err(|e| format!("the OCIO config: {e}"))?;
        let (doc, recovered) = editor::open_document(&paths, args.edit.as_deref(), &args.shots)?;
        let editor = editor::Editor::new(paths, ocio, doc, colour, recovered);
        Ok::<_, String>(match args.view.clone() {
            Some(shot) => editor.with_view(shot, args.at),
            None => editor,
        })
    })();
    match opened {
        Ok(editor) => run(editor, Setup::default())?,
        Err(message) => {
            eprintln!("cinedex: {message}");
            run(Failed { message }, Setup::default())?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &str) -> Result<Args, String> {
        parse_args(words.split_whitespace().map(str::to_owned))
    }

    #[test]
    fn arguments_name_the_project_and_what_to_open() {
        let a = args("--project P:/show --edit entity:/edits/Main@v0003").unwrap();
        assert_eq!(a.project, Some(PathBuf::from("P:/show")));
        assert_eq!(a.edit.as_deref(), Some("entity:/edits/Main@v0003"));
        let a = args("--ocio c.ocio --shots entity:/shots/A/010 entity:/shots/A/020").unwrap();
        assert_eq!(a.shots, ["entity:/shots/A/010", "entity:/shots/A/020"]);
        assert_eq!(args("").unwrap(), Args::default());
        assert!(args("--edit").is_err());
        assert!(args("--shots").is_err());
        assert!(args("--bogus").is_err());
        assert!(args("--edit entity:/edits/Main --shots entity:/shots/A/010").is_err());
        let a = args("--render entity:/edits/Main@v0002 --out D:/r").unwrap();
        assert_eq!((a.render.as_deref(), a.out), (Some("entity:/edits/Main@v0002"), Some(PathBuf::from("D:/r"))));
        let a = args("--edit entity:/edits/Main --view entity:/shots/A/010 --at 1040").unwrap();
        assert_eq!((a.view.as_deref(), a.at), (Some("entity:/shots/A/010"), Some(1040)));
        assert!(args("--view entity:/shots/A/010").is_err(), "a view wants an edit");
        assert!(args("--edit entity:/edits/Main --at 1040").is_err(), "--at wants --view");
        assert!(args("--edit entity:/edits/Main --view entity:/shots/A/010 --at x").is_err());
        assert!(args("--render entity:/edits/Main@v0002").is_err(), "no --out");
        assert!(args("--out D:/r").is_err(), "no --render");
        assert!(args("--render entity:/edits/Main@v0002 --out D:/r --edit entity:/edits/Main").is_err());
        let a = args("--project P:/show --mcp").unwrap();
        assert!(a.mcp && a.project == Some(PathBuf::from("P:/show")));
        assert!(args("--mcp --edit entity:/edits/Main").is_err(), "no window with --mcp");
        assert!(args("--mcp --ocio c.ocio").is_err());
        let a = args("--ocio c.ocio --frames D:/farm/batches/b1").unwrap();
        assert_eq!(a.frames, Some(PathBuf::from("D:/farm/batches/b1")));
        assert!(args("--frames").is_err());
        assert!(args("--frames D:/f --edit entity:/edits/Main").is_err());
        assert!(args("--frames D:/f --shots entity:/shots/A/010").is_err());
        assert!(args("--frames D:/f --mcp").is_err());
        assert!(args("--frames D:/f --view entity:/shots/A/010").is_err());
        assert!(args("--frames D:/f --render entity:/edits/Main@v0002 --out D:/r").is_err());
    }

    /// What Open starts another window with reads back as that edit of
    /// that project, through that OCIO config, whatever the paths hold.
    #[test]
    fn open_starts_a_window_on_the_edit() {
        for (root, ocio, edit) in [
            ("P:/show", "P:/show/config.ocio", "entity:/edits/Main"),
            ("C:/Users/a b/My Show", "C:/x y/aces.ocio", "entity:/edits/Cut2@v0003"),
        ] {
            let parsed = parse_args(editor::open_arguments(std::path::Path::new(root), ocio, edit)).unwrap();
            assert_eq!(
                parsed,
                Args { project: Some(PathBuf::from(root)), ocio: Some(ocio.into()), edit: Some(edit.into()), ..Args::default() }
            );
        }
    }
}
