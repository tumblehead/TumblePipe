//! Embeds Cinedex's icon (`icon/cinedex.ico`, drawn by
//! `scripts/cinedex_icon.py`) in the Windows executable, as its first icon
//! resource: what Explorer and the taskbar show for `cinedex.exe`, and what
//! `src/app_icon.rs` loads onto its windows. Nothing elsewhere: Linux and
//! macOS executables carry no icon (`src/app_icon.rs` sets theirs at run
//! time).

fn main() {
    println!("cargo:rerun-if-changed=icon/cinedex.rc");
    println!("cargo:rerun-if-changed=icon/cinedex.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("icon/cinedex.rc", embed_resource::NONE).manifest_optional().expect("compiling the icon resource");
    }
}
