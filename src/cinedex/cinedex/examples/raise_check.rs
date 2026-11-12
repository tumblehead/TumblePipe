//! Checks the Live view raise (`src/raise.rs`) on a real desktop: opens a
//! bare window, optionally minimises it, and after `--after` seconds (time
//! to put another app's window over it) raises it as a Live view does,
//! then says what the desktop shows: on X11 whether the window is on top of
//! the stacking order and whether the keyboard focus stayed where it was,
//! on macOS whether the window is visible and the app still inactive.
//!
//! `cargo run -p cinedex --example raise_check -- [--after SECS] [--minimise]`
//! Exits by itself two seconds after the raise; it prints `RAISE ...` lines.
//! With `--cover` it is the other app instead: a window over the whole
//! screen that takes the focus and closes after `--after` seconds, raising
//! nothing.

#[path = "../src/raise.rs"]
mod raise;

use std::time::{Duration, Instant};

use winit::application::ApplicationHandler;
use winit::dpi::{LogicalPosition, LogicalSize};
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::{Window, WindowId};

struct Check {
    window: Option<Window>,
    start: Instant,
    after: Duration,
    minimise: bool,
    minimised: bool,
    cover: bool,
    raised: bool,
    done: bool,
}

impl ApplicationHandler for Check {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_none() {
            let attributes = if self.cover {
                Window::default_attributes()
                    .with_title("raise check cover")
                    .with_position(LogicalPosition::new(0.0, 0.0))
                    .with_inner_size(LogicalSize::new(5000.0, 5000.0))
            } else {
                Window::default_attributes().with_title("cinedex raise check").with_inner_size(LogicalSize::new(640.0, 480.0))
            };
            let window = event_loop.create_window(attributes).expect("a window");
            if self.cover {
                window.focus_window();
                take_front();
            }
            self.window = Some(window);
            self.start = Instant::now();
        }
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        if let WindowEvent::CloseRequested = event {
            event_loop.exit();
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let Some(window) = &self.window else { return };
        if self.done {
            return;
        }
        let elapsed = self.start.elapsed();
        if self.minimise && !self.minimised && elapsed >= Duration::from_secs(1) {
            window.set_minimized(true);
            self.minimised = true;
            println!("RAISE minimised the window");
        }
        if self.cover {
            if !self.minimised && elapsed >= Duration::from_millis(500) {
                // In front of the window under test (macOS refuses a shell
                // process's activation, so it is ordered front as the raise
                // does), so the raise has something to come out from under.
                self.minimised = true;
                println!("RAISE cover lifted: {:?}", raise::raise_without_focus());
            }
            if !self.raised && elapsed >= Duration::from_secs(1) {
                self.raised = true;
                println!("RAISE cover: {}", report());
            }
            if elapsed >= self.after {
                self.done = true;
                event_loop.exit();
            } else {
                event_loop.set_control_flow(ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100)));
            }
            return;
        }
        if !self.raised && elapsed >= self.after {
            self.raised = true;
            println!("RAISE before: {}", report());
            println!("RAISE result: {:?}", raise::raise_without_focus());
        }
        if self.raised && elapsed >= self.after + Duration::from_secs(2) {
            println!("RAISE after: {}", report());
            self.done = true;
            event_loop.exit();
            return;
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100)));
    }
}

/// What the desktop says of this process's window.
#[cfg(all(unix, not(target_os = "macos")))]
fn report() -> String {
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::{AtomEnum, ConnectionExt};
    let Ok((conn, screen)) = x11rb::connect(None) else { return "no X11 display".into() };
    let root = conn.setup().roots[screen].root;
    let atom = |name: &str| conn.intern_atom(false, name.as_bytes()).ok().and_then(|c| c.reply().ok()).map_or(0, |r| r.atom);
    let values = |window: u32, name: &str, kind: AtomEnum| -> Vec<u32> {
        conn.get_property(false, window, atom(name), kind, 0, u32::MAX)
            .ok()
            .and_then(|c| c.reply().ok())
            .and_then(|r| r.value32().map(|v| v.collect()))
            .unwrap_or_default()
    };
    let ours = raise::x11::windows_of(&conn, root, std::process::id()).unwrap_or_default();
    let stacking = values(root, "_NET_CLIENT_LIST_STACKING", AtomEnum::WINDOW);
    let active = values(root, "_NET_ACTIVE_WINDOW", AtomEnum::WINDOW);
    let (hidden, above) = (atom("_NET_WM_STATE_HIDDEN"), atom("_NET_WM_STATE_ABOVE"));
    let ours_hidden = ours.iter().any(|&w| values(w, "_NET_WM_STATE", AtomEnum::ATOM).contains(&hidden));
    let ours_above = ours.iter().any(|&w| values(w, "_NET_WM_STATE", AtomEnum::ATOM).contains(&above));
    format!(
        "ours={ours:?} top_of_stack={} hidden={ours_hidden} always_on_top={ours_above} focus_is_ours={} stacking={stacking:?} active={active:?}",
        stacking.last().is_some_and(|w| ours.contains(w)),
        active.first().is_some_and(|w| ours.contains(w)),
    )
}

/// What AppKit says of this process's window.
#[cfg(target_os = "macos")]
fn report() -> String {
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{class, msg_send};
    const NS_WINDOW_OCCLUSION_STATE_VISIBLE: usize = 1 << 1;
    // SAFETY: AppKit queries with their documented signatures, on the main
    // thread (winit's event loop).
    unsafe {
        let app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
        let active: Bool = msg_send![app, isActive];
        let windows: *mut AnyObject = msg_send![app, windows];
        let count: usize = msg_send![windows, count];
        let mut out = format!("app_active={} windows={count}", active.as_bool());
        for index in 0..count {
            let window: *mut AnyObject = msg_send![windows, objectAtIndex: index];
            let visible: Bool = msg_send![window, isVisible];
            let minimised: Bool = msg_send![window, isMiniaturized];
            let occlusion: usize = msg_send![window, occlusionState];
            let on_active_space: Bool = msg_send![window, isOnActiveSpace];
            let number: isize = msg_send![window, windowNumber];
            out += &format!(
                " [#{number} visible={} minimised={} occlusion_visible={} on_active_space={}]",
                visible.as_bool(),
                minimised.as_bool(),
                occlusion & NS_WINDOW_OCCLUSION_STATE_VISIBLE != 0,
                on_active_space.as_bool()
            );
        }
        out + &format!(" on_screen_front_to_back={:?}", on_screen_windows())
    }
}

/// The cover takes the front and the focus as an app the user switched to
/// would: a process started from a shell is not activated by itself.
#[cfg(target_os = "macos")]
fn take_front() {
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{class, msg_send};
    // SAFETY: AppKit messages with their documented signatures, on the main
    // thread.
    unsafe {
        let app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
        let _: () = msg_send![app, activateIgnoringOtherApps: Bool::YES];
    }
}

#[cfg(not(target_os = "macos"))]
fn take_front() {}

/// The on-screen windows of every app, front to back (CoreGraphics).
#[cfg(target_os = "macos")]
fn on_screen_windows() -> Vec<u32> {
    use std::ffi::c_void;
    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGWindowListCreate(option: u32, relative_to: u32) -> *const c_void;
    }
    #[link(name = "CoreFoundation", kind = "framework")]
    unsafe extern "C" {
        fn CFArrayGetCount(array: *const c_void) -> isize;
        fn CFArrayGetValueAtIndex(array: *const c_void, index: isize) -> *const c_void;
        fn CFRelease(object: *const c_void);
    }
    const ON_SCREEN_ONLY: u32 = 1;
    // SAFETY: a CFArray of CGWindowIDs stored as pointer-sized values,
    // released once read.
    unsafe {
        let list = CGWindowListCreate(ON_SCREEN_ONLY, 0);
        if list.is_null() {
            return vec![];
        }
        let ids = (0..CFArrayGetCount(list)).map(|i| CFArrayGetValueAtIndex(list, i) as usize as u32).collect();
        CFRelease(list);
        ids
    }
}

#[cfg(windows)]
fn report() -> String {
    "(no report on Windows)".into()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let after = args.iter().position(|a| a == "--after").and_then(|i| args.get(i + 1)).and_then(|s| s.parse().ok()).unwrap_or(6.0);
    let mut check = Check {
        window: None,
        start: Instant::now(),
        after: Duration::from_secs_f64(after),
        minimise: args.iter().any(|a| a == "--minimise"),
        minimised: false,
        cover: args.iter().any(|a| a == "--cover"),
        raised: false,
        done: false,
    };
    let event_loop = EventLoop::new().expect("an event loop");
    event_loop.run_app(&mut check).expect("the event loop ran");
}
