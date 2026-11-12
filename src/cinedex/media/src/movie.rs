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

use std::ffi::{CStr, CString};
use std::ptr;
use std::sync::Arc;

use th_ffmpeg_sys as ff;

use crate::frame::{Frame, Pixels, YuvMatrix};
use crate::{Error, Result};
use crate::{FRAME_RANGES_KEY, FrameRanges};

pub(crate) fn ff_error(code: i32) -> String {
    let mut buf = [0 as std::ffi::c_char; 256];
    unsafe { ff::av_strerror(code, buf.as_mut_ptr(), buf.len()) };
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

/// An open media file and one of its streams (video, unless asked for
/// another).
pub(crate) struct Input {
    pub(crate) format: *mut ff::AVFormatContext,
    pub(crate) stream_index: i32,
    /// The stream's decoder.
    pub(crate) decoder: *const ff::AVCodec,
}

impl Input {
    fn open(path: &str) -> std::result::Result<Self, String> {
        Self::open_stream(path, ff::AVMEDIA_TYPE_VIDEO)
    }

    /// `path`'s best stream of `kind` (an `AVMediaType`).
    pub(crate) fn open_stream(path: &str, kind: ff::AVMediaType) -> std::result::Result<Self, String> {
        let c_path = CString::new(path).map_err(|_| "the path contains a NUL".to_owned())?;
        let mut input = Input { format: ptr::null_mut(), stream_index: -1, decoder: ptr::null() };
        unsafe {
            let code = ff::avformat_open_input(&mut input.format, c_path.as_ptr(), ptr::null(), ptr::null_mut());
            if code < 0 {
                return Err(ff_error(code));
            }
            let code = ff::avformat_find_stream_info(input.format, ptr::null_mut());
            if code < 0 {
                return Err(ff_error(code));
            }
            let index = ff::av_find_best_stream(input.format, kind, -1, -1, &mut input.decoder, 0);
            if index < 0 || input.decoder.is_null() {
                let what = if kind == ff::AVMEDIA_TYPE_AUDIO { "audio" } else { "video" };
                return Err(format!("no decodable {what} stream ({})", ff_error(index)));
            }
            input.stream_index = index;
        }
        Ok(input)
    }

    pub(crate) fn stream(&self) -> *mut ff::AVStream {
        unsafe { *(*self.format).streams.add(self.stream_index as usize) }
    }

    /// The file's metadata value for `key` (exact case), if it has one.
    fn tag(&self, key: &str) -> Option<String> {
        let key = CString::new(key).ok()?;
        unsafe {
            let entry = ff::av_dict_get((*self.format).metadata, key.as_ptr(), ptr::null(), ff::AV_DICT_MATCH_CASE);
            (!entry.is_null()).then(|| CStr::from_ptr((*entry).value).to_string_lossy().into_owned())
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

impl Drop for Input {
    fn drop(&mut self) {
        unsafe { ff::avformat_close_input(&mut self.format) };
    }
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
        let input = Input::open(path).map_err(fail)?;
        let start_frame = input.tag(START_FRAME_KEY).map(|v| parse_start_frame(&v)).transpose().map_err(fail)?;
        let frame_ranges = input.tag(FRAME_RANGES_KEY).map(|v| FrameRanges::parse(&v)).transpose().map_err(fail)?;
        let rate = unsafe { (*input.stream()).avg_frame_rate };
        if rate.num <= 0 || rate.den <= 0 {
            return Err(fail(format!("no frame rate ({}/{})", rate.num, rate.den)));
        }
        let mut entries: Vec<(i64, bool)> = Vec::new();
        unsafe {
            let mut packet = ff::av_packet_alloc();
            let read = loop {
                let code = ff::av_read_frame(input.format, packet);
                if code == ff::AVERROR_EOF {
                    break Ok(());
                }
                if code < 0 {
                    break Err(ff_error(code));
                }
                if (*packet).stream_index == input.stream_index {
                    let pts = if (*packet).pts != ff::AV_NOPTS_VALUE { (*packet).pts } else { (*packet).dts };
                    entries.push((pts, (*packet).flags & ff::AV_PKT_FLAG_KEY as i32 != 0));
                }
                ff::av_packet_unref(packet);
            };
            ff::av_packet_free(&mut packet);
            read.map_err(fail)?;
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
    input: Input,
    codec: *mut ff::AVCodecContext,
    packet: *mut ff::AVPacket,
    decoded: *mut ff::AVFrame,
    sws: *mut ff::SwsContext,
    /// The last frame the decoder produced, when it can continue from there.
    position: Option<usize>,
}

// The FFmpeg contexts are owned exclusively by this value and only touched
// through `&mut self`; moving it to a decode thread is sound.
unsafe impl Send for MovieDecoder {}

impl MovieDecoder {
    pub fn open(index: Arc<MovieIndex>, threads: usize) -> Result<Self> {
        let fail = |message: String| Error::Open { path: index.path.clone(), message };
        let input = Input::open(&index.path).map_err(fail)?;
        let mut decoder = MovieDecoder {
            input,
            codec: ptr::null_mut(),
            packet: ptr::null_mut(),
            decoded: ptr::null_mut(),
            sws: ptr::null_mut(),
            position: None,
            index: Arc::clone(&index),
        };
        unsafe {
            decoder.codec = ff::avcodec_alloc_context3(decoder.input.decoder);
            ff::avcodec_parameters_to_context(decoder.codec, (*decoder.input.stream()).codecpar);
            // Frame threading delays output by a frame per thread; slice
            // threading keeps one-in, one-out.
            (*decoder.codec).thread_count = threads as i32;
            (*decoder.codec).thread_type = ff::FF_THREAD_SLICE as i32;
            let code = ff::avcodec_open2(decoder.codec, decoder.input.decoder, ptr::null_mut());
            if code < 0 {
                return Err(fail(ff_error(code)));
            }
            decoder.packet = ff::av_packet_alloc();
            decoder.decoded = ff::av_frame_alloc();
        }
        Ok(decoder)
    }

    /// The movie it reads.
    pub fn index(&self) -> &Arc<MovieIndex> {
        &self.index
    }

    fn seek_to(&mut self, keyframe: usize) -> std::result::Result<(), String> {
        unsafe {
            let code =
                ff::av_seek_frame(self.input.format, self.input.stream_index, self.index.pts[keyframe], ff::AVSEEK_FLAG_BACKWARD as i32);
            if code < 0 {
                return Err(ff_error(code));
            }
            ff::avcodec_flush_buffers(self.codec);
        }
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
        loop {
            if !wanted() {
                return Err(Error::Cancelled { index });
            }
            let (at, frame) = self.next_frame().map_err(|message| Error::Decode { index, message })?;
            keep(at, &frame);
            if at >= index {
                if at != index {
                    return Err(Error::Decode { index, message: format!("decoder skipped to frame {at}") });
                }
                return Ok(frame);
            }
        }
    }

    /// The next frame out of the decoder and its index.
    fn next_frame(&mut self) -> std::result::Result<(usize, Frame), String> {
        let (format, stream_index) = (self.input.format, self.input.stream_index);
        unsafe {
            loop {
                let code = ff::avcodec_receive_frame(self.codec, self.decoded);
                if code == 0 {
                    let decoded = &*self.decoded;
                    let pts = if decoded.best_effort_timestamp != ff::AV_NOPTS_VALUE { decoded.best_effort_timestamp } else { decoded.pts };
                    let at =
                        self.index.pts.binary_search(&pts).map_err(|_| format!("decoded a frame at pts {pts} that is not in the index"))?;
                    let frame = self.convert()?;
                    ff::av_frame_unref(self.decoded);
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
                let read = ff::av_read_frame(format, self.packet);
                if read == ff::AVERROR_EOF {
                    ff::avcodec_send_packet(self.codec, ptr::null());
                    continue;
                }
                if read < 0 {
                    return Err(ff_error(read));
                }
                if (*self.packet).stream_index == stream_index {
                    let sent = ff::avcodec_send_packet(self.codec, self.packet);
                    ff::av_packet_unref(self.packet);
                    if sent < 0 && sent != ff::AVERROR_EAGAIN {
                        return Err(ff_error(sent));
                    }
                } else {
                    ff::av_packet_unref(self.packet);
                }
            }
        }
    }

    /// The decoder's current frame: 8-bit RGBA when it has alpha or is RGB
    /// (see [`is_rgba_format`]), else tightly packed 8-bit 4:2:0.
    unsafe fn convert(&mut self) -> std::result::Result<Frame, String> {
        if is_rgba_format(unsafe { (*self.decoded).format }) { unsafe { self.convert_rgba() } } else { unsafe { self.convert_yuv() } }
    }

    /// The decoder's current frame as 8-bit RGBA, straight alpha (opaque
    /// when the frame has none), its code values as they are: swscale never
    /// premultiplies. RGBA itself is copied row by row.
    #[allow(clippy::unnecessary_cast)]
    unsafe fn convert_rgba(&mut self) -> std::result::Result<Frame, String> {
        let decoded = unsafe { &*self.decoded };
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
                let src = unsafe { std::slice::from_raw_parts(decoded.data[0].add(row * stride), width * 4) };
                rgba[row * width * 4..(row + 1) * width * 4].copy_from_slice(src);
            }
        } else {
            unsafe {
                self.sws = ff::sws_getCachedContext(
                    self.sws,
                    decoded.width,
                    decoded.height,
                    decoded.format,
                    decoded.width,
                    decoded.height,
                    ff::AV_PIX_FMT_RGBA,
                    ff::SWS_BICUBIC as i32,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null(),
                );
                if self.sws.is_null() {
                    return Err(format!("cannot convert pixel format {} to RGBA", decoded.format));
                }
                let mut planes = [rgba.as_mut_ptr(), ptr::null_mut(), ptr::null_mut(), ptr::null_mut()];
                let strides = [(width * 4) as i32, 0, 0, 0];
                ff::sws_scale(
                    self.sws,
                    decoded.data.as_ptr() as *const *const u8,
                    decoded.linesize.as_ptr(),
                    0,
                    decoded.height,
                    planes.as_mut_ptr(),
                    strides.as_ptr(),
                );
            }
        }
        Ok(Frame { width: width as u32, height: height as u32, pixels: Arc::new(Pixels::Rgba8(rgba)) })
    }

    /// The decoder's current frame as tightly packed 8-bit 4:2:0. Anything
    /// else (4:2:2 ProRes, 10-bit) is converted through swscale.
    // SWS_BICUBIC is a C enum constant, i32 in the Windows bindings and u32
    // elsewhere: its cast is a no-op only here.
    #[allow(clippy::unnecessary_cast)]
    unsafe fn convert_yuv(&mut self) -> std::result::Result<Frame, String> {
        let decoded = unsafe { &*self.decoded };
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
                    let src = unsafe { std::slice::from_raw_parts(decoded.data[plane].add(row * stride), w) };
                    out[row * w..(row + 1) * w].copy_from_slice(src);
                }
            }
        } else {
            let target = if full_range { ff::AV_PIX_FMT_YUVJ420P } else { ff::AV_PIX_FMT_YUV420P };
            unsafe {
                self.sws = ff::sws_getCachedContext(
                    self.sws,
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
                );
                if self.sws.is_null() {
                    return Err(format!("cannot convert pixel format {}", decoded.format));
                }
                let mut planes = [y.as_mut_ptr(), u.as_mut_ptr(), v.as_mut_ptr(), ptr::null_mut()];
                let strides = [width as i32, cw as i32, cw as i32, 0];
                ff::sws_scale(
                    self.sws,
                    decoded.data.as_ptr() as *const *const u8,
                    decoded.linesize.as_ptr(),
                    0,
                    decoded.height,
                    planes.as_mut_ptr(),
                    strides.as_ptr(),
                );
            }
        }
        Ok(Frame { width: width as u32, height: height as u32, pixels: Arc::new(Pixels::Yuv420 { y, u, v, matrix, full_range }) })
    }
}

impl Drop for MovieDecoder {
    fn drop(&mut self) {
        unsafe {
            ff::sws_freeContext(self.sws);
            ff::av_frame_free(&mut self.decoded);
            ff::av_packet_free(&mut self.packet);
            ff::avcodec_free_context(&mut self.codec);
        }
    }
}
