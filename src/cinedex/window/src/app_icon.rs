//! An app's icon on its windows. Primer opens the windows and has no
//! window icon to give them, so this goes to the OS for the process's own
//! windows:
//!
//! - Windows: the executable's icon resource (the app's `build.rs` embeds
//!   it as resource 1) set on each of the process's top-level windows
//!   (`WM_SETICON`, big and small: the taskbar and the title bar) and on
//!   their window class, so windows opened later have it too. The message
//!   is posted, not sent: sent from a thread other than the window's while
//!   that thread waits on this one, it would never return (it hung
//!   Cinedex's window when it was sent);
//! - Linux on X11: `_NET_WM_ICON` (32, 64 and 128 px) on the client windows
//!   whose `_NET_WM_PID` is this process. On Wayland a client cannot set
//!   its icon: the compositor shows the icon of the `.desktop` file named
//!   after the app id ([`Icon::app`]), which TumblePipe does not install;
//! - macOS: the Dock icon (`NSApplication.applicationIconImage`): the apps
//!   are bare executables, not app bundles with an icon of their own.
//!
//! [`apply`] is called from the app's window updates until it has found a
//! window (they may not be open, or listed by the window manager, when the
//! first update runs), then does nothing.

use std::sync::atomic::{AtomicBool, Ordering};

/// Set once the icon is on a window (and so on their class).
static DONE: AtomicBool = AtomicBool::new(false);

/// An app's icon: its app id (the executable's name, and on Wayland the
/// `.desktop` file's), and its PNGs for the platforms that take pixels.
pub struct Icon {
    pub app: &'static str,
    /// 32, 64 and 128 px: X11's `_NET_WM_ICON`.
    pub png_32: &'static [u8],
    pub png_64: &'static [u8],
    pub png_128: &'static [u8],
    /// 256 px: the macOS Dock.
    pub png_256: &'static [u8],
}

/// Put `icon` on this process's windows, once there are any.
pub fn apply(icon: &Icon) {
    if DONE.load(Ordering::Relaxed) {
        return;
    }
    match set(icon) {
        Ok(0) => {}
        Ok(_) => DONE.store(true, Ordering::Relaxed),
        Err(e) => {
            eprintln!("{}: no window icon: {e}", icon.app);
            DONE.store(true, Ordering::Relaxed);
        }
    }
}

/// A PNG's size and straight RGBA pixels; only 8-bit RGBA reads.
pub fn rgba(png_bytes: &[u8]) -> Result<(u32, u32, Vec<u8>), String> {
    let decoder = png::Decoder::new(std::io::Cursor::new(png_bytes));
    let mut reader = decoder.read_info().map_err(|e| e.to_string())?;
    let mut buf = vec![0; reader.output_buffer_size().ok_or("the icon is too large")?];
    let info = reader.next_frame(&mut buf).map_err(|e| e.to_string())?;
    if info.color_type != png::ColorType::Rgba || info.bit_depth != png::BitDepth::Eight {
        return Err(format!("the icon is {:?} {:?}, not 8-bit RGBA", info.color_type, info.bit_depth));
    }
    buf.truncate(info.buffer_size());
    Ok((info.width, info.height, buf))
}

/// `_NET_WM_ICON`'s value for `images`: each image's width, height, then
/// its pixels as ARGB, one 32-bit cardinal each, rows top first.
pub fn net_wm_icon(images: &[(u32, u32, Vec<u8>)]) -> Vec<u32> {
    let mut out = Vec::new();
    for (w, h, rgba) in images {
        out.extend([*w, *h]);
        out.extend(rgba.as_chunks::<4>().0.iter().map(|p| u32::from_be_bytes([p[3], p[0], p[1], p[2]])));
    }
    out
}

/// Set the icon on the windows open now: how many.
#[cfg(windows)]
fn set(_icon: &Icon) -> Result<usize, String> {
    windows::set()
}

#[cfg(target_os = "macos")]
fn set(icon: &Icon) -> Result<usize, String> {
    macos::set(icon.png_256)
}

#[cfg(all(unix, not(target_os = "macos")))]
fn set(icon: &Icon) -> Result<usize, String> {
    if std::env::var_os("DISPLAY").is_none() {
        // A native Wayland window: its icon is the compositor's to find.
        return Err(format!("on Wayland the window icon comes from a {}.desktop file", icon.app));
    }
    x11::set(&[icon.png_32, icon.png_64, icon.png_128])
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;

    type Hwnd = *mut c_void;
    type Handle = *mut c_void;
    const IMAGE_ICON: u32 = 1;
    const LR_DEFAULTCOLOR: u32 = 0;
    const SM_CXICON: i32 = 11;
    const SM_CXSMICON: i32 = 49;
    const WM_SETICON: u32 = 0x0080;
    const ICON_SMALL: usize = 0;
    const ICON_BIG: usize = 1;
    const GCLP_HICON: i32 = -14;
    const GCLP_HICONSM: i32 = -34;
    const GW_OWNER: u32 = 4;
    /// The icon resource the app's `build.rs` embeds (`1 ICON` in its
    /// `icon/<app>.rc`).
    const ICON_ID: usize = 1;

    #[link(name = "user32")]
    unsafe extern "system" {
        fn EnumWindows(callback: unsafe extern "system" fn(Hwnd, isize) -> i32, param: isize) -> i32;
        fn GetWindowThreadProcessId(hwnd: Hwnd, pid: *mut u32) -> u32;
        fn GetWindow(hwnd: Hwnd, cmd: u32) -> Hwnd;
        fn LoadImageW(instance: Handle, name: *const u16, kind: u32, cx: i32, cy: i32, flags: u32) -> Handle;
        fn GetSystemMetrics(index: i32) -> i32;
        fn PostMessageW(hwnd: Hwnd, message: u32, wparam: usize, lparam: isize) -> i32;
        fn SetClassLongPtrW(hwnd: Hwnd, index: i32, value: isize) -> usize;
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetModuleHandleW(name: *const u16) -> Handle;
    }

    /// The top-level, unowned windows of this process, shown or not yet.
    fn own_windows() -> Vec<Hwnd> {
        unsafe extern "system" fn each(hwnd: Hwnd, param: isize) -> i32 {
            // SAFETY: `param` is the Vec passed below, alive for the call.
            let found = unsafe { &mut *(param as *mut Vec<Hwnd>) };
            let mut pid = 0u32;
            // SAFETY: plain Win32 queries on a handle EnumWindows gave.
            unsafe {
                GetWindowThreadProcessId(hwnd, &mut pid);
                if pid == std::process::id() && GetWindow(hwnd, GW_OWNER).is_null() {
                    found.push(hwnd);
                }
            }
            1
        }
        let mut found: Vec<Hwnd> = Vec::new();
        // SAFETY: the callback only reads `found` through `param` while
        // EnumWindows runs.
        unsafe {
            EnumWindows(each, &mut found as *mut Vec<Hwnd> as isize);
        }
        found
    }

    /// The executable's icon at the system's size `metric`.
    fn icon(metric: i32) -> Result<Handle, String> {
        // SAFETY: the executable's own module, and an integer resource id
        // (MAKEINTRESOURCE) for the name, as LoadImageW documents.
        let handle = unsafe {
            let size = GetSystemMetrics(metric);
            LoadImageW(GetModuleHandleW(std::ptr::null()), ICON_ID as *const u16, IMAGE_ICON, size, size, LR_DEFAULTCOLOR)
        };
        if handle.is_null() { Err("the executable has no icon resource".into()) } else { Ok(handle) }
    }

    pub(super) fn set() -> Result<usize, String> {
        let windows = own_windows();
        if windows.is_empty() {
            return Ok(0);
        }
        let (big, small) = (icon(SM_CXICON)?, icon(SM_CXSMICON)?);
        for &hwnd in &windows {
            // SAFETY: this process's own windows, and icons that live as
            // long as the process (LoadImageW's, never destroyed).
            unsafe {
                PostMessageW(hwnd, WM_SETICON, ICON_BIG, big as isize);
                PostMessageW(hwnd, WM_SETICON, ICON_SMALL, small as isize);
                SetClassLongPtrW(hwnd, GCLP_HICON, big as isize);
                SetClassLongPtrW(hwnd, GCLP_HICONSM, small as isize);
            }
        }
        Ok(windows.len())
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod x11 {
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::{AtomEnum, PropMode};
    use x11rb::wrapper::ConnectionExt as _;

    use crate::x11::{atom, windows_of};

    /// `pngs` as `_NET_WM_ICON` on this process's client windows.
    pub(super) fn set(pngs: &[&[u8]]) -> Result<usize, String> {
        let (conn, screen) = x11rb::connect(None).map_err(|e| format!("X11: {e}"))?;
        let root = conn.setup().roots[screen].root;
        let windows = windows_of(&conn, root, std::process::id())?;
        if windows.is_empty() {
            return Ok(0);
        }
        let images = pngs.iter().map(|png| super::rgba(png)).collect::<Result<Vec<_>, _>>()?;
        let data = super::net_wm_icon(&images);
        let x = |e: x11rb::errors::ConnectionError| format!("X11: {e}");
        let icon = atom(&conn, "_NET_WM_ICON").map_err(|e| format!("X11: {e}"))?;
        for &window in &windows {
            conn.change_property32(PropMode::REPLACE, window, icon, AtomEnum::CARDINAL, &data).map_err(x)?;
        }
        conn.flush().map_err(x)?;
        Ok(windows.len())
    }
}

#[cfg(target_os = "macos")]
mod macos {
    use std::ffi::c_void;

    use objc2::runtime::AnyObject;
    use objc2::{class, msg_send};

    /// AppKit is main-thread only: the apps' window updates run on the
    /// event loop's (main) thread.
    pub(super) fn set(png: &[u8]) -> Result<usize, String> {
        // SAFETY: AppKit and Foundation messages with their documented
        // signatures, on the main thread. The image is retained by the
        // application once set; the one reference made here is leaked, once.
        unsafe {
            let app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            if app.is_null() {
                return Err("no NSApplication".into());
            }
            let data: *mut AnyObject = msg_send![class!(NSData), dataWithBytes: png.as_ptr().cast::<c_void>(), length: png.len()];
            if data.is_null() {
                return Err("the icon's data".into());
            }
            let image: *mut AnyObject = msg_send![class!(NSImage), alloc];
            let image: *mut AnyObject = msg_send![image, initWithData: data];
            if image.is_null() {
                return Err("the icon is not an image AppKit reads".into());
            }
            let _: () = msg_send![app, setApplicationIconImage: image];
        }
        Ok(1)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each app's icon (drawn by `scripts/<app>_icon.py`) and the colour
    /// its accent is: Cinedex's playhead amber, Clean Up's sweep jade.
    const APPS: [(Icon, [u8; 4]); 2] = [
        (
            Icon {
                app: "cinedex",
                png_32: include_bytes!("../../cinedex/icon/cinedex-32.png"),
                png_64: include_bytes!("../../cinedex/icon/cinedex-64.png"),
                png_128: include_bytes!("../../cinedex/icon/cinedex-128.png"),
                png_256: include_bytes!("../../cinedex/icon/cinedex-256.png"),
            },
            [240, 180, 60, 255],
        ),
        (
            Icon {
                app: "cleanup",
                png_32: include_bytes!("../../cleanup-app/icon/cleanup-32.png"),
                png_64: include_bytes!("../../cleanup-app/icon/cleanup-64.png"),
                png_128: include_bytes!("../../cleanup-app/icon/cleanup-128.png"),
                png_256: include_bytes!("../../cleanup-app/icon/cleanup-256.png"),
            },
            [43, 174, 134, 255],
        ),
    ];

    /// Every app's PNGs read as square 8-bit RGBA of their size, with its
    /// accent in them and clear corners.
    #[test]
    fn the_icons_read() {
        for (icon, accent) in &APPS {
            for (png, size) in [(icon.png_32, 32), (icon.png_64, 64), (icon.png_128, 128), (icon.png_256, 256)] {
                let (w, h, pixels) = rgba(png).unwrap();
                assert_eq!((w, h), (size, size), "{}", icon.app);
                assert_eq!(pixels.len(), (size * size * 4) as usize);
                assert!(pixels.as_chunks::<4>().0.contains(accent), "{} {size} px has the accent", icon.app);
                assert_eq!(&pixels[..4], &[0, 0, 0, 0], "{} {size} px corners are clear", icon.app);
            }
        }
        assert!(rgba(b"not a png").is_err());
    }

    /// A PNG of `color` at `depth`, 2 x 2, every byte 7.
    fn png_of(color: png::ColorType, depth: png::BitDepth) -> Vec<u8> {
        let mut out = Vec::new();
        let mut encoder = png::Encoder::new(&mut out, 2, 2);
        encoder.set_color(color);
        encoder.set_depth(depth);
        let samples = match color {
            png::ColorType::Grayscale => 1,
            png::ColorType::Rgb => 3,
            _ => 4,
        };
        let bytes = if depth == png::BitDepth::Sixteen { 2 } else { 1 };
        let mut writer = encoder.write_header().unwrap();
        writer.write_image_data(&vec![7; 2 * 2 * samples * bytes]).unwrap();
        writer.finish().unwrap();
        out
    }

    /// Only 8-bit RGBA reads: the pixels go out as RGBA quads as they are.
    #[test]
    fn rgba_reads_only_8_bit_rgba() {
        assert_eq!(rgba(&png_of(png::ColorType::Rgba, png::BitDepth::Eight)).unwrap(), (2, 2, vec![7; 16]));
        for (color, depth) in [
            (png::ColorType::Rgb, png::BitDepth::Eight),
            (png::ColorType::Grayscale, png::BitDepth::Eight),
            (png::ColorType::Rgba, png::BitDepth::Sixteen),
        ] {
            assert!(rgba(&png_of(color, depth)).is_err(), "{color:?} {depth:?}");
        }
    }

    #[test]
    fn net_wm_icon_is_sizes_then_argb() {
        let data = net_wm_icon(&[(1, 2, vec![10, 20, 30, 40, 1, 2, 3, 255])]);
        assert_eq!(data, [1, 2, 0x280a141e, 0xff010203]);
    }

    use proptest::prelude::*;

    fn image() -> impl Strategy<Value = (u32, u32, Vec<u8>)> {
        (1u32..6, 1u32..6).prop_flat_map(|(w, h)| (Just(w), Just(h), proptest::collection::vec(any::<u8>(), (w * h * 4) as usize)))
    }

    proptest! {
        /// Any images pack as width, height, then one ARGB cardinal per
        /// pixel in order, and every pixel unpacks to its RGBA bytes.
        #[test]
        fn net_wm_icon_packs_every_pixel(images in proptest::collection::vec(image(), 0..4)) {
            let data = net_wm_icon(&images);
            prop_assert_eq!(data.len(), images.iter().map(|(w, h, _)| 2 + (w * h) as usize).sum::<usize>());
            let mut at = 0;
            for (w, h, px) in &images {
                prop_assert_eq!((data[at], data[at + 1]), (*w, *h));
                at += 2;
                for p in px.as_chunks::<4>().0 {
                    let v = data[at];
                    prop_assert_eq!([(v >> 16) as u8, (v >> 8) as u8, v as u8, (v >> 24) as u8], *p);
                    at += 1;
                }
            }
        }
    }

    /// Each Windows executable carries its icon as the resource its
    /// `build.rs` embeds, the one `windows::set` loads (id 1).
    #[test]
    fn the_executable_resources_are_the_icons() {
        let apps: [(&str, &str, &[u8]); 2] = [
            ("cinedex", include_str!("../../cinedex/icon/cinedex.rc"), include_bytes!("../../cinedex/icon/cinedex.ico")),
            ("cleanup", include_str!("../../cleanup-app/icon/cleanup.rc"), include_bytes!("../../cleanup-app/icon/cleanup.ico")),
        ];
        for (app, rc, ico) in apps {
            assert!(rc.lines().any(|l| l.trim() == format!("1 ICON \"{app}.ico\"")), "{app}.rc");
            assert_eq!(&ico[..4], &[0, 0, 1, 0], "{app}.ico is an icon file");
            let count = u16::from_le_bytes([ico[4], ico[5]]);
            assert!(count >= 6, "{app}: {count} sizes");
            let sizes: Vec<u32> = (0..count as usize).map(|i| ico[6 + 16 * i]).map(|b| if b == 0 { 256 } else { u32::from(b) }).collect();
            assert!(sizes.contains(&16) && sizes.contains(&32) && sizes.contains(&256), "{app}: {sizes:?}");
        }
    }
}
