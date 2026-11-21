//! The system's Open dialog, for Import…: pick one file or several. The
//! open side of `save_dialog.rs`, asked with the same [`Kind`] (a title,
//! what the files are called together, their extensions) and shown the
//! same three ways: Windows' common Open dialog called directly, a Finder
//! sheet through `osascript` on macOS, zenity or kdialog on Linux. No
//! crate, so no GTK link.
//!
//! It runs off the UI thread and blocks until the user answers: an empty
//! list is Cancel. Where there is no dialog to show (a Linux without
//! zenity or kdialog) it is an error, and the editor asks for a typed path
//! instead (the Import dialog of before).

use std::path::{Path, PathBuf};

use crate::save_dialog::{Kind, applescript_string};

/// Import…'s: everything `import_files` knows what to do with. The first
/// row of a Windows filter lists them all; the pictures are there so a
/// frame of a sequence can be picked and named in the report.
pub const MEDIA: Kind = Kind {
    title: "Import media",
    files: "Movies, sound and pictures",
    kinds: &[
        ("QuickTime movie", "mov"),
        ("MP4 movie", "mp4"),
        ("M4V movie", "m4v"),
        ("Matroska movie", "mkv"),
        ("AVI movie", "avi"),
        ("WebM movie", "webm"),
        ("MXF movie", "mxf"),
        ("MPEG transport stream", "ts"),
        ("MPEG transport stream", "m2ts"),
        ("AVCHD movie", "mts"),
        ("Ogg movie", "ogv"),
        ("WAV sound", "wav"),
        ("MP3 sound", "mp3"),
        ("AAC sound", "aac"),
        ("FLAC sound", "flac"),
        ("Ogg sound", "ogg"),
        ("Ogg sound", "oga"),
        ("M4A sound", "m4a"),
        ("AIFF sound", "aif"),
        ("AIFF sound", "aiff"),
        ("Opus sound", "opus"),
        ("OpenEXR picture", "exr"),
        ("JPEG picture", "jpg"),
        ("JPEG picture", "jpeg"),
        ("PNG picture", "png"),
        ("TIFF picture", "tif"),
        ("TIFF picture", "tiff"),
        ("DPX picture", "dpx"),
        ("Targa picture", "tga"),
        ("BMP picture", "bmp"),
    ],
};

/// Ask which files of `kind` to open, starting in `folder` (None: where
/// the system last was). Empty when cancelled.
pub fn open(folder: Option<&Path>, kind: &Kind) -> Result<Vec<PathBuf>, String> {
    platform::open(folder, kind)
}

/// The Windows dialog's filter: every extension of `kind` as one row, then
/// all files; each label and pattern ended by a NUL, one more to end.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn windows_filter(kind: &Kind) -> String {
    let all = kind.kinds.iter().map(|(_, ext)| format!("*.{ext}")).collect::<Vec<_>>().join(";");
    format!("{}\0{all}\0All files\0*.*\0\0", kind.files)
}

/// The files a Windows multi-select dialog left in its buffer: one path
/// when one was picked; else the folder, then each name, NUL between, two
/// NULs at the end.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn windows_chosen(buffer: &[u16]) -> Vec<PathBuf> {
    let mut parts: Vec<String> = Vec::new();
    for part in buffer.split(|&c| c == 0) {
        if part.is_empty() {
            break;
        }
        parts.push(String::from_utf16_lossy(part));
    }
    match parts.as_slice() {
        [] => Vec::new(),
        [one] => vec![PathBuf::from(one)],
        [folder, names @ ..] => names.iter().map(|name| Path::new(folder).join(name)).collect(),
    }
}

/// The AppleScript that asks for files of `kind` and prints the POSIX path
/// of each on a line of its own.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn applescript(folder: Option<&Path>, kind: &Kind) -> String {
    let types = kind.kinds.iter().map(|(_, ext)| applescript_string(ext)).collect::<Vec<_>>().join(", ");
    let mut ask = format!("choose file with prompt {} of type {{{types}}}", applescript_string(kind.title));
    if let Some(folder) = folder.filter(|f| !f.as_os_str().is_empty()) {
        ask += &format!(" default location (POSIX file {})", applescript_string(&folder.to_string_lossy()));
    }
    format!(
        "set chosen to {ask} with multiple selections allowed\nset out to \"\"\nrepeat with f in chosen\nset out to out & POSIX path of f & linefeed\nend repeat\nout"
    )
}

/// zenity's arguments, then kdialog's.
#[cfg_attr(any(windows, target_os = "macos"), allow(dead_code))]
pub fn linux_commands(folder: Option<&Path>, kind: &Kind) -> [(&'static str, Vec<String>); 2] {
    let globs = kind.kinds.iter().map(|(_, ext)| format!("*.{ext}")).collect::<Vec<_>>().join(" ");
    // A folder to start in ends in a slash, or zenity takes it for a file.
    let start = folder.map(|f| format!("{}/", f.to_string_lossy().trim_end_matches('/'))).unwrap_or_default();
    let mut zenity = vec![
        "--file-selection".to_owned(),
        "--multiple".into(),
        "--separator=\n".into(),
        format!("--title={}", kind.title),
        format!("--file-filter={} | {globs}", kind.files),
        "--file-filter=All files | *".into(),
    ];
    if !start.is_empty() {
        zenity.push(format!("--filename={start}"));
    }
    let kdialog = vec![
        "--getopenfilename".to_owned(),
        if start.is_empty() { ".".into() } else { start },
        format!("{globs}|{}", kind.files),
        "--multiple".into(),
        "--separate-output".into(),
        "--title".into(),
        kind.title.into(),
    ];
    [("zenity", zenity), ("kdialog", kdialog)]
}

/// What a dialog program printed, as the paths chosen: a path a line.
#[cfg_attr(windows, allow(dead_code))]
pub fn chosen_lines(printed: &[u8]) -> Vec<PathBuf> {
    String::from_utf8_lossy(printed).lines().map(|l| l.trim_end_matches('\r')).filter(|l| !l.trim().is_empty()).map(PathBuf::from).collect()
}

#[cfg(windows)]
mod platform {
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};

    // OPENFILENAMEW, as `save_dialog` declares it for the Save dialog.
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
        fn GetOpenFileNameW(ofn: *mut OpenFileNameW) -> i32;
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

    const OFN_NOCHANGEDIR: u32 = 0x8;
    const OFN_ALLOWMULTISELECT: u32 = 0x200;
    const OFN_PATHMUSTEXIST: u32 = 0x800;
    const OFN_FILEMUSTEXIST: u32 = 0x1000;
    const OFN_EXPLORER: u32 = 0x80000;
    const COINIT_APARTMENTTHREADED: u32 = 0x2;
    /// The buffer was too small for the names picked.
    const FNERR_BUFFERTOOSMALL: u32 = 0x3003;

    fn wide(text: &std::ffi::OsStr) -> Vec<u16> {
        text.encode_wide().chain([0]).collect()
    }

    pub fn open(folder: Option<&Path>, kind: &super::Kind) -> Result<Vec<PathBuf>, String> {
        // Room for some hundreds of names.
        let mut file = vec![0u16; 65536];
        // Explorer's dialog takes backslashes only.
        let folder = folder.map(|f| f.to_string_lossy().replace('/', "\\")).unwrap_or_default();
        let folder = wide(std::ffi::OsStr::new(&folder));
        let filter: Vec<u16> = super::windows_filter(kind).encode_utf16().collect();
        let title = wide(std::ffi::OsStr::new(kind.title));
        // SAFETY: every pointer is to a buffer that outlives the call; the
        // struct is OPENFILENAMEW's layout, its size given. The owner is
        // the window in front, the editor's: the user just pressed Import.
        unsafe {
            let initialised = CoInitializeEx(std::ptr::null_mut(), COINIT_APARTMENTTHREADED) >= 0;
            let mut ofn = OpenFileNameW {
                lStructSize: std::mem::size_of::<OpenFileNameW>() as u32,
                hwndOwner: GetForegroundWindow(),
                hInstance: std::ptr::null_mut(),
                lpstrFilter: filter.as_ptr(),
                lpstrCustomFilter: std::ptr::null_mut(),
                nMaxCustFilter: 0,
                nFilterIndex: 1,
                lpstrFile: file.as_mut_ptr(),
                nMaxFile: file.len() as u32,
                lpstrFileTitle: std::ptr::null_mut(),
                nMaxFileTitle: 0,
                lpstrInitialDir: if folder.len() > 1 { folder.as_ptr() } else { std::ptr::null() },
                lpstrTitle: title.as_ptr(),
                Flags: OFN_NOCHANGEDIR | OFN_ALLOWMULTISELECT | OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST | OFN_EXPLORER,
                nFileOffset: 0,
                nFileExtension: 0,
                lpstrDefExt: std::ptr::null(),
                lCustData: 0,
                lpfnHook: std::ptr::null(),
                lpTemplateName: std::ptr::null(),
                pvReserved: std::ptr::null_mut(),
                dwReserved: 0,
                FlagsEx: 0,
            };
            let ok = GetOpenFileNameW(&mut ofn) != 0;
            let error = if ok { 0 } else { CommDlgExtendedError() };
            if initialised {
                CoUninitialize();
            }
            if !ok {
                return match error {
                    0 => Ok(Vec::new()),
                    FNERR_BUFFERTOOSMALL => Err("too many files picked at once: pick fewer".into()),
                    error => Err(format!("the Open dialog failed (error {error:#x})")),
                };
            }
        }
        Ok(super::windows_chosen(&file))
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use std::path::{Path, PathBuf};

    pub fn open(folder: Option<&Path>, kind: &super::Kind) -> Result<Vec<PathBuf>, String> {
        let out = std::process::Command::new("osascript")
            .args(["-e", &super::applescript(folder, kind)])
            .output()
            .map_err(|e| format!("osascript: {e}"))?;
        // Cancel is error -128, not a failure.
        if !out.status.success() {
            let words = String::from_utf8_lossy(&out.stderr);
            return if words.contains("-128") { Ok(Vec::new()) } else { Err(format!("the Open dialog failed: {}", words.trim())) };
        }
        Ok(super::chosen_lines(&out.stdout))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod platform {
    use std::path::{Path, PathBuf};

    pub fn open(folder: Option<&Path>, kind: &super::Kind) -> Result<Vec<PathBuf>, String> {
        for (program, args) in super::linux_commands(folder, kind) {
            let Ok(out) = std::process::Command::new(program).args(&args).output() else { continue };
            // Both exit 1 on Cancel, printing nothing.
            return Ok(if out.status.success() { super::chosen_lines(&out.stdout) } else { Vec::new() });
        }
        Err(super::NO_DIALOG.into())
    }
}

/// What a Linux without zenity or kdialog says.
#[cfg_attr(any(windows, target_os = "macos"), allow(dead_code))]
pub const NO_DIALOG: &str = "no Open dialog here (install zenity or kdialog)";

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use super::*;

    fn name() -> impl Strategy<Value = String> {
        "[a-zA-Z0-9 _ÆØé.-]{1,12}".prop_filter("a name", |s| !s.trim().is_empty())
    }

    proptest! {
        /// The Windows buffer reads back as the files picked: one path as
        /// it is, several as the folder joined with each name, in order,
        /// and nothing past the two NULs that end it.
        #[test]
        fn the_windows_buffer_reads_back_as_the_files_picked(folder in "[C-F]:\\\\[a-z ]{1,8}\\\\[a-z]{1,6}", names in prop::collection::vec(name(), 1..6), junk in "[a-z]{0,5}") {
            let mut buffer: Vec<u16> = Vec::new();
            let expected: Vec<PathBuf> = names.iter().map(|n| Path::new(&folder).join(n)).collect();
            if names.len() == 1 {
                buffer.extend(expected[0].to_string_lossy().encode_utf16());
                buffer.push(0);
            } else {
                buffer.extend(folder.encode_utf16());
                buffer.push(0);
                for n in &names {
                    buffer.extend(n.encode_utf16());
                    buffer.push(0);
                }
            }
            buffer.push(0);
            // What was in the buffer before, past the end.
            buffer.extend(junk.encode_utf16());
            buffer.extend([0, 0, 0]);
            prop_assert_eq!(windows_chosen(&buffer), expected);
            prop_assert!(windows_chosen(&[0, 0, 0]).is_empty(), "a cancelled dialog's buffer");
        }

        /// What zenity, kdialog or osascript print reads back as the files
        /// picked, a line each, with either line ending.
        #[test]
        fn printed_lines_read_back_as_the_files_picked(paths in prop::collection::vec("/[a-z ]{1,8}/[a-zÆé .]{1,8}[a-z]", 0..6), crlf in any::<bool>()) {
            let printed = paths.iter().map(|p| format!("{p}{}", if crlf { "\r\n" } else { "\n" })).collect::<String>();
            prop_assert_eq!(chosen_lines(printed.as_bytes()), paths.iter().map(PathBuf::from).collect::<Vec<_>>());
        }

        /// The script and the commands ask for several files, of the
        /// kind's extensions, starting in the folder given.
        #[test]
        fn each_system_is_asked_for_several_files_of_the_kind(folder in proptest::option::of("/[a-z]{1,8}/[a-z \"]{1,8}")) {
            let start = folder.as_deref().map(Path::new);
            let script = applescript(start, &MEDIA);
            prop_assert!(script.contains("with multiple selections allowed"), "{}", script);
            prop_assert_eq!(script.contains("default location"), folder.is_some());
            if let Some(folder) = &folder {
                let location = format!("(POSIX file {})", applescript_string(folder));
                prop_assert!(script.contains(&location), "{}", script);
            }
            let [(zenity, z), (kdialog, k)] = linux_commands(start, &MEDIA);
            prop_assert_eq!((zenity, kdialog), ("zenity", "kdialog"));
            prop_assert!(z.contains(&"--multiple".to_owned()) && k.contains(&"--multiple".to_owned()) && k.contains(&"--separate-output".to_owned()));
            prop_assert_eq!(z.iter().any(|a| a.starts_with("--filename=")), folder.is_some());
            if let Some(folder) = &folder {
                let filename = format!("--filename={folder}/");
                prop_assert!(z.contains(&filename), "{:?}", z);
                prop_assert_eq!(&k[1], &format!("{folder}/"));
            }
            for (_, ext) in MEDIA.kinds {
                let quoted = format!("\"{ext}\"");
                let glob = format!("*.{ext}");
                prop_assert!(script.contains(&quoted), "{} is not asked for", ext);
                prop_assert!(z.iter().any(|a| a.contains(&glob)) && k[2].contains(&glob));
            }
        }
    }

    /// The dialog offers exactly what the importer sorts: nothing it would
    /// call unsupported, and nothing it takes left out.
    #[test]
    fn the_dialog_offers_what_the_importer_takes() {
        let offered: std::collections::BTreeSet<&str> = MEDIA.kinds.iter().map(|k| k.1).collect();
        let taken: std::collections::BTreeSet<&str> =
            crate::import_files::MEDIA.iter().chain(crate::import_files::STILLS).copied().collect();
        assert_eq!(offered, taken);
        let filter = windows_filter(&MEDIA);
        let rows: Vec<&str> = filter.split('\0').collect();
        assert_eq!(rows[0], MEDIA.files);
        assert_eq!(rows[1].split(';').count(), MEDIA.kinds.len());
        assert!(rows[1].split(';').all(|p| p.starts_with("*.")));
        assert_eq!(&rows[2..], ["All files", "*.*", "", ""]);
    }
}
