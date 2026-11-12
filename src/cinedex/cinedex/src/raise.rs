//! Bring this process's windows to the front, restoring a minimised one,
//! without taking the keyboard focus where the OS allows it: a Live view
//! asked for from Houdini shows the editor over Houdini while the animator
//! goes on typing there (`crate::live_view`).
//!
//! Primer owns the windows (and their winit handles), so this goes to the
//! OS for the process's own top-level windows:
//!
//! - Windows: topmost and back (`SetWindowPos`), without activating;
//! - macOS: `orderFrontRegardless` on each of the app's windows, which
//!   orders a window of an inactive app in front without activating it;
//! - Linux on X11: the client windows whose `_NET_WM_PID` is this process,
//!   lifted by turning `_NET_WM_STATE_ABOVE` on and, a moment later, off
//!   again, without the focus (an activation, `_NET_ACTIVE_WINDOW`, would
//!   take it). A minimised one is activated: GNOME's mutter restores a
//!   minimised window no other way. On mutter (the Linux build worker,
//!   GNOME 46 on X11, 2026-11-12) `_NET_RESTACK_WINDOW` and a plain
//!   restack raised nothing, and ABOVE turned off with no pause left the
//!   window where it was. The requests and their pauses (0.4 s) run on a
//!   thread of their own with an X connection of its own, so the editor
//!   goes on playing meanwhile.
//! - Linux on Wayland (the editor a native Wayland client, so no X11
//!   window of this process): a client cannot raise itself. This says so
//!   ([`Raise::Compositor`]) and the editor asks the compositor for
//!   attention through Primer (`primer::Command::request_attention`, an
//!   xdg-activation request): GNOME Shell 46 shows a "“<title>” is ready"
//!   notification that raises the window when clicked.
//!
//! Also compiled into `examples/raise_check.rs`, which checks it on a real
//! desktop (the build workers).

/// What came of a raise.
#[derive(Debug, PartialEq, Eq)]
pub enum Raise {
    /// This many windows were raised (on X11: are being raised).
    Raised(usize),
    /// The windows are native Wayland ones, which only the compositor can
    /// bring forward: ask it, through the window.
    #[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
    Compositor,
}

/// Raise this process's windows: how many were raised, or why none was.
#[cfg(windows)]
pub fn raise_without_focus() -> Result<Raise, String> {
    Ok(Raise::Raised(windows::raise_without_focus()))
}

/// Raise this process's windows: how many were raised, or why none was.
#[cfg(target_os = "macos")]
pub fn raise_without_focus() -> Result<Raise, String> {
    macos::raise_without_focus().map(Raise::Raised)
}

/// Raise this process's windows: how many were raised, or why none was.
/// With no X11 window of this process to raise and a Wayland display, the
/// windows are Wayland ones: [`Raise::Compositor`].
#[cfg(all(unix, not(target_os = "macos")))]
pub fn raise_without_focus() -> Result<Raise, String> {
    match x11::raise_without_focus() {
        Ok(count) => Ok(Raise::Raised(count)),
        Err(_) if std::env::var_os("WAYLAND_DISPLAY").is_some() => Ok(Raise::Compositor),
        Err(e) => Err(e),
    }
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;

    type Hwnd = *mut c_void;
    const SW_SHOWNOACTIVATE: i32 = 4;
    const SWP_NOSIZE: u32 = 0x0001;
    const SWP_NOMOVE: u32 = 0x0002;
    const SWP_NOACTIVATE: u32 = 0x0010;
    const SWP_SHOWWINDOW: u32 = 0x0040;
    const HWND_TOPMOST: isize = -1;
    const HWND_NOTOPMOST: isize = -2;

    #[link(name = "user32")]
    unsafe extern "system" {
        fn EnumWindows(callback: unsafe extern "system" fn(Hwnd, isize) -> i32, param: isize) -> i32;
        fn GetWindowThreadProcessId(hwnd: Hwnd, pid: *mut u32) -> u32;
        fn IsWindowVisible(hwnd: Hwnd) -> i32;
        fn IsIconic(hwnd: Hwnd) -> i32;
        fn GetWindow(hwnd: Hwnd, cmd: u32) -> Hwnd;
        fn ShowWindow(hwnd: Hwnd, cmd: i32) -> i32;
        fn SetWindowPos(hwnd: Hwnd, after: isize, x: i32, y: i32, cx: i32, cy: i32, flags: u32) -> i32;
    }

    const GW_OWNER: u32 = 4;

    /// The top-level, unowned, visible windows of this process.
    fn own_windows() -> Vec<Hwnd> {
        unsafe extern "system" fn each(hwnd: Hwnd, param: isize) -> i32 {
            // SAFETY: `param` is the Vec passed below, alive for the call.
            let found = unsafe { &mut *(param as *mut Vec<Hwnd>) };
            let mut pid = 0u32;
            // SAFETY: plain Win32 queries on a handle EnumWindows gave.
            unsafe {
                GetWindowThreadProcessId(hwnd, &mut pid);
                if pid == std::process::id() && IsWindowVisible(hwnd) != 0 && GetWindow(hwnd, GW_OWNER).is_null() {
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

    pub(super) fn raise_without_focus() -> usize {
        let windows = own_windows();
        for &hwnd in &windows {
            // SAFETY: handles of this process's own windows. Topmost and
            // back lifts the window over the others without activating it
            // (activation from a background process is refused anyway).
            unsafe {
                if IsIconic(hwnd) != 0 {
                    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
                }
                let flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW;
                SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags);
                SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags);
            }
        }
        windows.len()
    }
}

#[cfg(target_os = "macos")]
mod macos {
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{class, msg_send};

    /// AppKit is main-thread only: the editor raises from its update, which
    /// runs on the event loop's (main) thread.
    pub(super) fn raise_without_focus() -> Result<usize, String> {
        let mut raised = 0;
        // SAFETY: AppKit messages with their documented signatures, sent on
        // the main thread; the window objects are the app's own, alive while
        // `windows` (the NSApp's array) is.
        unsafe {
            let app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            if app.is_null() {
                return Err("no NSApplication".into());
            }
            let windows: *mut AnyObject = msg_send![app, windows];
            if windows.is_null() {
                return Ok(0);
            }
            let count: usize = msg_send![windows, count];
            for index in 0..count {
                let window: *mut AnyObject = msg_send![windows, objectAtIndex: index];
                if window.is_null() {
                    continue;
                }
                let main: Bool = msg_send![window, canBecomeMainWindow];
                let visible: Bool = msg_send![window, isVisible];
                let minimised: Bool = msg_send![window, isMiniaturized];
                if !main.as_bool() || !(visible.as_bool() || minimised.as_bool()) {
                    continue;
                }
                if minimised.as_bool() {
                    let _: () = msg_send![window, deminiaturize: std::ptr::null_mut::<AnyObject>()];
                }
                let _: () = msg_send![window, orderFrontRegardless];
                raised += 1;
            }
        }
        Ok(raised)
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
pub(crate) mod x11 {
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::{AtomEnum, ClientMessageEvent, ConnectionExt, EventMask, Window};

    const NET_WM_STATE_REMOVE: u32 = 0;
    const NET_WM_STATE_ADD: u32 = 1;
    /// The request comes from an application (not a pager).
    const SOURCE_APPLICATION: u32 = 1;
    /// How long the window manager is given to act on a request before the
    /// next: GNOME's mutter, given none, left the window where it was. The
    /// raise thread waits this long (twice at most) on a Live view.
    const MOMENT: std::time::Duration = std::time::Duration::from_millis(200);

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
    pub fn windows_of(conn: &impl Connection, root: Window, pid: u32) -> Result<Vec<Window>, String> {
        let (clients, wm_pid) = (atom(conn, "_NET_CLIENT_LIST")?, atom(conn, "_NET_WM_PID")?);
        let mut found = Vec::new();
        for window in property32(conn, root, clients, AtomEnum::WINDOW)? {
            if property32(conn, window, wm_pid, AtomEnum::CARDINAL)?.first() == Some(&pid) {
                found.push(window);
            }
        }
        Ok(found)
    }

    /// Find this process's windows now (how many, or why none), then ask
    /// the window manager to lift them on a thread of its own, which owns
    /// the connection: the pauses between the requests do not hold up the
    /// caller (the editor's UI thread). What fails there goes to the log.
    pub(super) fn raise_without_focus() -> Result<usize, String> {
        let (conn, root, windows) = own_windows()?;
        let count = windows.len();
        std::thread::Builder::new()
            .name("cinedex-raise".into())
            .spawn(move || {
                if let Err(e) = lift(&conn, root, &windows) {
                    eprintln!("cinedex: Live view could not raise the window: {e}");
                }
            })
            .map_err(|e| format!("could not start the raise: {e}"))?;
        Ok(count)
    }

    /// A connection of its own to the X server, its root window, and this
    /// process's client windows on it.
    fn own_windows() -> Result<(x11rb::rust_connection::RustConnection, Window, Vec<Window>), String> {
        if std::env::var_os("DISPLAY").is_none() {
            return Err("no X11 display; on Wayland the editor cannot raise its own window".into());
        }
        let (conn, screen) = x11rb::connect(None).map_err(|e| format!("X11: {e}"))?;
        let root = conn.setup().roots[screen].root;
        let windows = windows_of(&conn, root, std::process::id())?;
        if windows.is_empty() {
            return Err("no X11 window of this process (a native Wayland window cannot be raised by the editor)".into());
        }
        Ok((conn, root, windows))
    }

    /// Lift `windows` (restoring a minimised one) on `conn`, a moment for
    /// the window manager after each request.
    fn lift(conn: &impl Connection, root: Window, windows: &[Window]) -> Result<(), String> {
        let (active, state) = (atom(conn, "_NET_ACTIVE_WINDOW")?, atom(conn, "_NET_WM_STATE")?);
        let (above, hidden) = (atom(conn, "_NET_WM_STATE_ABOVE")?, atom(conn, "_NET_WM_STATE_HIDDEN")?);
        let x = |e: x11rb::errors::ConnectionError| format!("X11: {e}");
        let mask = EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY;
        // A client message to the window manager about `window`, and a
        // moment for it to act on it before the next.
        let ask = |window: Window, kind: u32, data: [u32; 5]| -> Result<(), String> {
            conn.send_event(false, root, mask, ClientMessageEvent::new(32, window, kind, data)).map_err(x)?;
            conn.flush().map_err(x)?;
            std::thread::sleep(MOMENT);
            Ok(())
        };
        for &window in windows {
            if property32(conn, window, state, AtomEnum::ATOM)?.contains(&hidden) {
                // Minimised: only an activation brings it back.
                conn.map_window(window).map_err(x)?;
                ask(window, active, [SOURCE_APPLICATION, 0, 0, 0, 0])?;
            } else {
                ask(window, state, [NET_WM_STATE_ADD, above, 0, SOURCE_APPLICATION, 0])?;
                ask(window, state, [NET_WM_STATE_REMOVE, above, 0, SOURCE_APPLICATION, 0])?;
            }
        }
        Ok(())
    }
}
