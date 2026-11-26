//! Cinedex's icon on its windows (`icon/`, drawn by
//! `scripts/cinedex_icon.py`; `build.rs` embeds `icon/cinedex.ico` as the
//! Windows executable's icon resource). [`th_window::app_icon`] puts it on
//! them, per platform.

use th_window::app_icon::Icon;

const ICON: Icon = Icon {
    app: "cinedex",
    png_32: include_bytes!("../icon/cinedex-32.png"),
    png_64: include_bytes!("../icon/cinedex-64.png"),
    png_128: include_bytes!("../icon/cinedex-128.png"),
    png_256: include_bytes!("../icon/cinedex-256.png"),
};

/// Put the icon on this process's windows: called from the windows'
/// updates until there is one (they may not be open when the first update
/// runs), then it does nothing.
pub(crate) fn apply() {
    th_window::app_icon::apply(&ICON);
}
