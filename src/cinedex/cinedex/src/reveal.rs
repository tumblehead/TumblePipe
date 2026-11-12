//! Reveal folder (#59): the folder of the take a clip plays, in the
//! system's file manager.

use std::path::{Path, PathBuf};

use th_playlist::Media;

/// The folder `media`'s files are in: a movie's folder, a frame sequence's
/// directory, a stack's first layer's.
pub(crate) fn media_folder(media: &Media) -> Option<PathBuf> {
    match media {
        Media::Movie { path } => Path::new(path).parent().map(Path::to_path_buf),
        Media::Exr { frames, .. } | Media::Live { frames } => Some(PathBuf::from(&frames.directory)),
        Media::Stack { layers } => layers.first().and_then(|l| media_folder(&l.media)),
    }
}

/// Open `folder` in the file manager (Explorer, Finder, the desktop's).
pub(crate) fn reveal(folder: &Path) -> Result<(), String> {
    if !folder.is_dir() {
        return Err(format!("{} is not there", folder.display()));
    }
    let program = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    std::process::Command::new(program).arg(native(folder)).spawn().map(|_| ()).map_err(|e| format!("{program}: {e}"))
}

/// `folder` as the file manager reads it: Explorer takes only backslashes
/// (given `C:/a/b` it opens Documents instead); elsewhere as it is.
fn native(folder: &Path) -> std::ffi::OsString {
    if cfg!(windows) { folder.to_string_lossy().replace('/', "\\").into() } else { folder.as_os_str().to_owned() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_playlist::{Frames, StackLayer};

    fn part() -> impl Strategy<Value = String> {
        "[A-Za-z0-9_ .-]{1,8}".prop_filter("a real name", |s| s.trim_matches(['.', ' ']).len() == s.len())
    }

    proptest! {
        /// The folder revealed holds the take: a movie is in it; frames and
        /// a stack's first layer are it, however deep and whichever slashes.
        #[test]
        fn the_folder_holds_the_take(
            drive in prop::sample::select(vec!["C:", "P:", "", "//server/share"]),
            parts in prop::collection::vec(part(), 1..5),
            slash in prop::sample::select(vec!["/", "\\"]),
            file in part(),
            depth in 1usize..4,
        ) {
            let dir = format!("{drive}{slash}{}", parts.join(slash));
            let movie = format!("{dir}{slash}{file}.mov");
            let folder = media_folder(&Media::Movie { path: movie.clone() }).unwrap();
            prop_assert!(Path::new(&movie).starts_with(&folder), "{} is not in {}", movie, folder.display());
            prop_assert_eq!(Path::new(&movie).parent(), Some(folder.as_path()));
            let layer = Media::Exr { layer: "beauty".into(), frames: frames(&dir) };
            let mut stack = layer.clone();
            for _ in 0..depth {
                stack = Media::Stack { layers: vec![StackLayer { name: "a".into(), media: stack }, StackLayer { name: "b".into(), media: Media::Live { frames: frames("Z:/other") } }] };
            }
            prop_assert_eq!(media_folder(&stack), Some(PathBuf::from(&dir)));
            if cfg!(windows) {
                // Explorer gets backslashes only, naming the same folder.
                let native = native(&folder);
                let native = native.to_string_lossy();
                prop_assert!(!native.contains('/'), "{}", native);
                prop_assert!(Path::new(native.as_ref()).components().eq(folder.components()));
            }
        }
    }

    fn frames(directory: &str) -> Frames {
        Frames { directory: directory.into(), prefix: "f.".into(), suffix: ".exr".into(), padding: 4, first: 1001, last: 1010, step: 1 }
    }

    #[test]
    fn every_kind_of_take_has_its_folder() {
        assert_eq!(media_folder(&Media::Movie { path: "P:/show/review/sh010_v0003.mov".into() }), Some(PathBuf::from("P:/show/review")));
        assert_eq!(
            media_folder(&Media::Exr { layer: "default".into(), frames: frames("P:/r/v0002/beauty") }),
            Some(PathBuf::from("P:/r/v0002/beauty"))
        );
        assert_eq!(media_folder(&Media::Live { frames: frames("C:/live/x") }), Some(PathBuf::from("C:/live/x")));
        let stack = Media::Stack {
            layers: vec![
                StackLayer { name: "chars".into(), media: Media::Exr { layer: "chars".into(), frames: frames("P:/r/chars") } },
                StackLayer { name: "env".into(), media: Media::Exr { layer: "env".into(), frames: frames("P:/r/env") } },
            ],
        };
        assert_eq!(media_folder(&stack), Some(PathBuf::from("P:/r/chars")));
        assert_eq!(media_folder(&Media::Stack { layers: vec![] }), None);
        assert!(reveal(Path::new("Z:/no/such/folder/here")).is_err());
        if cfg!(windows) {
            assert_eq!(native(Path::new("P:/show/review")), std::ffi::OsString::from(r"P:\show\review"));
        }
    }
}
