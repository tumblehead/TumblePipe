//! Embeds Clean Up Project's icon (`icon/cleanup.ico`, drawn by
//! `scripts/cleanup_icon.py`) in the Windows executable, as its first icon
//! resource: what Explorer and the taskbar show for `cleanup.exe`, and what
//! `src/app_icon.rs` loads onto its window. Nothing elsewhere: Linux and
//! macOS executables carry no icon (`src/app_icon.rs` sets theirs at run
//! time). Cinedex's `build.rs` does the same for its icon.

fn main() {
    println!("cargo:rerun-if-changed=icon/cleanup.rc");
    println!("cargo:rerun-if-changed=icon/cleanup.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("icon/cleanup.rc", embed_resource::NONE).manifest_optional().expect("compiling the icon resource");
    }
}
