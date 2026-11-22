//! A movie file decoded frame-accurately through the vendored FFmpeg.
//!
//! [`MovieIndex::read`] reads the file's video packets once, without
//! decoding: every frame's presentation timestamp in display order and which
//! frames are keyframes. Frame `i` is then the `i`-th smallest timestamp,
//! never "the frame nearest `i / fps` seconds", so rounding can never land a
//! step on the wrong frame. The index is plain data, shared by everything
//! that reads the movie.
//!
//! A [`MovieDecoder`] holds the decoding state of one reader of an index.
//! Decoding frame `i` either continues forward from where it stopped (when
//! that is no more work) or seeks to the last keyframe at or before `i` and
//! decodes forward. Every frame decoded on the way is handed to the caller,
//! which keeps them in the frame buffer: a TumblePipe playblast has a single
//! keyframe, so stepping back from its last frame decodes the whole shot
//! once, and every step after that is free.
//!
//! Every FFmpeg object is held by an owner that frees it ([`MediaFile`],
//! [`CodecCtx`], [`Packet`], [`AvFrame`], [`Sws`]), so no path out of a
//! function leaks one or uses one that failed to allocate.

#![warn(clippy::undocumented_unsafe_blocks)]

use std::ffi::{CStr, CString};
use std::fmt;
use std::ptr::{self, NonNull};
use std::sync::Arc;

use th_ffmpeg_sys as ff;

use crate::frame::{Frame, Pixels, YuvMatrix};
use crate::{Error, Result};
use crate::{FRAME_RANGES_KEY, FrameRanges};

pub(crate) fn ff_error(code: i32) -> String {
    let mut buf = [0 as std::ffi::c_char; 256];
    // SAFETY: av_strerror writes at most `buf.len()` bytes, NUL-terminated.
    unsafe { ff::av_strerror(code, buf.as_mut_ptr(), buf.len()) };
    // SAFETY: `buf` holds a NUL-terminated string (above).
    unsafe { CStr::from_ptr(buf.as_ptr()) }.to_string_lossy().into_owned()
}

/// Whether frames of pixel format `format` decode to RGBA rather than
/// Y'CbCr: every format with alpha, and the RGB ones (a PNG movie's rgb24,
/// rgb48, palette and grey-with-alpha frames, QuickTime Animation's argb, ProRes
/// 4444's Y'CbCrA among them). The bindings have
/// no pixel format descriptors, so this is a list.
pub fn is_rgba_format(format: i32) -> bool {
    [
        ff::AV_PIX_FMT_RGBA,
        ff::AV_PIX_FMT_BGRA,
        ff::AV_PIX_FMT_ARGB,
        ff::AV_PIX_FMT_ABGR,
        ff::AV_PIX_FMT_RGB24,
        ff::AV_PIX_FMT_BGR24,
        ff::AV_PIX_FMT_RGB0,
        ff::AV_PIX_FMT_BGR0,
        ff::AV_PIX_FMT_RGB48BE,
        ff::AV_PIX_FMT_RGB48LE,
        ff::AV_PIX_FMT_BGR48BE,
        ff::AV_PIX_FMT_BGR48LE,
        ff::AV_PIX_FMT_RGBA64BE,
        ff::AV_PIX_FMT_RGBA64LE,
        ff::AV_PIX_FMT_BGRA64BE,
        ff::AV_PIX_FMT_BGRA64LE,
        ff::AV_PIX_FMT_GBRP,
        ff::AV_PIX_FMT_GBRAP,
        ff::AV_PIX_FMT_GBRP16BE,
        ff::AV_PIX_FMT_GBRP16LE,
        ff::AV_PIX_FMT_GBRAP16BE,
        ff::AV_PIX_FMT_GBRAP16LE,
        ff::AV_PIX_FMT_PAL8,
        ff::AV_PIX_FMT_YA8,
        ff::AV_PIX_FMT_YA16BE,
        ff::AV_PIX_FMT_YA16LE,
        ff::AV_PIX_FMT_RGB555BE,
        ff::AV_PIX_FMT_RGB555LE,
        ff::AV_PIX_FMT_RGB565BE,
        ff::AV_PIX_FMT_RGB565LE,
    ]
    .contains(&format)
        || yuva_layout(format).is_some()
}

/// A planar Y'CbCrA format converted to RGBA here, not by swscale: its bits
/// per sample (little-endian past 8) and its chroma planes' shifts across
/// and down.
fn yuva_layout(format: i32) -> Option<(u32, usize, usize)> {
    Some(match format {
        ff::AV_PIX_FMT_YUVA420P => (8, 1, 1),
        ff::AV_PIX_FMT_YUVA422P => (8, 1, 0),
        ff::AV_PIX_FMT_YUVA444P => (8, 0, 0),
        ff::AV_PIX_FMT_YUVA420P10LE => (10, 1, 1),
        ff::AV_PIX_FMT_YUVA422P10LE => (10, 1, 0),
        ff::AV_PIX_FMT_YUVA444P10LE => (10, 0, 0),
        ff::AV_PIX_FMT_YUVA422P12LE => (12, 1, 0),
        ff::AV_PIX_FMT_YUVA444P12LE => (12, 0, 0),
        ff::AV_PIX_FMT_YUVA420P16LE => (16, 1, 1),
        ff::AV_PIX_FMT_YUVA422P16LE => (16, 1, 0),
        ff::AV_PIX_FMT_YUVA444P16LE => (16, 0, 0),
        _ => return None,
    })
}

/// The Y'CbCr matrix and range a decoded frame says it is in.
fn matrix_of(decoded: &ff::AVFrame) -> (YuvMatrix, bool) {
    let full_range = decoded.color_range == ff::AVCOL_RANGE_JPEG || decoded.format == ff::AV_PIX_FMT_YUVJ420P;
    let matrix = match decoded.colorspace {
        ff::AVCOL_SPC_BT470BG | ff::AVCOL_SPC_SMPTE170M => YuvMatrix::Bt601,
        ff::AVCOL_SPC_BT709 => YuvMatrix::Bt709,
        // Unspecified: HD and up is BT.709 by convention, SD BT.601.
        _ if decoded.height >= 720 => YuvMatrix::Bt709,
        _ => YuvMatrix::Bt601,
    };
    (matrix, full_range)
}

/// Whether `stream` is a cover picture (an attached picture: an mp3's album
/// art), which is no video.
///
/// # Safety
/// `stream` is a stream of an open input.
unsafe fn is_cover(stream: *const ff::AVStream) -> bool {
    // SAFETY: the caller's.
    unsafe { (*stream).disposition & ff::AV_DISPOSITION_ATTACHED_PIC != 0 }
}

/// What to say of `what` ("video", "audio") streams in `codecs` the player
/// has no decoder for: "video codec av1 is not supported". None when there
/// are none.
pub fn unsupported(what: &str, codecs: &[String]) -> Option<String> {
    let (last, rest) = codecs.split_last()?;
    Some(if rest.is_empty() {
        format!("{what} codec {last} is not supported")
    } else {
        format!("{what} codecs {} and {last} are not supported", rest.join(", "))
    })
}

/// Why the file at `path` gives the player neither picture nor sound when
/// it is the codecs: "video codec av1 is not supported, audio codec dts is
/// not supported". None when it does not open, or nothing in it is in a
/// codec the player lacks (it has no streams, or only a cover picture).
pub fn undecodable(path: &str) -> Option<String> {
    let file = MediaFile::open(path).ok()?;
    let parts: Vec<String> = [("video", ff::AVMEDIA_TYPE_VIDEO), ("audio", ff::AVMEDIA_TYPE_AUDIO)]
        .into_iter()
        .filter_map(|(what, kind)| unsupported(what, &file.undecodable(kind)))
        .collect();
    (!parts.is_empty()).then(|| parts.join(", "))
}

/// An open media file, its streams probed; closed when dropped.
struct MediaFile {
    format: NonNull<ff::AVFormatContext>,
}

impl MediaFile {
    fn open(path: &str) -> std::result::Result<Self, String> {
        let c_path = CString::new(path).map_err(|_| "the path contains a NUL".to_owned())?;
        let mut format = ptr::null_mut();
        // SAFETY: `format` is a null out-pointer for the input opened;
        // `c_path` is NUL-terminated and outlives the call. On failure
        // FFmpeg frees what it allocated and leaves `format` null.
        let code = unsafe { ff::avformat_open_input(&mut format, c_path.as_ptr(), ptr::null(), ptr::null_mut()) };
        if code == ff::AVERROR_INVALIDDATA {
            return Err(format!("not a kind of file the player reads, or a damaged one ({})", ff_error(code)));
        }
        if code < 0 {
            return Err(ff_error(code));
        }
        let file = MediaFile { format: NonNull::new(format).ok_or("FFmpeg opened the file but gave no input")? };
        // SAFETY: an open input.
        let code = unsafe { ff::avformat_find_stream_info(file.as_ptr(), ptr::null_mut()) };
        if code < 0 {
            return Err(ff_error(code));
        }
        Ok(file)
    }

    fn as_ptr(&self) -> *mut ff::AVFormatContext {
        self.format.as_ptr()
    }

    /// Its streams, each valid while the file is open.
    fn streams(&self) -> &[*mut ff::AVStream] {
        // SAFETY: an open input; FFmpeg keeps `nb_streams` streams in
        // `streams` until it is closed (a null array when there are none).
        unsafe {
            let format = &*self.as_ptr();
            if format.nb_streams == 0 || format.streams.is_null() {
                return &[];
            }
            std::slice::from_raw_parts(format.streams, format.nb_streams as usize)
        }
    }

    /// The names of the codecs of its streams of `kind` this FFmpeg has no
    /// decoder for, each once, cover pictures left out.
    fn undecodable(&self, kind: ff::AVMediaType) -> Vec<String> {
        let mut names = Vec::new();
        for &stream in self.streams() {
            // SAFETY: a stream of this open input, and its codec parameters;
            // avcodec_get_name returns a static NUL-terminated name.
            let name = unsafe {
                let par = (*stream).codecpar;
                if (*par).codec_type != kind || is_cover(stream) || !ff::avcodec_find_decoder((*par).codec_id).is_null() {
                    continue;
                }
                CStr::from_ptr(ff::avcodec_get_name((*par).codec_id)).to_string_lossy().into_owned()
            };
            if !names.contains(&name) {
                names.push(name);
            }
        }
        names
    }

    /// The file's metadata value for `key` (exact case), if it has one.
    fn tag(&self, key: &str) -> Option<String> {
        let key = CString::new(key).ok()?;
        // SAFETY: an open input's metadata (a null dictionary is empty);
        // an entry's value is NUL-terminated and lives as long as the input.
        unsafe {
            let entry = ff::av_dict_get((*self.as_ptr()).metadata, key.as_ptr(), ptr::null(), ff::AV_DICT_MATCH_CASE);
            (!entry.is_null()).then(|| CStr::from_ptr((*entry).value).to_string_lossy().into_owned())
        }
    }
}

impl Drop for MediaFile {
    fn drop(&mut self) {
        let mut format = self.as_ptr();
        // SAFETY: an input avformat_open_input opened, closed once here.
        unsafe { ff::avformat_close_input(&mut format) };
    }
}

/// Why a file gives no stream of the kind asked for.
#[derive(Debug)]
pub(crate) enum OpenError {
    /// The file does not open.
    File(String),
    /// It opens, but holds no stream of that kind the player can decode:
    /// "no decodable audio stream (...)".
    NoStream(String),
}

impl fmt::Display for OpenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OpenError::File(message) | OpenError::NoStream(message) => f.write_str(message),
        }
    }
}

/// An open media file and one of its streams (video, unless asked for
/// another), with the stream's decoder.
pub(crate) struct Input {
    file: MediaFile,
    stream: NonNull<ff::AVStream>,
    /// The stream's index in the file: never negative.
    stream_index: i32,
    decoder: NonNull<ff::AVCodec>,
}

impl Input {
    fn open(path: &str) -> std::result::Result<Self, OpenError> {
        Self::open_stream(path, ff::AVMEDIA_TYPE_VIDEO)
    }

    /// `path`'s best stream of `kind` (an `AVMediaType`). A video stream
    /// that is only a cover picture (an mp3's album art) is none.
    pub(crate) fn open_stream(path: &str, kind: ff::AVMediaType) -> std::result::Result<Self, OpenError> {
        let file = MediaFile::open(path).map_err(OpenError::File)?;
        let what = if kind == ff::AVMEDIA_TYPE_AUDIO { "audio" } else { "video" };
        let no_stream = |file: &MediaFile, otherwise: String| {
            let why = unsupported(what, &file.undecodable(kind)).unwrap_or(otherwise);
            OpenError::NoStream(format!("no decodable {what} stream ({why})"))
        };
        let mut decoder = ptr::null();
        // SAFETY: an open input; `decoder` is an out-pointer for the
        // stream's decoder, a static AVCodec.
        let index = unsafe { ff::av_find_best_stream(file.as_ptr(), kind, -1, -1, &mut decoder, 0) };
        let (Ok(at), Some(decoder)) = (usize::try_from(index), NonNull::new(decoder.cast_mut())) else {
            return Err(no_stream(&file, ff_error(index)));
        };
        let stream = file.streams().get(at).copied().and_then(NonNull::new).expect("av_find_best_stream names one of the file's streams");
        // SAFETY: a stream of the open input.
        if unsafe { is_cover(stream.as_ptr()) } {
            return Err(no_stream(&file, "only a cover picture".into()));
        }
        Ok(Input { file, stream, stream_index: index, decoder })
    }

    pub(crate) fn format(&self) -> *mut ff::AVFormatContext {
        self.file.as_ptr()
    }

    /// The stream, valid while the input is.
    pub(crate) fn stream(&self) -> *mut ff::AVStream {
        self.stream.as_ptr()
    }

    pub(crate) fn stream_index(&self) -> i32 {
        self.stream_index
    }

    fn tag(&self, key: &str) -> Option<String> {
        self.file.tag(key)
    }
}

/// A decoder context for an input's stream, its parameters copied from the
/// stream and opened; freed when dropped.
pub(crate) struct CodecCtx(NonNull<ff::AVCodecContext>);

impl CodecCtx {
    /// Decoding on `threads` slice threads where given (frame threading
    /// delays output by a frame per thread; slice threading keeps one-in,
    /// one-out), else as FFmpeg chooses.
    pub(crate) fn open(input: &Input, threads: Option<usize>) -> std::result::Result<Self, String> {
        // SAFETY: the input's decoder, a static AVCodec.
        let context = unsafe { ff::avcodec_alloc_context3(input.decoder.as_ptr()) };
        let codec = CodecCtx(NonNull::new(context).ok_or("out of memory for a decoder")?);
        // SAFETY: a context just allocated, and the codec parameters of the
        // input's stream.
        let code = unsafe { ff::avcodec_parameters_to_context(codec.as_ptr(), (*input.stream()).codecpar) };
        if code < 0 {
            return Err(ff_error(code));
        }
        if let Some(threads) = threads {
            // SAFETY: a context allocated above, not yet opened.
            unsafe {
                (*codec.as_ptr()).thread_count = threads as i32;
                (*codec.as_ptr()).thread_type = ff::FF_THREAD_SLICE as i32;
            }
        }
        // SAFETY: a context for the input's decoder, opened once.
        let code = unsafe { ff::avcodec_open2(codec.as_ptr(), input.decoder.as_ptr(), ptr::null_mut()) };
        if code < 0 {
            return Err(ff_error(code));
        }
        Ok(codec)
    }

    pub(crate) fn as_ptr(&self) -> *mut ff::AVCodecContext {
        self.0.as_ptr()
    }

    /// Its sample rate (an audio decoder's; 0 when it has none).
    pub(crate) fn sample_rate(&self) -> u32 {
        // SAFETY: an allocated context.
        unsafe { (*self.as_ptr()).sample_rate }.max(0) as u32
    }
}

impl Drop for CodecCtx {
    fn drop(&mut self) {
        let mut context = self.as_ptr();
        // SAFETY: a context avcodec_alloc_context3 allocated, freed once here.
        unsafe { ff::avcodec_free_context(&mut context) };
    }
}

/// A packet read from an input; freed (and so unreferenced) when dropped.
pub(crate) struct Packet(NonNull<ff::AVPacket>);

impl Packet {
    pub(crate) fn new() -> std::result::Result<Self, String> {
        // SAFETY: allocation only.
        NonNull::new(unsafe { ff::av_packet_alloc() }).map(Packet).ok_or_else(|| "out of memory for a packet".into())
    }

    pub(crate) fn as_ptr(&self) -> *mut ff::AVPacket {
        self.0.as_ptr()
    }

    /// The stream the packet last read belongs to.
    pub(crate) fn stream_index(&self) -> i32 {
        // SAFETY: an allocated packet.
        unsafe { (*self.as_ptr()).stream_index }
    }

    pub(crate) fn unref(&mut self) {
        // SAFETY: an allocated packet; unreferencing an empty one is a no-op.
        unsafe { ff::av_packet_unref(self.as_ptr()) };
    }
}

impl Drop for Packet {
    fn drop(&mut self) {
        let mut packet = self.as_ptr();
        // SAFETY: a packet av_packet_alloc allocated, freed once here.
        unsafe { ff::av_packet_free(&mut packet) };
    }
}

/// A frame a decoder fills; freed when dropped.
pub(crate) struct AvFrame(NonNull<ff::AVFrame>);

impl AvFrame {
    pub(crate) fn new() -> std::result::Result<Self, String> {
        // SAFETY: allocation only.
        NonNull::new(unsafe { ff::av_frame_alloc() }).map(AvFrame).ok_or_else(|| "out of memory for a frame".into())
    }

    pub(crate) fn as_ptr(&self) -> *mut ff::AVFrame {
        self.0.as_ptr()
    }

    pub(crate) fn get(&self) -> &ff::AVFrame {
        // SAFETY: an allocated frame, only changed through `&mut self` or
        // by FFmpeg calls this borrow does not span.
        unsafe { self.0.as_ref() }
    }

    pub(crate) fn unref(&mut self) {
        // SAFETY: an allocated frame; unreferencing an empty one is a no-op.
        unsafe { ff::av_frame_unref(self.as_ptr()) };
    }
}

impl Drop for AvFrame {
    fn drop(&mut self) {
        let mut frame = self.as_ptr();
        // SAFETY: a frame av_frame_alloc allocated, freed once here.
        unsafe { ff::av_frame_free(&mut frame) };
    }
}

/// swscale's context, kept between frames of one size and format; freed
/// when dropped.
#[derive(Default)]
struct Sws(Option<NonNull<ff::SwsContext>>);

impl Sws {
    /// A context converting frames like `decoded` to `target` at their own
    /// size: the last one when it fits. None when swscale cannot.
    // SWS_BICUBIC is a C enum constant, i32 in the Windows bindings and u32
    // elsewhere: its cast is a no-op only here.
    #[allow(clippy::unnecessary_cast)]
    fn for_frame(&mut self, decoded: &ff::AVFrame, target: ff::AVPixelFormat) -> Option<*mut ff::SwsContext> {
        let last = self.0.map_or(ptr::null_mut(), NonNull::as_ptr);
        // SAFETY: `last` is null or a context this call made before, which
        // it reuses or frees.
        let context = unsafe {
            ff::sws_getCachedContext(
                last,
                decoded.width,
                decoded.height,
                decoded.format,
                decoded.width,
                decoded.height,
                target,
                ff::SWS_BICUBIC as i32,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null(),
            )
        };
        self.0 = NonNull::new(context);
        self.0.map(NonNull::as_ptr)
    }
}

impl Drop for Sws {
    fn drop(&mut self) {
        if let Some(context) = self.0 {
            // SAFETY: a context sws_getCachedContext made, freed once here.
            unsafe { ff::sws_freeContext(context.as_ptr()) };
        }
    }
}

/// The metadata key TumblePipe records a playblast's first frame under
/// (`tumblepipe.apps.mp4.START_FRAME_KEY`).
pub const START_FRAME_KEY: &str = "start_frame";

/// A `start_frame` tag's value as a frame number; anything but an integer is
/// an error, never a guess.
pub fn parse_start_frame(value: &str) -> std::result::Result<i64, String> {
    value.trim().parse::<i64>().map_err(|_| format!("its {START_FRAME_KEY} tag is '{value}', not a frame number"))
}

/// Which frames a movie has: their timestamps and keyframes, and its rate.
#[derive(Debug)]
pub struct MovieIndex {
    path: String,
    /// Presentation timestamps in display order: frame `i` is `pts[i]`.
    pts: Vec<i64>,
    /// Indices (into `pts`) of the keyframes, ascending; the first is 0.
    keyframes: Vec<usize>,
    rate: (i32, i32),
    /// The shot frame its first picture is, when the movie records one
    /// (TumblePipe playblasts do).
    start_frame: Option<i64>,
    /// The frames its pictures are, when they are not one run: a
    /// multi-range render's mp4 records them (#86).
    frame_ranges: Option<FrameRanges>,
}

impl MovieIndex {
    /// Read the movie at `path`'s packets once (no decoding). A movie
    /// without a frame rate, without frames, or not opening on a keyframe
    /// cannot be played frame-accurately and is an error.
    pub fn read(path: &str) -> Result<Self> {
        let fail = |message: String| Error::Open { path: path.to_owned(), message };
        let input = Input::open(path).map_err(|e| fail(e.to_string()))?;
        let start_frame = input.tag(START_FRAME_KEY).map(|v| parse_start_frame(&v)).transpose().map_err(fail)?;
        let frame_ranges = input.tag(FRAME_RANGES_KEY).map(|v| FrameRanges::parse(&v)).transpose().map_err(fail)?;
        // SAFETY: the input's stream.
        let rate = unsafe { (*input.stream()).avg_frame_rate };
        if rate.num <= 0 || rate.den <= 0 {
            return Err(fail(format!("no frame rate ({}/{})", rate.num, rate.den)));
        }
        let mut entries: Vec<(i64, bool)> = Vec::new();
        let mut packet = Packet::new().map_err(fail)?;
        loop {
            // SAFETY: an open input and an allocated packet.
            let code = unsafe { ff::av_read_frame(input.format(), packet.as_ptr()) };
            if code == ff::AVERROR_EOF {
                break;
            }
            if code < 0 {
                return Err(fail(ff_error(code)));
            }
            if packet.stream_index() == input.stream_index() {
                // SAFETY: a packet just read.
                let (pts, dts, flags) = unsafe { ((*packet.as_ptr()).pts, (*packet.as_ptr()).dts, (*packet.as_ptr()).flags) };
                entries.push((if pts != ff::AV_NOPTS_VALUE { pts } else { dts }, flags & ff::AV_PKT_FLAG_KEY as i32 != 0));
            }
            packet.unref();
        }
        entries.sort_by_key(|&(pts, _)| pts);
        let keyframes: Vec<usize> = entries.iter().enumerate().filter(|(_, (_, key))| *key).map(|(i, _)| i).collect();
        if entries.is_empty() {
            return Err(fail("the video stream has no frames".into()));
        }
        if keyframes.first() != Some(&0) {
            return Err(fail("the video does not start on a keyframe".into()));
        }
        if let Some(list) = &frame_ranges
            && list.count() != entries.len()
        {
            return Err(fail(format!("its {FRAME_RANGES_KEY} tag lists {} frames; it has {}", list.count(), entries.len())));
        }
        Ok(Self {
            path: path.to_owned(),
            pts: entries.iter().map(|&(pts, _)| pts).collect(),
            keyframes,
            rate: (rate.num, rate.den),
            start_frame,
            frame_ranges,
        })
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    pub fn frame_count(&self) -> usize {
        self.pts.len()
    }

    /// Frames per second as a ratio (`(24, 1)`, `(24000, 1001)`).
    pub fn rate(&self) -> (i32, i32) {
        self.rate
    }

    /// The shot frame its first picture is, when the movie records one
    /// (the `start_frame` tag TumblePipe playblasts carry).
    pub fn start_frame(&self) -> Option<i64> {
        self.start_frame
    }

    /// The frames its pictures are when it records a list of them (a
    /// multi-range render's mp4); its numbering then, over `start_frame`.
    pub fn frame_ranges(&self) -> Option<&FrameRanges> {
        self.frame_ranges.as_ref()
    }

    /// Indices of the keyframes, ascending.
    pub fn keyframes(&self) -> &[usize] {
        &self.keyframes
    }

    fn keyframe_at_or_before(&self, index: usize) -> usize {
        self.keyframes[self.keyframes.partition_point(|&k| k <= index) - 1]
    }
}

/// One reader of a movie: the FFmpeg decoding state, decoding on `threads`
/// threads.
pub struct MovieDecoder {
    index: Arc<MovieIndex>,
    // Declared before `input`, so they are freed first, while the file
    // they came from is still open.
    codec: CodecCtx,
    packet: Packet,
    decoded: AvFrame,
    sws: Sws,
    input: Input,
    /// The last frame the decoder produced, when it can continue from there.
    position: Option<usize>,
}

// SAFETY: the FFmpeg objects are owned by this value alone and only touched
// through `&mut self`; FFmpeg ties none of them to the thread that made it,
// so moving the decoder to a decode thread is sound.
unsafe impl Send for MovieDecoder {}

impl MovieDecoder {
    pub fn open(index: Arc<MovieIndex>, threads: usize) -> Result<Self> {
        let fail = |message: String| Error::Open { path: index.path.clone(), message };
        let input = Input::open(&index.path).map_err(|e| fail(e.to_string()))?;
        Ok(MovieDecoder {
            codec: CodecCtx::open(&input, Some(threads)).map_err(fail)?,
            packet: Packet::new().map_err(fail)?,
            decoded: AvFrame::new().map_err(fail)?,
            sws: Sws::default(),
            input,
            position: None,
            index: Arc::clone(&index),
        })
    }

    /// The movie it reads.
    pub fn index(&self) -> &Arc<MovieIndex> {
        &self.index
    }

    fn seek_to(&mut self, keyframe: usize) -> std::result::Result<(), String> {
        let pts = self.index.pts[keyframe];
        // SAFETY: the open input and its stream's index.
        let code = unsafe { ff::av_seek_frame(self.input.format(), self.input.stream_index(), pts, ff::AVSEEK_FLAG_BACKWARD as i32) };
        if code < 0 {
            return Err(ff_error(code));
        }
        // SAFETY: an open decoder context.
        unsafe { ff::avcodec_flush_buffers(self.codec.as_ptr()) };
        self.position = None;
        Ok(())
    }

    /// Back to the file's first byte, so decoding starts at its first
    /// frame: for a seek that landed past its keyframe. MPEG-TS and MPEG-PS
    /// have no index, so a timestamp seek there is a guess by bytes; a mov
    /// or Matroska seek is exact (and mov cannot seek by bytes).
    fn rewind(&mut self) -> std::result::Result<(), String> {
        // SAFETY: the open input; stream -1 seeks the file by bytes.
        let code = unsafe { ff::av_seek_frame(self.input.format(), -1, 0, ff::AVSEEK_FLAG_BYTE) };
        if code < 0 {
            return Err(ff_error(code));
        }
        // SAFETY: an open decoder context.
        unsafe { ff::avcodec_flush_buffers(self.codec.as_ptr()) };
        self.position = None;
        Ok(())
    }

    /// Decode frame `index`, handing it and every frame decoded on the way to
    /// `keep` (so the caller can buffer them), and return it. Stops with
    /// [`Error::Cancelled`] once `wanted` says the frame is no longer wanted
    /// (it asks before each frame).
    pub fn decode(&mut self, index: usize, mut keep: impl FnMut(usize, &Frame), wanted: impl Fn() -> bool) -> Result<Frame> {
        let count = self.index.frame_count();
        if index >= count {
            return Err(Error::OutOfRange { index, count });
        }
        let keyframe = self.index.keyframe_at_or_before(index);
        // Continue forward when the decoder already sits between that keyframe
        // and the target: it is never more work than seeking back.
        let resume = matches!(self.position, Some(at) if at < index && at >= keyframe);
        if !resume {
            self.seek_to(keyframe).map_err(|message| Error::Decode { index, message })?;
        }
        let mut rewound = false;
        loop {
            if !wanted() {
                return Err(Error::Cancelled { index });
            }
            // A seek that guessed wrong lands mid-picture (the decoder
            // refuses the data) or past the frame: once more from the start.
            let problem = match self.next_frame() {
                Ok((at, frame)) => {
                    keep(at, &frame);
                    if at == index {
                        return Ok(frame);
                    }
                    if at < index {
                        continue;
                    }
                    format!("decoder skipped to frame {at}")
                }
                Err(message) => message,
            };
            if rewound || self.rewind().is_err() {
                return Err(Error::Decode { index, message: problem });
            }
            rewound = true;
        }
    }

    /// The next frame out of the decoder and its index.
    fn next_frame(&mut self) -> std::result::Result<(usize, Frame), String> {
        loop {
            // SAFETY: an open decoder context and an allocated frame.
            let code = unsafe { ff::avcodec_receive_frame(self.codec.as_ptr(), self.decoded.as_ptr()) };
            if code == 0 {
                let decoded = self.decoded.get();
                let pts = if decoded.best_effort_timestamp != ff::AV_NOPTS_VALUE { decoded.best_effort_timestamp } else { decoded.pts };
                let at =
                    self.index.pts.binary_search(&pts).map_err(|_| format!("decoded a frame at pts {pts} that is not in the index"))?;
                // SAFETY: a frame the decoder just produced.
                let frame = unsafe { convert(decoded, &mut self.sws) }?;
                self.decoded.unref();
                self.position = Some(at);
                return Ok((at, frame));
            }
            if code == ff::AVERROR_EOF {
                return Err("end of stream before the frame".into());
            }
            if code != ff::AVERROR_EAGAIN {
                return Err(ff_error(code));
            }
            // The decoder wants input: feed it the next video packet, or
            // tell it the file has ended so it flushes what it holds.
            // SAFETY: the open input and an allocated packet.
            let read = unsafe { ff::av_read_frame(self.input.format(), self.packet.as_ptr()) };
            if read == ff::AVERROR_EOF {
                // SAFETY: an open decoder context; no packet asks it to flush.
                unsafe { ff::avcodec_send_packet(self.codec.as_ptr(), ptr::null()) };
                continue;
            }
            if read < 0 {
                return Err(ff_error(read));
            }
            if self.packet.stream_index() == self.input.stream_index() {
                // SAFETY: an open decoder context and a packet just read.
                let sent = unsafe { ff::avcodec_send_packet(self.codec.as_ptr(), self.packet.as_ptr()) };
                self.packet.unref();
                if sent < 0 && sent != ff::AVERROR_EAGAIN {
                    return Err(ff_error(sent));
                }
            } else {
                self.packet.unref();
            }
        }
    }
}

/// `decoded` as 8-bit RGBA when it has alpha or is RGB (see
/// [`is_rgba_format`]), else tightly packed 8-bit 4:2:0.
///
/// # Safety
/// `decoded` is a frame a video decoder just produced: its planes hold its
/// rows at its strides.
unsafe fn convert(decoded: &ff::AVFrame, sws: &mut Sws) -> std::result::Result<Frame, String> {
    // SAFETY: the caller's.
    if is_rgba_format(decoded.format) { unsafe { convert_rgba(decoded, sws) } } else { unsafe { convert_yuv(decoded, sws) } }
}

/// `decoded` as 8-bit RGBA, straight alpha (opaque when the frame has
/// none), its code values as they are: swscale never premultiplies. RGBA
/// itself is copied row by row.
///
/// # Safety
/// As [`convert`].
unsafe fn convert_rgba(decoded: &ff::AVFrame, sws: &mut Sws) -> std::result::Result<Frame, String> {
    let (width, height) = (decoded.width as usize, decoded.height as usize);
    let mut rgba = vec![0u8; width * height * 4];
    if let Some((bits, sx, sy)) = yuva_layout(decoded.format) {
        // Converted here as `convert_yuv` and the shader do, by the
        // movie's own matrix and range: swscale's would be BT.601.
        let (matrix, full_range) = matrix_of(decoded);
        let scale = f32::from(1u16 << (bits - 8));
        let alpha_max = ((1u32 << bits) - 1) as f32;
        let sample = |plane: usize, row: usize, col: usize| -> f32 {
            let stride = decoded.linesize[plane] as usize;
            // SAFETY: `row` and `col` are inside the plane (its chroma
            // shifts applied), whose rows are `stride` bytes apart.
            unsafe {
                let line = decoded.data[plane].add(row * stride);
                if bits == 8 {
                    f32::from(*line.add(col))
                } else {
                    let at = line.add(col * 2);
                    f32::from(u16::from_le_bytes([*at, *at.add(1)]))
                }
            }
        };
        for row in 0..height {
            for col in 0..width {
                let (crow, ccol) = (row >> sy, col >> sx);
                let rgb = crate::thumbnail::yuv_pixel_f32(
                    sample(0, row, col) / scale,
                    sample(1, crow, ccol) / scale,
                    sample(2, crow, ccol) / scale,
                    matrix,
                    full_range,
                );
                let out = &mut rgba[(row * width + col) * 4..][..4];
                for c in 0..3 {
                    out[c] = (rgb[c] * 255.0).round() as u8;
                }
                out[3] = (sample(3, row, col) / alpha_max * 255.0).round() as u8;
            }
        }
    } else if decoded.format == ff::AV_PIX_FMT_RGBA {
        let stride = decoded.linesize[0] as usize;
        for row in 0..height {
            // SAFETY: an RGBA frame's row of `width` pixels, rows `stride` apart.
            let src = unsafe { std::slice::from_raw_parts(decoded.data[0].add(row * stride), width * 4) };
            rgba[row * width * 4..(row + 1) * width * 4].copy_from_slice(src);
        }
    } else {
        let context =
            sws.for_frame(decoded, ff::AV_PIX_FMT_RGBA).ok_or_else(|| format!("cannot convert pixel format {} to RGBA", decoded.format))?;
        let mut planes = [rgba.as_mut_ptr(), ptr::null_mut(), ptr::null_mut(), ptr::null_mut()];
        let strides = [(width * 4) as i32, 0, 0, 0];
        // SAFETY: a context for this frame's size and format; the frame's
        // planes (the caller's), and one RGBA plane of `height` rows at
        // `width * 4` bytes, allocated above.
        unsafe {
            ff::sws_scale(
                context,
                decoded.data.as_ptr() as *const *const u8,
                decoded.linesize.as_ptr(),
                0,
                decoded.height,
                planes.as_mut_ptr(),
                strides.as_ptr(),
            )
        };
    }
    Ok(Frame { width: width as u32, height: height as u32, pixels: Arc::new(Pixels::Rgba8(rgba)) })
}

/// `decoded` as tightly packed 8-bit 4:2:0. Anything else (4:2:2 ProRes,
/// 10-bit) is converted through swscale.
///
/// # Safety
/// As [`convert`].
unsafe fn convert_yuv(decoded: &ff::AVFrame, sws: &mut Sws) -> std::result::Result<Frame, String> {
    let (width, height) = (decoded.width as usize, decoded.height as usize);
    let (matrix, full_range) = matrix_of(decoded);
    let (cw, ch) = (width.div_ceil(2), height.div_ceil(2));
    let mut y = vec![0u8; width * height];
    let mut u = vec![0u8; cw * ch];
    let mut v = vec![0u8; cw * ch];
    if decoded.format == ff::AV_PIX_FMT_YUV420P || decoded.format == ff::AV_PIX_FMT_YUVJ420P {
        for (plane, out, w, h) in [(0, &mut y, width, height), (1, &mut u, cw, ch), (2, &mut v, cw, ch)] {
            let stride = decoded.linesize[plane] as usize;
            for row in 0..h {
                // SAFETY: a 4:2:0 plane's row of `w` samples, rows `stride` apart.
                let src = unsafe { std::slice::from_raw_parts(decoded.data[plane].add(row * stride), w) };
                out[row * w..(row + 1) * w].copy_from_slice(src);
            }
        }
    } else {
        let target = if full_range { ff::AV_PIX_FMT_YUVJ420P } else { ff::AV_PIX_FMT_YUV420P };
        let context = sws.for_frame(decoded, target).ok_or_else(|| format!("cannot convert pixel format {}", decoded.format))?;
        let mut planes = [y.as_mut_ptr(), u.as_mut_ptr(), v.as_mut_ptr(), ptr::null_mut()];
        let strides = [width as i32, cw as i32, cw as i32, 0];
        // SAFETY: a context for this frame's size and format; the frame's
        // planes (the caller's), and the three 4:2:0 planes allocated above
        // at the strides given.
        unsafe {
            ff::sws_scale(
                context,
                decoded.data.as_ptr() as *const *const u8,
                decoded.linesize.as_ptr(),
                0,
                decoded.height,
                planes.as_mut_ptr(),
                strides.as_ptr(),
            )
        };
    }
    Ok(Frame { width: width as u32, height: height as u32, pixels: Arc::new(Pixels::Yuv420 { y, u, v, matrix, full_range }) })
}
