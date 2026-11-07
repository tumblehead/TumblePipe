//! th_player: TumblePipe's editor. Edits are pipeline entities, versioned
//! and published (`designs/edit-entity.md`); it plays them frame-accurately,
//! and shots end to end as an untitled edit (the asset browser's Play).
//!
//! ```text
//! th_player [--project <root>] [--ocio <config>] --edit entity:/edits/<name>[@vNNNN]
//! th_player [--project <root>] [--ocio <config>] --shots <shot uri>...
//! th_player [--project <root>] [--ocio <config>]
//! th_player [--project <root>] [--ocio <config>] --render entity:/edits/<name>@vNNNN --out <dir>
//! ```
//!
//! `--edit` opens the edit's newest working version (offering an autosave
//! newer than it back), or with `@vNNNN` that published version, read-only;
//! `--shots` an untitled edit of those shots end to end; neither, an
//! untitled edit of every shot of the project (as is an edit never saved);
//! `--render`, no window: the published version's frames, mix
//! and `render.json` written into `--out` for the farm (see `render`). The
//! project is `--project`, else `$TH_PROJECT_PATH`; the
//! OCIO config `--ocio`, else `$OCIO`. When either is missing, or the edit
//! does not open, the window says why. See `editor` for the window and its
//! keys.

mod audio;
mod burnin;
mod colour;
mod display;
mod editing;
mod editor;
mod engine;
mod look;
mod render;
mod take_menu;
mod tracks;
mod video;

use std::path::PathBuf;

use primer::prelude::*;

use colour::Colour;

/// The window shown when there is nothing to open, saying why.
struct Failed {
    message: String,
}

impl Application for Failed {
    type Message = ();

    fn windows(&self) -> Vec<window::Window> {
        vec![window::Window::new(window::Id::MAIN).title("TumblePipe Editor").size(Size::new(720.0, 240.0)).theme(display::theme())]
    }

    fn update(&mut self, _message: ()) -> Command<()> {
        Command::none()
    }

    fn view(&self, _window: window::Id) -> Element<()> {
        let words = text(format!("Nothing to open: {}", self.message)).font_size(14.0).color(look::error());
        container(words).padding(16.0).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center).into()
    }
}

const USAGE: &str = "usage: th_player [--project <root>] [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] | --shots <shot uri>... | --render entity:/edits/<name>@vNNNN --out <dir>]";

/// What the arguments ask the editor for.
#[derive(Debug, Default, PartialEq, Eq)]
struct Args {
    project: Option<PathBuf>,
    ocio: Option<String>,
    edit: Option<String>,
    shots: Vec<String>,
    render: Option<String>,
    out: Option<PathBuf>,
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
    Ok(out)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = match parse_args(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(e) => {
            eprintln!("th_player: {e}\n{USAGE}");
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
                eprintln!("th_player: {e}");
                std::process::exit(1);
            }
        }
    }
    let opened = (|| {
        let (paths, ocio) = paths_and_ocio()?;
        let colour = Colour::open(&ocio).map_err(|e| format!("the OCIO config: {e}"))?;
        let (doc, recovered) = editor::open_document(&paths, args.edit.as_deref(), &args.shots)?;
        Ok::<_, String>(editor::Editor::new(paths, ocio, doc, colour, recovered))
    })();
    match opened {
        Ok(editor) => run(editor, Setup::default())?,
        Err(message) => {
            eprintln!("th_player: {message}");
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
        assert!(args("--render entity:/edits/Main@v0002").is_err(), "no --out");
        assert!(args("--out D:/r").is_err(), "no --render");
        assert!(args("--render entity:/edits/Main@v0002 --out D:/r --edit entity:/edits/Main").is_err());
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
