//! Link the vendored FFmpeg and, with `--features regenerate`, rewrite the
//! committed bindings for this target from its headers.
//!
//! FFmpeg is found at `$TH_FFMPEG_DIR`, else the repo's
//! `build-ffmpeg-<platform>/install` that `.ci/build_ffmpeg.py` produces.

use std::env;
use std::path::{Path, PathBuf};

const LIBS: [&str; 4] = ["avformat", "avcodec", "swscale", "avutil"];

fn platform() -> &'static str {
    match (env::var("CARGO_CFG_TARGET_OS").as_deref(), env::var("CARGO_CFG_TARGET_ARCH").as_deref()) {
        (Ok("windows"), Ok("x86_64")) => "windows-x86_64",
        (Ok("linux"), Ok("x86_64")) => "linux-x86_64",
        (Ok("macos"), Ok("aarch64")) => "macos-aarch64",
        (os, arch) => panic!("th_ffmpeg_sys: unsupported target {os:?}/{arch:?}"),
    }
}

fn ffmpeg_dir() -> PathBuf {
    println!("cargo:rerun-if-env-changed=TH_FFMPEG_DIR");
    if let Ok(dir) = env::var("TH_FFMPEG_DIR") {
        return PathBuf::from(dir);
    }
    // src/player/ffmpeg-sys -> repo root
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let repo = manifest.ancestors().nth(3).unwrap();
    repo.join(format!("build-ffmpeg-{}", platform())).join("install")
}

fn main() {
    let dir = ffmpeg_dir();
    assert!(
        dir.join("include/libavcodec/avcodec.h").is_file(),
        "th_ffmpeg_sys: no FFmpeg at {} (run .ci/build_ffmpeg.py or set TH_FFMPEG_DIR)",
        dir.display()
    );
    // MSVC builds put the import libraries beside the DLLs in bin/.
    for sub in ["lib", "bin"] {
        println!("cargo:rustc-link-search=native={}", dir.join(sub).display());
    }
    for lib in LIBS {
        println!("cargo:rustc-link-lib=dylib={lib}");
    }
    regenerate(&dir);
}

/// The FFmpeg functions th_media calls.
#[cfg(feature = "regenerate")]
const FUNCTIONS: &[&str] = &[
    "av_find_best_stream",
    "av_frame_alloc",
    "av_frame_free",
    "av_frame_unref",
    "av_packet_alloc",
    "av_packet_free",
    "av_packet_unref",
    "av_read_frame",
    "av_seek_frame",
    "av_strerror",
    "avcodec_alloc_context3",
    "avcodec_flush_buffers",
    "avcodec_free_context",
    "avcodec_open2",
    "avcodec_parameters_to_context",
    "avcodec_receive_frame",
    "avcodec_send_packet",
    "avformat_close_input",
    "avformat_find_stream_info",
    "avformat_open_input",
    "sws_freeContext",
    "sws_getCachedContext",
    "sws_scale",
];

/// The types th_media names; the enums' values come with them.
#[cfg(feature = "regenerate")]
const TYPES: &[&str] = &[
    "AVCodec",
    "AVCodecContext",
    "AVFormatContext",
    "AVFrame",
    "AVPacket",
    "AVStream",
    "SwsContext",
    "AVColorRange",
    "AVColorSpace",
    "AVMediaType",
    "AVPixelFormat",
    "SwsFlags",
];

/// The flag macros th_media passes (`SWS_BICUBIC` is a `SwsFlags` value).
#[cfg(feature = "regenerate")]
const CONSTANTS: &[&str] = &["AVSEEK_FLAG_BACKWARD", "AV_PKT_FLAG_KEY", "FF_THREAD_SLICE"];

#[cfg(feature = "regenerate")]
fn regenerate(dir: &Path) {
    let out = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("src/bindings").join(format!("{}.rs", platform()));
    let mut builder = bindgen::Builder::default()
        .header_contents(
            "ffmpeg.h",
            "#include <libavformat/avformat.h>\n\
             #include <libavcodec/avcodec.h>\n\
             #include <libswscale/swscale.h>\n",
        )
        .clang_arg(format!("-I{}", dir.join("include").display()));
    // Exactly what th_media calls and reads; bindgen adds what they need.
    for item in FUNCTIONS.iter().chain(TYPES).chain(CONSTANTS) {
        builder = builder.allowlist_item(format!("^{item}$"));
    }
    builder
        // Plain integer constants: a Rust enum would be UB the day FFmpeg hands
        // back a value the bindings do not know.
        .default_enum_style(bindgen::EnumVariation::Consts)
        .prepend_enum_name(false)
        .layout_tests(false)
        .generate_comments(false)
        .generate()
        .expect("bindgen failed")
        .write_to_file(&out)
        .expect("writing bindings");
}

#[cfg(not(feature = "regenerate"))]
fn regenerate(_dir: &Path) {}
