//! The system's Save dialog, for the Browse… of Export Movie and Export
//! Timeline ([`Kind`] says which file it asks for): Windows' common
//! Save As dialog, a Finder sheet through `osascript` on macOS, and
//! zenity or kdialog on Linux. Primer has none, and a crate for it would
//! link GTK on Linux, which artists' machines would then need.
//!
//! It runs off the UI thread and blocks until the user answers: Ok(None)
//! is Cancel. Where there is no dialog to show (a Linux without zenity or
//! kdialog) it is an error, and the path is typed instead.

use std::path::{Path, PathBuf};

/// What a Save dialog asks for: its title, what its files are called
/// together, and the kinds of file it offers, each a label and an
/// extension, the first the one a name typed without any gets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Kind {
    pub title: &'static str,
    pub files: &'static str,
    pub kinds: &'static [(&'static str, &'static str)],
}

/// Export Movie's.
pub const MOVIE: Kind = Kind { title: "Export movie", files: "Movies", kinds: &[("MP4 movie", "mp4"), ("QuickTime movie", "mov")] };

/// Ask where to save a movie, offering `offered` (its folder and name).
pub fn save_movie(offered: &Path) -> Result<Option<PathBuf>, String> {
    save(offered, &MOVIE)
}

/// Ask where to save a file of `kind`, offering `offered`.
pub fn save(offered: &Path, kind: &Kind) -> Result<Option<PathBuf>, String> {
    let chosen = platform::save(offered, kind)?;
    Ok(chosen.map(|c| with_extension_of(&c, kind)))
}

/// A chosen path as a file of `kind`: one typed without an extension gets
/// the kind's first (`.mp4` for a movie; the dialogs add none on macOS
/// and Linux).
pub fn with_extension_of(chosen: &Path, kind: &Kind) -> PathBuf {
    if chosen.extension().is_some() { chosen.to_path_buf() } else { chosen.with_extension(kind.kinds[0].1) }
}

/// `text` as an AppleScript string literal.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn applescript_string(text: &str) -> String {
    format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
}

/// [`applescript_for`] a movie; the app asks through `_for` (tests only, so
/// it is not dead code on macOS).
#[cfg(test)]
pub fn applescript(offered: &Path) -> String {
    applescript_for(offered, &MOVIE)
}

/// The AppleScript that asks for a file name of `kind`, offering `offered`,
/// and prints the POSIX path chosen.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn applescript_for(offered: &Path, kind: &Kind) -> String {
    let name = offered.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let prompt = applescript_string(kind.title);
    let mut script = format!("POSIX path of (choose file name with prompt {prompt} default name {}", applescript_string(&name));
    if let Some(folder) = offered.parent().filter(|f| !f.as_os_str().is_empty()) {
        script += &format!(" default location (POSIX file {})", applescript_string(&folder.to_string_lossy()));
    }
    script + ")"
}

/// [`linux_commands_for`] a movie; the app asks through `_for` (tests only,
/// so it is not dead code on Linux).
#[cfg(test)]
pub fn linux_commands(offered: &Path) -> [(&'static str, Vec<String>); 2] {
    linux_commands_for(offered, &MOVIE)
}

/// zenity's arguments, then kdialog's, asking for a file of `kind` and
/// offering `offered`.
#[cfg_attr(any(windows, target_os = "macos"), allow(dead_code))]
pub fn linux_commands_for(offered: &Path, kind: &Kind) -> [(&'static str, Vec<String>); 2] {
    let path = offered.to_string_lossy().into_owned();
    let globs = kind.kinds.iter().map(|(_, ext)| format!("*.{ext}")).collect::<Vec<_>>().join(" ");
    [
        (
            "zenity",
            vec![
                "--file-selection".into(),
                "--save".into(),
                "--confirm-overwrite".into(),
                format!("--title={}", kind.title),
                format!("--filename={path}"),
                format!("--file-filter={} | {globs}", kind.files),
            ],
        ),
        ("kdialog", vec!["--getsavefilename".into(), path, format!("{globs}|{}", kind.files), "--title".into(), kind.title.into()]),
    ]
}

/// The Windows dialog's filter for `kind`: a label and a pattern per kind
/// of file, each ended by a NUL, and one more to end the list.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn windows_filter(kind: &Kind) -> String {
    kind.kinds.iter().map(|(label, ext)| format!("{label} (*.{ext})\0*.{ext}\0")).collect::<String>() + "\0"
}

/// Which of `kind`'s kinds of file `offered` is, by its extension: the
/// first when it is none of them.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn kind_index(offered: &Path, kind: &Kind) -> usize {
    kind.kinds.iter().position(|(_, ext)| offered.extension().is_some_and(|e| e.eq_ignore_ascii_case(ext))).unwrap_or(0)
}

/// What a dialog program printed, as the path chosen: its first line,
/// None when empty (it was cancelled).
#[cfg_attr(windows, allow(dead_code))]
pub fn chosen_line(printed: &[u8]) -> Option<PathBuf> {
    let text = String::from_utf8_lossy(printed);
    text.lines().next().map(str::trim).filter(|l| !l.is_empty()).map(PathBuf::from)
}

#[cfg(windows)]
mod platform {
    use std::ffi::c_void;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::path::{Path, PathBuf};

    #[repr(C)]
    #[allow(non_snake_case)]
    struct OpenFileNameW {
        lStructSize: u32,
        hwndOwner: *mut c_void,
        hInstance: *mut c_void,
        lpstrFilter: *const u16,
        lpstrCustomFilter: *mut u16,
        nMaxCustFilter: u32,
        nFilterIndex: u32,
        lpstrFile: *mut u16,
        nMaxFile: u32,
        lpstrFileTitle: *mut u16,
        nMaxFileTitle: u32,
        lpstrInitialDir: *const u16,
        lpstrTitle: *const u16,
        Flags: u32,
        nFileOffset: u16,
        nFileExtension: u16,
        lpstrDefExt: *const u16,
        lCustData: isize,
        lpfnHook: *const c_void,
        lpTemplateName: *const u16,
        pvReserved: *mut c_void,
        dwReserved: u32,
        FlagsEx: u32,
    }

    #[link(name = "comdlg32")]
    unsafe extern "system" {
        fn GetSaveFileNameW(ofn: *mut OpenFileNameW) -> i32;
        fn CommDlgExtendedError() -> u32;
    }

    #[link(name = "ole32")]
    unsafe extern "system" {
        fn CoInitializeEx(reserved: *mut c_void, coinit: u32) -> i32;
        fn CoUninitialize();
    }

    #[link(name = "user32")]
    unsafe extern "system" {
        fn GetForegroundWindow() -> *mut c_void;
    }

    const OFN_OVERWRITEPROMPT: u32 = 0x2;
    const OFN_NOCHANGEDIR: u32 = 0x8;
    const OFN_PATHMUSTEXIST: u32 = 0x800;
    const OFN_EXPLORER: u32 = 0x80000;
    const COINIT_APARTMENTTHREADED: u32 = 0x2;

    fn wide(text: &std::ffi::OsStr) -> Vec<u16> {
        text.encode_wide().chain([0]).collect()
    }

    pub fn save(offered: &Path, kind: &super::Kind) -> Result<Option<PathBuf>, String> {
        let mut file = vec![0u16; 4096];
        let name: Vec<u16> = offered.file_name().map(|n| n.encode_wide().collect()).unwrap_or_default();
        file[..name.len().min(4000)].copy_from_slice(&name[..name.len().min(4000)]);
        // Explorer's dialog takes backslashes only.
        let folder = offered.parent().map(|f| f.to_string_lossy().replace('/', "\\")).unwrap_or_default();
        let folder = wide(std::ffi::OsStr::new(&folder));
        let filter: Vec<u16> = super::windows_filter(kind).encode_utf16().collect();
        let title = wide(std::ffi::OsStr::new(kind.title));
        let ext = wide(std::ffi::OsStr::new(kind.kinds[0].1));
        let index = super::kind_index(offered, kind) as u32 + 1;
        // SAFETY: every pointer is to a buffer that outlives the call; the
        // struct is OPENFILENAMEW's layout, its size given. The owner is
        // the window in front, the editor's: the user just pressed Browse.
        unsafe {
            let initialised = CoInitializeEx(std::ptr::null_mut(), COINIT_APARTMENTTHREADED) >= 0;
            let mut ofn = OpenFileNameW {
                lStructSize: std::mem::size_of::<OpenFileNameW>() as u32,
                hwndOwner: GetForegroundWindow(),
                hInstance: std::ptr::null_mut(),
                lpstrFilter: filter.as_ptr(),
                lpstrCustomFilter: std::ptr::null_mut(),
                nMaxCustFilter: 0,
                nFilterIndex: index,
                lpstrFile: file.as_mut_ptr(),
                nMaxFile: file.len() as u32,
                lpstrFileTitle: std::ptr::null_mut(),
                nMaxFileTitle: 0,
                lpstrInitialDir: folder.as_ptr(),
                lpstrTitle: title.as_ptr(),
                Flags: OFN_OVERWRITEPROMPT | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | OFN_EXPLORER,
                nFileOffset: 0,
                nFileExtension: 0,
                lpstrDefExt: ext.as_ptr(),
                lCustData: 0,
                lpfnHook: std::ptr::null(),
                lpTemplateName: std::ptr::null(),
                pvReserved: std::ptr::null_mut(),
                dwReserved: 0,
                FlagsEx: 0,
            };
            let ok = GetSaveFileNameW(&mut ofn) != 0;
            let error = if ok { 0 } else { CommDlgExtendedError() };
            if initialised {
                CoUninitialize();
            }
            if !ok {
                return if error == 0 { Ok(None) } else { Err(format!("the Save dialog failed (error {error:#x})")) };
            }
        }
        let end = file.iter().position(|&c| c == 0).unwrap_or(file.len());
        Ok(Some(PathBuf::from(std::ffi::OsString::from_wide(&file[..end]))))
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use std::path::{Path, PathBuf};

    pub fn save(offered: &Path, kind: &super::Kind) -> Result<Option<PathBuf>, String> {
        let out = std::process::Command::new("osascript")
            .args(["-e", &super::applescript_for(offered, kind)])
            .output()
            .map_err(|e| format!("osascript: {e}"))?;
        // Cancel is error -128, not a failure.
        if !out.status.success() {
            let words = String::from_utf8_lossy(&out.stderr);
            return if words.contains("-128") { Ok(None) } else { Err(format!("the Save dialog failed: {}", words.trim())) };
        }
        Ok(super::chosen_line(&out.stdout))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod platform {
    use std::path::{Path, PathBuf};

    pub fn save(offered: &Path, kind: &super::Kind) -> Result<Option<PathBuf>, String> {
        for (program, args) in super::linux_commands_for(offered, kind) {
            let Ok(out) = std::process::Command::new(program).args(&args).output() else { continue };
            // Both exit 1 on Cancel, printing nothing.
            return Ok(if out.status.success() { super::chosen_line(&out.stdout) } else { None });
        }
        Err("no Save dialog here (install zenity or kdialog): type the path".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// An AppleScript string literal read back the way AppleScript reads it.
    fn unquote(literal: &str) -> String {
        let inner = &literal[1..literal.len() - 1];
        let mut out = String::new();
        let mut chars = inner.chars();
        while let Some(c) = chars.next() {
            out.push(if c == '\\' { chars.next().unwrap() } else { c });
        }
        out
    }

    proptest! {
        /// Whatever a name holds, AppleScript reads back that name.
        #[test]
        fn applescript_strings_read_back(text in "[a-zA-Z0-9 \"\\\\/._ÆØé-]{0,20}") {
            let literal = applescript_string(&text);
            prop_assert!(literal.starts_with('"') && literal.ends_with('"'));
            prop_assert_eq!(unquote(&literal), text);
        }

        /// The script offers the file's name and its folder.
        #[test]
        fn the_script_offers_the_name_and_folder(folder in "/[a-z ]{1,8}/[a-z\"]{1,8}", name in "[a-z\"]{1,8}\\.mp4") {
            let script = applescript(&Path::new(&folder).join(&name));
            let default_name = format!("default name {}", applescript_string(&name));
            let location = format!("(POSIX file {})", applescript_string(&folder));
            prop_assert!(script.contains(&default_name), "{}", script);
            prop_assert!(script.contains(&location), "{}", script);
        }

        /// Both Linux dialogs are offered the movie.
        #[test]
        fn the_linux_dialogs_offer_the_movie(name in "[a-z ]{1,8}\\.mp4") {
            let offered = Path::new("/home/a").join(&name);
            let path = offered.to_string_lossy().into_owned();
            let [(zenity, z), (kdialog, k)] = linux_commands(&offered);
            prop_assert_eq!((zenity, kdialog), ("zenity", "kdialog"));
            let filename = format!("--filename={}", path);
            let offers = z.contains(&filename) && z.iter().any(|a| a == "--save");
            prop_assert!(offers);
            prop_assert_eq!(&k[..2], &["--getsavefilename".to_owned(), path]);
        }

        /// What a dialog prints is the path on its first line; nothing
        /// printed is Cancel.
        #[test]
        fn the_printed_line_is_the_path(path in prop::option::of("/[a-z ]{1,10}/[a-z]{1,6}\\.mp4"), trail in "(\n|\r\n|\n\nmore)?") {
            let printed = format!("{}{trail}", path.clone().unwrap_or_default());
            prop_assert_eq!(chosen_line(printed.as_bytes()), path.map(|p| PathBuf::from(p.trim())));
        }

        /// A dialog for any kind of file offers that kind: its title, its
        /// extensions in the Windows filter (the movie's as it always
        /// was) and the Linux dialogs' patterns, the offered file's kind
        /// picked, and a name typed without an extension gaining the first.
        #[test]
        fn a_dialog_offers_its_kind_of_file(which in 0usize..2, stem in "[a-z]{1,8}", picked in 0usize..3) {
            const TIMELINE: Kind = Kind { title: "Export timeline", files: "Timelines", kinds: &[("Final Cut Pro 7 XML", "xml"), ("OpenTimelineIO", "otio")] };
            let kind = [MOVIE, TIMELINE][which];
            let filter = windows_filter(&kind);
            prop_assert!(filter.ends_with("\0\0"));
            let parts: Vec<&str> = filter.trim_end_matches('\0').split('\0').collect();
            prop_assert_eq!(parts.len(), kind.kinds.len() * 2);
            for (i, (label, ext)) in kind.kinds.iter().enumerate() {
                prop_assert_eq!(parts[i * 2], format!("{label} (*.{ext})"));
                prop_assert_eq!(parts[i * 2 + 1], format!("*.{ext}"));
            }
            if which == 0 {
                prop_assert_eq!(&filter, "MP4 movie (*.mp4)\0*.mp4\0QuickTime movie (*.mov)\0*.mov\0\0");
                prop_assert_eq!(MOVIE.kinds[0].1, th_pipe::movie::MOVIE_EXTENSIONS[0]);
            }
            let ext = kind.kinds.get(picked).map(|k| k.1);
            let offered = PathBuf::from(match ext { Some(e) => format!("/a/{stem}.{e}"), None => format!("/a/{stem}.else") });
            prop_assert_eq!(kind_index(&offered, &kind), if ext.is_some() { picked } else { 0 });
            let [(_, zenity), (_, kdialog)] = linux_commands_for(&offered, &kind);
            let title = format!("--title={}", kind.title);
            prop_assert!(zenity.contains(&title));
            for (_, ext) in kind.kinds {
                let glob = format!("*.{ext}");
                prop_assert!(zenity.last().unwrap().contains(&glob) && kdialog[2].contains(&glob));
            }
            let prompt = format!("with prompt {}", applescript_string(kind.title));
            prop_assert!(applescript_for(&offered, &kind).contains(&prompt));
            let bare = PathBuf::from(format!("/a/{stem}"));
            prop_assert_eq!(with_extension_of(&bare, &kind), bare.with_extension(kind.kinds[0].1));
            prop_assert_eq!(with_extension_of(&offered, &kind), offered);
        }

        /// A chosen path keeps its extension, else gains .mp4.
        #[test]
        fn a_chosen_path_is_a_movie(stem in "[a-z]{1,8}", ext in prop::option::of("(mp4|mov|MOV)")) {
            let chosen = match &ext { Some(e) => format!("/a/{stem}.{e}"), None => format!("/a/{stem}") };
            let got = with_extension_of(Path::new(&chosen), &MOVIE);
            prop_assert_eq!(got, PathBuf::from(if ext.is_some() { chosen } else { format!("/a/{stem}.mp4") }));
        }
    }
}
