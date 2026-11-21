//! Clean Up Project's icon on its window (`icon/`, drawn by
//! `scripts/cleanup_icon.py`). Primer opens the window and has no window
//! icon to give it, so this goes to the OS for the process's own windows,
//! as Cinedex's `app_icon.rs` does (this is that module, with its X11
//! window lookup brought in from Cinedex's `raise.rs`):
//!
//! - Windows: the executable's icon resource (`build.rs`) set on each of
//!   the process's top-level windows (`WM_SETICON`, big and small: the
//!   taskbar and the title bar) and on their window class. The message is
//!   posted, not sent, so it never waits on the window's thread;
//! - Linux on X11: `_NET_WM_ICON` (32, 64 and 128 px) on the client windows
//!   whose `_NET_WM_PID` is this process. On Wayland a client cannot set
//!   its icon: the compositor shows the icon of the `.desktop` file named
//!   after the app id (`cleanup`), which TumblePipe does not install;
//! - macOS: the Dock icon (`NSApplication.applicationIconImage`): `cleanup`
//!   is a bare executable, not an app bundle with an icon of its own.
//!
//! [`apply`] is called from the window's view until it has found a window
//! (the window manager may not list it on the first frames), then does
//! nothing.

use std::sync::atomic::{AtomicBool, Ordering};

/// Set once the icon is on a window (and so on their class).
static DONE: AtomicBool = AtomicBool::new(false);

/// The icon as PNGs, for the platforms that take pixels.
#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub(crate) const PNG_32: &[u8] = include_bytes!("../icon/cleanup-32.png");
#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub(crate) const PNG_64: &[u8] = include_bytes!("../icon/cleanup-64.png");
#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub(crate) const PNG_128: &[u8] = include_bytes!("../icon/cleanup-128.png");
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) const PNG_256: &[u8] = include_bytes!("../icon/cleanup-256.png");

/// Put the icon on this process's windows, once there are any.
pub(crate) fn apply() {
    if DONE.load(Ordering::Relaxed) {
        return;
    }
    match set() {
        Ok(0) => {}
        Ok(_) => DONE.store(true, Ordering::Relaxed),
        Err(e) => {
            eprintln!("cleanup: no window icon: {e}");
            DONE.store(true, Ordering::Relaxed);
        }
    }
}

/// A PNG's size and straight RGBA pixels.
#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub(crate) fn rgba(png_bytes: &[u8]) -> Result<(u32, u32, Vec<u8>), String> {
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
#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub(crate) fn net_wm_icon(images: &[(u32, u32, Vec<u8>)]) -> Vec<u32> {
    let mut out = Vec::new();
    for (w, h, rgba) in images {
        out.extend([*w, *h]);
        out.extend(rgba.as_chunks::<4>().0.iter().map(|p| u32::from_be_bytes([p[3], p[0], p[1], p[2]])));
    }
    out
}

/// Set the icon on the windows open now: how many.
#[cfg(windows)]
fn set() -> Result<usize, String> {
    windows::set()
}

#[cfg(target_os = "macos")]
fn set() -> Result<usize, String> {
    macos::set()
}

#[cfg(all(unix, not(target_os = "macos")))]
fn set() -> Result<usize, String> {
    if std::env::var_os("DISPLAY").is_none() {
        // A native Wayland window: its icon is the compositor's to find.
        return Err("on Wayland the window icon comes from a cleanup.desktop file".into());
    }
    x11::set()
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
    /// The icon resource `build.rs` embeds (`icon/cleanup.rc`).
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
    use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _, PropMode, Window};
    use x11rb::wrapper::ConnectionExt as _;

    fn atom(conn: &impl Connection, name: &str) -> Result<u32, String> {
        let reply = conn.intern_atom(false, name.as_bytes()).map_err(|e| e.to_string())?.reply().map_err(|e| e.to_string())?;
        Ok(reply.atom)
    }

    /// The 32-bit values of `window`'s property `name` (empty when unset).
    fn property32(conn: &impl Connection, window: Window, name: u32, kind: AtomEnum) -> Result<Vec<u32>, String> {
        let reply =
            conn.get_property(false, window, name, kind, 0, u32::MAX).map_err(|e| e.to_string())?.reply().map_err(|e| e.to_string())?;
        Ok(reply.value32().map(|v| v.collect()).unwrap_or_default())
    }

    /// The managed client windows of process `pid` on the X server, by the
    /// window manager's client list.
    fn windows_of(conn: &impl Connection, root: Window, pid: u32) -> Result<Vec<Window>, String> {
        let (clients, wm_pid) = (atom(conn, "_NET_CLIENT_LIST")?, atom(conn, "_NET_WM_PID")?);
        let mut found = Vec::new();
        for window in property32(conn, root, clients, AtomEnum::WINDOW)? {
            if property32(conn, window, wm_pid, AtomEnum::CARDINAL)?.first() == Some(&pid) {
                found.push(window);
            }
        }
        Ok(found)
    }

    pub(super) fn set() -> Result<usize, String> {
        let (conn, screen) = x11rb::connect(None).map_err(|e| format!("X11: {e}"))?;
        let root = conn.setup().roots[screen].root;
        let windows = windows_of(&conn, root, std::process::id())?;
        if windows.is_empty() {
            return Ok(0);
        }
        let images = [super::PNG_32, super::PNG_64, super::PNG_128].iter().map(|png| super::rgba(png)).collect::<Result<Vec<_>, _>>()?;
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

    /// AppKit is main-thread only: the window's view runs on the event
    /// loop's (main) thread.
    pub(super) fn set() -> Result<usize, String> {
        let png = super::PNG_256;
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

    /// The icons embedded read as square 8-bit RGBA of their size, with
    /// the sweep's jade in them.
    #[test]
    fn the_embedded_icons_read() {
        for (png, size) in [(PNG_32, 32), (PNG_64, 64), (PNG_128, 128), (PNG_256, 256)] {
            let (w, h, pixels) = rgba(png).unwrap();
            assert_eq!((w, h), (size, size));
            assert_eq!(pixels.len(), (size * size * 4) as usize);
            assert!(pixels.as_chunks::<4>().0.contains(&[43, 174, 134, 255]), "{size} px has the accent");
            assert_eq!(&pixels[..4], &[0, 0, 0, 0], "{size} px corners are clear");
        }
        assert!(rgba(b"not a png").is_err());
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

    /// The Windows executable carries the icon resource `build.rs` embeds.
    #[cfg(windows)]
    #[test]
    fn the_executable_resource_is_the_icon() {
        let rc = include_str!("../icon/cleanup.rc");
        assert!(rc.lines().any(|l| l.trim() == "1 ICON \"cleanup.ico\""));
        let ico = include_bytes!("../icon/cleanup.ico");
        assert_eq!(&ico[..4], &[0, 0, 1, 0], "an icon file");
        let count = u16::from_le_bytes([ico[4], ico[5]]);
        assert!(count >= 6, "{count} sizes");
        let sizes: Vec<u32> = (0..count as usize).map(|i| ico[6 + 16 * i]).map(|b| if b == 0 { 256 } else { u32::from(b) }).collect();
        assert!(sizes.contains(&16) && sizes.contains(&32) && sizes.contains(&256), "{sizes:?}");
    }
}
