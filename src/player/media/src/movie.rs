//! A movie file decoded frame-accurately through the vendored FFmpeg.
//!
//! On open the file's video packets are read once, without decoding, to build
//! the frame index: every frame's presentation timestamp in display order and
//! which frames are keyframes. Frame `i` is then the `i`-th smallest
//! timestamp, never "the frame nearest `i / fps` seconds", so rounding can
//! never land a step on the wrong frame.
//!
//! Decoding frame `i` either continues forward from where the decoder stopped
//! (when that is no more work) or seeks to the last keyframe at or before `i`
//! and decodes forward. Every frame decoded on the way is handed to the
//! caller, which keeps them in the frame buffer: a TumblePipe playblast has a
//! single keyframe, so stepping back from its last frame decodes the whole
//! shot once, and every step after that is free.

use std::ffi::{CStr, CString};
use std::ptr;
use std::sync::Arc;

use th_ffmpeg_sys as ff;

use crate::frame::{Frame, Pixels, YuvMatrix};
use crate::{Error, Result};

pub struct MovieSource {
    path: String,
    format: *mut ff::AVFormatContext,
    codec: *mut ff::AVCodecContext,
    packet: *mut ff::AVPacket,
    decoded: *mut ff::AVFrame,
    sws: *mut ff::SwsContext,
    stream_index: i32,
    /// Presentation timestamps in display order: frame `i` is `pts[i]`.
    pts: Vec<i64>,
    /// Indices (into `pts`) of the keyframes, ascending.
    keyframes: Vec<usize>,
    /// The last frame the decoder produced, when it can continue from there.
    position: Option<usize>,
    width: u32,
    height: u32,
    rate: (i32, i32),
}

// The FFmpeg contexts are owned exclusively by this value and only touched
// through `&mut self`; moving it to a decode thread is sound.
unsafe impl Send for MovieSource {}

fn ff_error(code: i32) -> String {
    let mut buf = [0 as std::ffi::c_char; 256];
    unsafe { ff::av_strerror(code, buf.as_mut_ptr(), buf.len()) };
    unsafe { CStr::from_ptr(buf.as_ptr()) }.to_string_lossy().into_owned()
}

impl MovieSource {
    pub fn open(path: &str) -> Result<Self> {
        let fail = |message: String| Error::Open { path: path.to_owned(), message };
        let c_path = CString::new(path).map_err(|_| fail("path contains a NUL".into()))?;
        let mut source = MovieSource {
            path: path.to_owned(),
            format: ptr::null_mut(),
            codec: ptr::null_mut(),
            packet: ptr::null_mut(),
            decoded: ptr::null_mut(),
            sws: ptr::null_mut(),
            stream_index: -1,
            pts: Vec::new(),
            keyframes: Vec::new(),
            position: None,
            width: 0,
            height: 0,
            rate: (24, 1),
        };
        unsafe {
            let code = ff::avformat_open_input(&mut source.format, c_path.as_ptr(), ptr::null(), ptr::null_mut());
            if code < 0 {
                return Err(fail(ff_error(code)));
            }
            let code = ff::avformat_find_stream_info(source.format, ptr::null_mut());
            if code < 0 {
                return Err(fail(ff_error(code)));
            }
            let mut decoder: *const ff::AVCodec = ptr::null();
            let index = ff::av_find_best_stream(source.format, ff::AVMEDIA_TYPE_VIDEO, -1, -1, &mut decoder, 0);
            if index < 0 || decoder.is_null() {
                return Err(fail(format!("no decodable video stream ({})", ff_error(index))));
            }
            source.stream_index = index;
            let stream = *(*source.format).streams.add(index as usize);
            source.codec = ff::avcodec_alloc_context3(decoder);
            ff::avcodec_parameters_to_context(source.codec, (*stream).codecpar);
            // Decode on several threads, but frame threading delays output by a
            // frame per thread; slice threading keeps one-in, one-out.
            (*source.codec).thread_count = 0;
            (*source.codec).thread_type = ff::FF_THREAD_SLICE as i32;
            let code = ff::avcodec_open2(source.codec, decoder, ptr::null_mut());
            if code < 0 {
                return Err(fail(ff_error(code)));
            }
            source.packet = ff::av_packet_alloc();
            source.decoded = ff::av_frame_alloc();
            source.width = (*source.codec).width as u32;
            source.height = (*source.codec).height as u32;
            let rate = (*stream).avg_frame_rate;
            if rate.num > 0 && rate.den > 0 {
                source.rate = (rate.num, rate.den);
            }
        }
        source.build_index().map_err(fail)?;
        Ok(source)
    }

    /// Read every packet of the video stream once (no decoding) and record
    /// each frame's timestamp and whether it is a keyframe.
    fn build_index(&mut self) -> std::result::Result<(), String> {
        let mut entries: Vec<(i64, bool)> = Vec::new();
        unsafe {
            loop {
                let code = ff::av_read_frame(self.format, self.packet);
                if code == ff::AVERROR_EOF {
                    break;
                }
                if code < 0 {
                    return Err(ff_error(code));
                }
                let packet = &*self.packet;
                if packet.stream_index == self.stream_index {
                    let pts = if packet.pts != ff::AV_NOPTS_VALUE { packet.pts } else { packet.dts };
                    let key = packet.flags & ff::AV_PKT_FLAG_KEY as i32 != 0;
                    entries.push((pts, key));
                }
                ff::av_packet_unref(self.packet);
            }
        }
        if entries.is_empty() {
            return Err("the video stream has no frames".into());
        }
        entries.sort_by_key(|&(pts, _)| pts);
        self.pts = entries.iter().map(|&(pts, _)| pts).collect();
        self.keyframes = entries.iter().enumerate().filter(|(_, (_, key))| *key).map(|(i, _)| i).collect();
        if self.keyframes.first() != Some(&0) {
            // A stream that does not open on a keyframe still decodes from its start.
            self.keyframes.insert(0, 0);
        }
        self.seek_to(0)
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    pub fn frame_count(&self) -> usize {
        self.pts.len()
    }

    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    /// Frames per second as a ratio (`(24, 1)`, `(24000, 1001)`).
    pub fn rate(&self) -> (i32, i32) {
        self.rate
    }

    /// Indices of the keyframes, ascending.
    pub fn keyframes(&self) -> &[usize] {
        &self.keyframes
    }

    fn keyframe_at_or_before(&self, index: usize) -> usize {
        let at = self.keyframes.partition_point(|&k| k <= index);
        self.keyframes[at.saturating_sub(1)]
    }

    fn seek_to(&mut self, keyframe: usize) -> std::result::Result<(), String> {
        unsafe {
            let code = ff::av_seek_frame(self.format, self.stream_index, self.pts[keyframe], ff::AVSEEK_FLAG_BACKWARD as i32);
            if code < 0 {
                return Err(ff_error(code));
            }
            ff::avcodec_flush_buffers(self.codec);
        }
        self.position = None;
        Ok(())
    }

    /// Decode frame `index`, handing it and every frame decoded on the way to
    /// `keep` (so the caller can buffer them), and return it.
    pub fn decode(&mut self, index: usize, mut keep: impl FnMut(usize, &Frame)) -> Result<Frame> {
        let count = self.frame_count();
        if index >= count {
            return Err(Error::OutOfRange { index, count });
        }
        let keyframe = self.keyframe_at_or_before(index);
        // Continue forward when the decoder already sits between that keyframe
        // and the target: it is never more work than seeking back.
        let resume = matches!(self.position, Some(at) if at < index && at >= keyframe);
        if !resume {
            self.seek_to(keyframe).map_err(|message| Error::Decode { index, message })?;
        }
        loop {
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
        unsafe {
            loop {
                let code = ff::avcodec_receive_frame(self.codec, self.decoded);
                if code == 0 {
                    let decoded = &*self.decoded;
                    let pts = if decoded.best_effort_timestamp != ff::AV_NOPTS_VALUE {
                        decoded.best_effort_timestamp
                    } else {
                        decoded.pts
                    };
                    let at = self
                        .pts
                        .binary_search(&pts)
                        .map_err(|_| format!("decoded a frame at pts {pts} that is not in the index"))?;
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
                let read = ff::av_read_frame(self.format, self.packet);
                if read == ff::AVERROR_EOF {
                    ff::avcodec_send_packet(self.codec, ptr::null());
                    continue;
                }
                if read < 0 {
                    return Err(ff_error(read));
                }
                if (*self.packet).stream_index == self.stream_index {
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

    /// The decoder's current frame as tightly packed 8-bit 4:2:0. Anything
    /// else (4:2:2 ProRes, 10-bit) is converted through swscale.
    // SWS_BICUBIC is a C enum constant, i32 in the Windows bindings and u32
    // elsewhere: its cast is a no-op only here.
    #[allow(clippy::unnecessary_cast)]
    unsafe fn convert(&mut self) -> std::result::Result<Frame, String> {
        let decoded = unsafe { &*self.decoded };
        let (width, height) = (decoded.width as usize, decoded.height as usize);
        let full_range = decoded.color_range == ff::AVCOL_RANGE_JPEG
            || decoded.format == ff::AV_PIX_FMT_YUVJ420P;
        let matrix = match decoded.colorspace {
            ff::AVCOL_SPC_BT470BG | ff::AVCOL_SPC_SMPTE170M => YuvMatrix::Bt601,
            ff::AVCOL_SPC_BT709 => YuvMatrix::Bt709,
            // Unspecified: HD and up is BT.709 by convention, SD BT.601.
            _ if height >= 720 => YuvMatrix::Bt709,
            _ => YuvMatrix::Bt601,
        };
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
        Ok(Frame {
            width: width as u32,
            height: height as u32,
            pixels: Arc::new(Pixels::Yuv420 { y, u, v, matrix, full_range }),
        })
    }
}

impl Drop for MovieSource {
    fn drop(&mut self) {
        unsafe {
            ff::sws_freeContext(self.sws);
            ff::av_frame_free(&mut self.decoded);
            ff::av_packet_free(&mut self.packet);
            ff::avcodec_free_context(&mut self.codec);
            ff::avformat_close_input(&mut self.format);
        }
    }
}

impl std::fmt::Debug for MovieSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MovieSource")
            .field("path", &self.path)
            .field("frames", &self.pts.len())
            .field("keyframes", &self.keyframes.len())
            .finish_non_exhaustive()
    }
}
