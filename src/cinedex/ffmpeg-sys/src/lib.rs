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

/// FFmpeg's `FFERRTAG(a, b, c, d)`: an FFmpeg error code of four tag bytes.
pub const fn fferrtag(a: u8, b: u8, c: u8, d: u8) -> i32 {
    -((a as i32) | ((b as i32) << 8) | ((c as i32) << 16) | ((d as i32) << 24))
}

/// `AVERROR_INVALIDDATA`: what opening a file no demuxer takes returns.
pub const AVERROR_INVALIDDATA: i32 = fferrtag(b'I', b'N', b'D', b'A');

/// `AVERROR_DECODER_NOT_FOUND`: a stream is there, its codec's decoder is not.
pub const AVERROR_DECODER_NOT_FOUND: i32 = fferrtag(0xF8, b'D', b'E', b'C');

/// `AV_DISPOSITION_ATTACHED_PIC`: a stream that is one cover picture (an
/// mp3's album art), not video (libavformat/avformat.h).
pub const AV_DISPOSITION_ATTACHED_PIC: i32 = 1 << 10;

/// `AVSEEK_FLAG_BYTE`: `av_seek_frame` to a byte offset, not a timestamp
/// (libavformat/avformat.h).
pub const AVSEEK_FLAG_BYTE: i32 = 2;

/// `AV_NOPTS_VALUE`: "no timestamp" (a cast expression bindgen cannot see).
pub const AV_NOPTS_VALUE: i64 = i64::MIN;

/// One key and value of an [`AVDictionary`] (libavutil/dict.h). Written by
/// hand, not generated: its layout has been these two pointers since FFmpeg
/// 0.x, and binding it here keeps the per-platform bindings untouched.
#[repr(C)]
#[derive(Debug, Copy, Clone)]
pub struct AVDictionaryEntry {
    pub key: *mut ::std::os::raw::c_char,
    pub value: *mut ::std::os::raw::c_char,
}

/// `av_dict_get` matches keys case-insensitively unless this is set.
pub const AV_DICT_MATCH_CASE: i32 = 1;

unsafe extern "C" {
    /// The entry of `m` named `key` after `prev` (null: the first), or null
    /// (libavutil/dict.h). Declared by hand with [`AVDictionaryEntry`].
    pub fn av_dict_get(
        m: *const AVDictionary,
        key: *const ::std::os::raw::c_char,
        prev: *const AVDictionaryEntry,
        flags: ::std::os::raw::c_int,
    ) -> *mut AVDictionaryEntry;

    /// The decoder of codec `id` this FFmpeg was built with, or null
    /// (libavcodec/codec.h). Declared by hand, like `av_dict_get`, so the
    /// committed bindings stay as generated.
    pub fn avcodec_find_decoder(id: AVCodecID) -> *const AVCodec;

    /// Codec `id`'s short name ("h264", "av1"), whether or not this FFmpeg
    /// decodes it; never null (libavcodec/avcodec.h).
    pub fn avcodec_get_name(id: AVCodecID) -> *const ::std::os::raw::c_char;
}
