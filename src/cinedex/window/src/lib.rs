//! An app's own windows through the OS, for what Primer (which opens them)
//! has no call for: the window icon ([`app_icon`]), shared by Cinedex and
//! Clean Up, and on X11 the lookup of this process's windows ([`x11`]),
//! which Cinedex's Live view raise uses as well. Links no FFmpeg, OCIO or
//! GUI, so it builds and tests anywhere.

pub mod app_icon;
#[cfg(all(unix, not(target_os = "macos")))]
pub mod x11;
