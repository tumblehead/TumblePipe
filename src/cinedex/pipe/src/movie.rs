//! A movie of an edit made on this machine (the editor's File > Export
//! movie…): the frames the editor renders, streamed into Houdini's hffmpeg
//! and encoded as the farm's Render Edit encodes them
//! (`apps.mp4.edit_movie_args`): H.264 through openh264, sizes padded even,
//! the sound as AAC, and a timecode track at the edit's start timecode.
//! `tests/test_live_parity.py`'s `export_args_alike` holds the two to the
//! same encode.
//!
//! The editor's own FFmpeg decodes only, so the encoder is Houdini's, found
//! by [`find_hffmpeg`].

use std::path::{Path, PathBuf};

/// The variable that names an hffmpeg (or any FFmpeg with openh264 and
/// AAC) to export with, ahead of the Houdini ones.
pub const HFFMPEG_OVERRIDE: &str = "TH_HFFMPEG";

/// The containers Export movie writes, by extension: both take H.264 and
/// AAC as they are.
pub const MOVIE_EXTENSIONS: [&str; 2] = ["mp4", "mov"];

/// hffmpeg's arguments that encode `frames` raw RGB frames (`rgb24`,
/// `width` x `height`, read from its stdin) at `fps`, with the WAV `audio`
/// when the edit has sound, into `output`, starting its timecode track at
/// `start_timecode`. From `-frames:v` on they are the farm's
/// (`apps.mp4.edit_movie_args`); only the video input differs, a pipe
/// instead of a PNG sequence on disk.
pub fn edit_export_args(
    width: u32,
    height: u32,
    fps: u32,
    frames: usize,
    audio: Option<&str>,
    start_timecode: &str,
    output: &str,
) -> Result<Vec<String>, String> {
    if frames == 0 || fps == 0 || width == 0 || height == 0 {
        return Err(format!("No movie of {frames} frames of {width}x{height} at {fps} fps"));
    }
    let mut args: Vec<String> =
        ["-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", &format!("{width}x{height}"), "-framerate", &fps.to_string(), "-i", "pipe:0"]
            .into_iter()
            .map(str::to_owned)
            .collect();
    if let Some(audio) = audio {
        args.extend(["-i".to_owned(), audio.to_owned()]);
    }
    args.extend(
        [
            "-frames:v",
            &frames.to_string(),
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libopenh264",
            "-rc_mode",
            "quality",
            "-q:v",
            "22",
            "-pix_fmt",
            "yuv420p",
        ]
        .into_iter()
        .map(str::to_owned),
    );
    if audio.is_some() {
        args.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"].into_iter().map(str::to_owned));
    }
    args.extend(["-timecode".to_owned(), start_timecode.to_owned(), output.to_owned()]);
    Ok(args)
}

/// The hffmpeg program's file name on this platform.
fn program() -> &'static str {
    if cfg!(windows) { "hffmpeg.exe" } else { "hffmpeg" }
}

/// The hffmpeg to export with: `$TH_HFFMPEG` when it names a file, else
/// the running Houdini's (`$HFS/bin`, which an editor started from Houdini
/// inherits), else the newest of `installs` (each a Houdini's `bin`
/// folder, as [`houdini_bins`] lists them) that has one. `env` reads a
/// variable; `is_file` asks whether a path is a file.
pub fn find_hffmpeg(
    env: impl Fn(&str) -> Option<String>,
    installs: impl FnOnce() -> Vec<PathBuf>,
    is_file: impl Fn(&Path) -> bool,
) -> Option<PathBuf> {
    let set = |name: &str| env(name).map(|v| v.trim().to_owned()).filter(|v| !v.is_empty());
    if let Some(named) = set(HFFMPEG_OVERRIDE).map(PathBuf::from)
        && is_file(&named)
    {
        return Some(named);
    }
    if let Some(hfs) = set("HFS") {
        let beside = Path::new(&hfs).join("bin").join(program());
        if is_file(&beside) {
            return Some(beside);
        }
    }
    let mut bins = installs();
    bins.sort_by(|a, b| version_key(a).cmp(&version_key(b)).then_with(|| a.cmp(b)));
    bins.into_iter().rev().map(|bin| bin.join(program())).find(|p| is_file(p))
}

/// The `major.minor.build` a Houdini install's path names (`Houdini
/// 22.0.457`, `Houdini22.0.457`, `hfs22.0.457`), as numbers to sort by;
/// none sorts first.
fn version_key(bin: &Path) -> Vec<u64> {
    bin.components()
        .filter_map(|c| {
            let name = c.as_os_str().to_string_lossy().replace(' ', "");
            let digits = name.trim_start_matches(|ch: char| !ch.is_ascii_digit());
            let parts: Vec<u64> = digits.split('.').map_while(|p| p.parse().ok()).collect();
            (parts.len() == 3).then_some(parts)
        })
        .next_back()
        .unwrap_or_default()
}

/// The `bin` folders of the Houdini installs in SideFX's default places
/// (`apps.houdini`): `<drive>:/Program Files/Side Effects Software/Houdini
/// <v>` on Windows, `/Applications/Houdini/Houdini<v>` on macOS,
/// `/opt/hfs<v>` on Linux.
pub fn houdini_bins() -> Vec<PathBuf> {
    let list = |dir: &Path| -> Vec<PathBuf> { std::fs::read_dir(dir).map(|r| r.flatten().map(|e| e.path()).collect()).unwrap_or_default() };
    let named = |p: &PathBuf, prefix: &str| p.file_name().is_some_and(|n| n.to_string_lossy().starts_with(prefix));
    if cfg!(windows) {
        "CDEFGH"
            .chars()
            .flat_map(|d| list(&PathBuf::from(format!("{d}:/Program Files/Side Effects Software"))))
            .filter(|p| named(p, "Houdini"))
            .map(|p| p.join("bin"))
            .collect()
    } else if cfg!(target_os = "macos") {
        list(Path::new("/Applications/Houdini"))
            .into_iter()
            .filter(|p| named(p, "Houdini"))
            .flat_map(|p| list(&p.join("Frameworks/Houdini.framework/Versions")))
            .map(|v| v.join("Resources/bin"))
            .collect()
    } else {
        list(Path::new("/opt")).into_iter().filter(|p| named(p, "hfs")).map(|p| p.join("bin")).collect()
    }
}

/// `path` as a movie Export movie writes: an extension added (`.mp4`) when
/// it has none; refused when it names another kind of file, or nothing.
pub fn movie_path(path: &str) -> Result<PathBuf, String> {
    let trimmed = path.trim().trim_matches('"').trim();
    if trimmed.is_empty() {
        return Err("Name the movie file".into());
    }
    let mut out = PathBuf::from(trimmed);
    if out.file_name().is_none() {
        return Err(format!("{trimmed}: name a file, not a folder"));
    }
    match out.extension().map(|e| e.to_string_lossy().to_lowercase()) {
        None => {
            out.set_extension(MOVIE_EXTENSIONS[0]);
            Ok(out)
        }
        Some(e) if MOVIE_EXTENSIONS.contains(&e.as_str()) => Ok(out),
        Some(e) => Err(format!("{trimmed}: a .{e} file; Export movie writes .mp4 or .mov")),
    }
}

/// Where the movie is written while it is made, beside `output`, then
/// renamed over it: a cancelled or failed export leaves no half movie, and
/// does not touch a file already there. Same extension, so hffmpeg picks
/// the same container.
pub fn partial_path(output: &Path) -> PathBuf {
    let stem = output.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let name = match output.extension() {
        Some(ext) => format!(".{stem}.exporting.{}", ext.to_string_lossy()),
        None => format!(".{stem}.exporting"),
    };
    output.with_file_name(name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::collections::{HashMap, HashSet};

    proptest! {
        /// The arguments read the video from stdin as raw RGB of the edit's
        /// size and rate, take the sound only when there is one, and end at
        /// the output after the timecode.
        #[test]
        fn the_arguments_read_raw_frames_and_the_sound(
            width in 1u32..5000, height in 1u32..5000, fps in 1u32..121, frames in 1usize..100_000,
            audio in prop::option::of("[a-z]{1,8}\\.wav"), output in "[a-z]{1,8}\\.(mp4|mov)",
        ) {
            let args = edit_export_args(width, height, fps, frames, audio.as_deref(), "01:00:00:00", &output).unwrap();
            let after = |flag: &str| args.iter().position(|a| a == flag).map(|i| args[i + 1].clone());
            prop_assert_eq!(after("-f"), Some("rawvideo".to_owned()));
            prop_assert_eq!(after("-s"), Some(format!("{width}x{height}")));
            prop_assert_eq!(after("-framerate"), Some(fps.to_string()));
            prop_assert_eq!(after("-frames:v"), Some(frames.to_string()));
            // H.264 in yuv420p wants even sizes: an odd edit is padded.
            prop_assert_eq!(after("-vf"), Some("pad=ceil(iw/2)*2:ceil(ih/2)*2".to_owned()));
            prop_assert_eq!(after("-c:v"), Some("libopenh264".to_owned()));
            prop_assert_eq!(after("-pix_fmt"), Some("rgb24".to_owned()));
            prop_assert_eq!(args.iter().filter(|a| *a == "yuv420p").count(), 1);
            let inputs: Vec<&String> = args.windows(2).filter(|w| w[0] == "-i").map(|w| &w[1]).collect();
            let mut want = vec!["pipe:0".to_owned()];
            want.extend(audio.clone());
            prop_assert_eq!(inputs, want.iter().collect::<Vec<_>>());
            prop_assert_eq!(args.iter().any(|a| a == "aac"), audio.is_some());
            prop_assert_eq!(&args[args.len() - 3..], &["-timecode".to_owned(), "01:00:00:00".to_owned(), output]);
        }

        /// Nothing to encode is refused.
        #[test]
        fn an_empty_movie_is_refused(width in 0u32..3, height in 0u32..3, fps in 0u32..3, frames in 0usize..3) {
            let empty = width == 0 || height == 0 || fps == 0 || frames == 0;
            prop_assert_eq!(edit_export_args(width, height, fps, frames, None, "00:00:00:00", "o.mp4").is_err(), empty);
        }

        /// $TH_HFFMPEG naming a file wins; else $HFS's; else the newest
        /// install that has one; else none.
        #[test]
        fn hffmpeg_is_the_override_then_houdinis_then_the_newest_install(
            override_set in any::<bool>(), override_exists in any::<bool>(),
            hfs_set in any::<bool>(), hfs_exists in any::<bool>(),
            installs in prop::collection::vec((0u64..30, 0u64..10, 0u64..900, any::<bool>()), 0..6),
        ) {
            let p = program();
            let mut env = HashMap::new();
            let mut files = HashSet::new();
            if override_set {
                env.insert(HFFMPEG_OVERRIDE, "D:/tools/ffmpeg.exe".to_owned());
                if override_exists { files.insert(PathBuf::from("D:/tools/ffmpeg.exe")); }
            }
            if hfs_set {
                env.insert("HFS", "C:/hfs/running".to_owned());
                if hfs_exists { files.insert(Path::new("C:/hfs/running").join("bin").join(p)); }
            }
            let bins: Vec<PathBuf> = installs.iter().map(|(a, b, c, _)| PathBuf::from(format!("C:/SFX/Houdini {a}.{b}.{c}/bin"))).collect();
            for ((_, _, _, has), bin) in installs.iter().zip(&bins) {
                if *has { files.insert(bin.join(p)); }
            }
            let found = find_hffmpeg(|n| env.get(n).cloned(), || bins.clone(), |path| files.contains(path));
            let want = if override_set && override_exists {
                Some(PathBuf::from("D:/tools/ffmpeg.exe"))
            } else if hfs_set && hfs_exists {
                Some(Path::new("C:/hfs/running").join("bin").join(p))
            } else {
                installs.iter().zip(&bins).filter(|((_, _, _, has), _)| *has)
                    .max_by(|x, y| (x.0.0, x.0.1, x.0.2, x.1).cmp(&(y.0.0, y.0.1, y.0.2, y.1)))
                    .map(|(_, bin)| bin.join(p))
            };
            prop_assert_eq!(found, want);
        }

        /// A movie path keeps a .mp4 or .mov (any case), gains .mp4 when
        /// it has no extension, and anything else is refused.
        #[test]
        fn movie_paths_are_mp4_or_mov(stem in "[A-Za-z0-9][A-Za-z0-9_ ]{0,11}", ext in prop::option::of("(mp4|MP4|mov|Mov|png|exr|txt)")) {
            let typed = match &ext { Some(e) => format!("C:/out/{stem}.{e}"), None => format!("C:/out/{stem}") };
            let got = movie_path(&format!("  \"{typed}\" "));
            match ext.as_deref().map(str::to_lowercase).as_deref() {
                None => prop_assert_eq!(got.unwrap(), PathBuf::from(format!("C:/out/{}.mp4", stem.trim()))),
                Some("mp4" | "mov") => prop_assert_eq!(got.unwrap(), PathBuf::from(typed.trim())),
                Some(_) => prop_assert!(got.is_err()),
            }
        }

        /// The partial movie is a dot file beside the movie with its
        /// extension, never the movie itself.
        #[test]
        fn the_partial_movie_is_beside_it(stem in "[A-Za-z0-9_]{1,12}", ext in "(mp4|mov)") {
            let out = PathBuf::from(format!("C:/out/{stem}.{ext}"));
            let partial = partial_path(&out);
            prop_assert_eq!(partial.parent(), out.parent());
            prop_assert_eq!(partial.extension(), out.extension());
            prop_assert_ne!(&partial, &out);
            prop_assert!(partial.file_name().unwrap().to_string_lossy().starts_with('.'));
        }
    }

    #[test]
    fn nothing_typed_is_no_movie() {
        assert!(movie_path("  ").is_err());
        assert!(movie_path("\"\"").is_err());
    }
}
