//! Raw bindings to the player's vendored, decode-only FFmpeg (see
//! `.ci/build_ffmpeg.py`). Generated per platform by bindgen and committed;
//! regenerate with `cargo build -p th_ffmpeg_sys --features regenerate`.
#![allow(non_upper_case_globals, non_camel_case_types, non_snake_case, unsafe_op_in_unsafe_fn, unnecessary_transmutes)]
#![allow(clippy::all, clippy::pedantic)]

#[cfg(all(windows, target_arch = "x86_64"))]
include!("bindings/windows-x86_64.rs");
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
include!("bindings/linux-x86_64.rs");
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
include!("bindings/macos-aarch64.rs");

/// FFmpeg's `AVERROR(e)`: a POSIX errno as a negative return code.
pub const fn averror(errno: i32) -> i32 {
    -errno
}

/// `AVERROR_EOF`: FFERRTAG('E','O','F',' ').
pub const AVERROR_EOF: i32 = -((b'E' as i32) | ((b'O' as i32) << 8) | ((b'F' as i32) << 16) | ((b' ' as i32) << 24));

/// `AVERROR(EAGAIN)`: EAGAIN is 11 on Windows (MSVC CRT) and Linux, 35 on macOS.
pub const AVERROR_EAGAIN: i32 = averror(if cfg!(target_os = "macos") { 35 } else { 11 });

/// `AV_NOPTS_VALUE`: "no timestamp" (a cast expression bindgen cannot see).
pub const AV_NOPTS_VALUE: i64 = i64::MIN;
