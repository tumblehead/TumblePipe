//! Clean Up Project's icon on its window (`icon/`, drawn by
//! `scripts/cleanup_icon.py`; `build.rs` embeds `icon/cleanup.ico` as the
//! Windows executable's icon resource). [`th_window::app_icon`] puts it on
//! the window, per platform, as it does Cinedex's.

use th_window::app_icon::Icon;

const ICON: Icon = Icon {
    app: "cleanup",
    png_32: include_bytes!("../icon/cleanup-32.png"),
    png_64: include_bytes!("../icon/cleanup-64.png"),
    png_128: include_bytes!("../icon/cleanup-128.png"),
    png_256: include_bytes!("../icon/cleanup-256.png"),
};

/// Put the icon on this process's window: called from the window's view
/// until there is one (the window manager may not list it on the first
/// frames), then it does nothing.
pub(crate) fn apply() {
    th_window::app_icon::apply(&ICON);
}
