//! An EXR image sequence: one file per frame, so any frame is exactly one
//! read and stepping needs no decoder state at all.
//!
//! Channels are grouped into layers by the text before their last `.`
//! (`beauty.R` is channel `R` of layer `beauty`). A channel without a prefix
//! belongs to its part's name, or to the unnamed layer when the part has
//! none. TumblePipe renders one AOV per file, so a file usually has one
//! layer, but nothing here assumes it.
//!
//! A frame is the file's display window. Data outside it is cut off and
//! display pixels the data window does not cover are zero, so a cropped
//! render lines up with a full one.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use exr::image::FlatImage;
use exr::prelude::{FlatSamples, ReadChannels, ReadLayers};
use half::f16;

use crate::frame::{Frame, Pixels};
use crate::{Error, Result};

/// A layer of an EXR file and its channel names, without the layer prefix,
/// in file order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExrLayer {
    pub name: String,
    pub channels: Vec<String>,
}

/// What to show of a sequence: a layer's colour, or one of its channels.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub struct ExrView {
    pub layer: String,
    /// One channel shown as grey; None shows the layer's colour.
    pub channel: Option<String>,
}

/// The frame files of a sequence, in playback order: index `i` is frame
/// number `first + i * step`. A frame missing on disk is an error when it is
/// decoded, never a shifted frame.
#[derive(Debug, Clone)]
pub struct ExrSequence {
    first: i64,
    step: i64,
    frames: Vec<PathBuf>,
}

/// What a sequence's first frame says: its layers, in the order their first
/// channel appears, and the colour space it is in (OIIO's `oiio:ColorSpace`,
/// which our renders carry), if it says.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExrHeader {
    pub layers: Vec<ExrLayer>,
    pub colour_space: Option<String>,
}

/// The layer and in-layer name of channel `full` of the part named `part`.
fn channel_address(full: &str, part: Option<&str>) -> (String, String) {
    match full.rsplit_once('.') {
        Some((layer, name)) => (layer.to_owned(), name.to_owned()),
        None => (part.unwrap_or_default().to_owned(), full.to_owned()),
    }
}

/// How much of a file is read between asking whether it is still wanted:
/// small enough that reads no longer wanted stop within a second when six
/// share a slow link.
const PIECE: u64 = 1 << 20;

/// The file at `path`, read whole in pieces of [`PIECE`]; None once `wanted`
/// says to stop.
fn read_whole(path: &Path, wanted: &dyn Fn() -> bool) -> std::io::Result<Option<Vec<u8>>> {
    let mut file = std::fs::File::open(path)?;
    let size = file.metadata().map_or(0, |m| m.len());
    let mut bytes = Vec::with_capacity(usize::try_from(size).unwrap_or(0));
    loop {
        if !wanted() {
            return Ok(None);
        }
        // A short piece is the file's last.
        if ((&mut file).take(PIECE).read_to_end(&mut bytes)? as u64) < PIECE {
            return Ok(Some(bytes));
        }
    }
}

/// Every layer of the file at `path`; None once `wanted` says to stop. The
/// file is read whole first: the decoder's own reads are 8 KB each, a round
/// trip apiece on a file server (a 40 MB frame took 28 s from the NAS that
/// way, against 6 s to read it whole and 50 ms to decode it).
fn read_flat(path: &Path, wanted: &dyn Fn() -> bool) -> std::result::Result<Option<FlatImage>, String> {
    let Some(bytes) = read_whole(path, wanted).map_err(|e| e.to_string())? else {
        return Ok(None);
    };
    exr::prelude::read()
        .no_deep_data()
        .largest_resolution_level()
        .all_channels()
        .all_layers()
        .all_attributes()
        .from_buffered(std::io::Cursor::new(bytes))
        .map(Some)
        .map_err(|e| e.to_string())
}

impl ExrLayer {
    /// The channels `channel` shows, as indices into `channels`, in display
    /// order: that one channel; else the colour channels R,G,B (X,Y,Z for a
    /// vector layer), whatever their case; else, for a layer that is not
    /// colour (a matte, a depth pass), its first channel. None when the layer
    /// has no channel `channel`, or no channels at all.
    pub fn shown(&self, channel: Option<&str>) -> Option<Vec<usize>> {
        let find = |n: &str| self.channels.iter().position(|c| c.eq_ignore_ascii_case(n));
        if let Some(wanted) = channel {
            return self.channels.iter().position(|c| c == wanted).map(|i| vec![i]);
        }
        for [r, g, b] in [["r", "g", "b"], ["x", "y", "z"]] {
            if let (Some(r), Some(g), Some(b)) = (find(r), find(g), find(b)) {
                return Some(vec![r, g, b]);
            }
        }
        (!self.channels.is_empty()).then(|| vec![0])
    }
}

impl ExrSequence {
    /// The sequence of `frames`, numbered from `first` by `step`.
    pub fn new(first: i64, step: i64, frames: Vec<PathBuf>) -> Self {
        Self { first, step, frames }
    }

    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    /// The frame number of index `index`.
    pub fn frame_number(&self, index: usize) -> i64 {
        self.first + index as i64 * self.step
    }

    /// What it is read from, for messages: its first frame.
    pub fn describe(&self) -> String {
        self.frames.first().map_or_else(String::new, |p| p.display().to_string())
    }

    /// Its first frame's header.
    pub fn header(&self) -> Result<ExrHeader> {
        let path = self.frames.first().ok_or_else(|| Error::Open { path: String::new(), message: "no frames".into() })?;
        let meta = exr::meta::MetaData::read_from_file(path, false)
            .map_err(|e| Error::Open { path: path.display().to_string(), message: e.to_string() })?;
        let mut layers: Vec<ExrLayer> = Vec::new();
        for header in &meta.headers {
            let part = header.own_attributes.layer_name.as_ref().map(ToString::to_string);
            for channel in &header.channels.list {
                let (layer, name) = channel_address(&channel.name.to_string(), part.as_deref());
                match layers.iter_mut().find(|l| l.name == layer) {
                    Some(existing) => existing.channels.push(name),
                    None => layers.push(ExrLayer { name: layer, channels: vec![name] }),
                }
            }
        }
        let key = exr::meta::attribute::Text::from("oiio:ColorSpace");
        let colour_space = meta.headers.iter().find_map(|h| {
            let value = h.own_attributes.other.get(&key).or_else(|| h.shared_attributes.other.get(&key))?;
            match value {
                exr::meta::attribute::AttributeValue::Text(text) => Some(text.to_string()),
                _ => None,
            }
        });
        Ok(ExrHeader { layers, colour_space })
    }

    /// The view a sequence opens on: its beauty (the unnamed layer, or one
    /// with colour channels) as colour, else its first layer.
    pub fn default_view(layers: &[ExrLayer]) -> ExrView {
        let colour = |l: &&ExrLayer| l.shown(None).is_some_and(|s| s.len() == 3);
        let layer = layers.iter().find(|l| l.name.is_empty() && colour(l)).or_else(|| layers.iter().find(colour)).or(layers.first());
        ExrView { layer: layer.map(|l| l.name.clone()).unwrap_or_default(), channel: None }
    }

    /// Decode frame `index`, showing `view`.
    pub fn decode(&self, index: usize, view: &ExrView) -> Result<Frame> {
        self.decode_while(index, view, &|| true)
    }

    /// [`decode`](Self::decode), stopping with [`Error::Cancelled`] once
    /// `wanted` says the frame is no longer wanted (it asks between the
    /// pieces of the file as it reads them). The frame is RGBA: a single
    /// channel shows as grey, and alpha is opaque.
    pub fn decode_while(&self, index: usize, view: &ExrView, wanted: &dyn Fn() -> bool) -> Result<Frame> {
        let count = self.frames.len();
        let path = self.frames.get(index).ok_or(Error::OutOfRange { index, count })?;
        let fail = |message: String| Error::Decode { index, message: format!("{}: {message}", path.display()) };
        let image = read_flat(path, wanted).map_err(fail)?.ok_or(Error::Cancelled { index })?;
        let display = image.attributes.display_window;

        // A layer's channels can be spread over parts; gather them first.
        let mut layer = ExrLayer { name: view.layer.clone(), channels: Vec::new() };
        let mut samples = Vec::new();
        for data in &image.layer_data {
            let part = data.attributes.layer_name.as_ref().map(ToString::to_string);
            for c in &data.channel_data.list {
                let (l, name) = channel_address(&c.name.to_string(), part.as_deref());
                if l == view.layer {
                    layer.channels.push(name);
                    samples.push((data, &c.sample_data));
                }
            }
        }
        if samples.is_empty() {
            return Err(fail(format!("no layer '{}'", view.layer)));
        }
        let picked = layer
            .shown(view.channel.as_deref())
            .ok_or_else(|| fail(format!("layer '{}' has no channel '{}'", view.layer, view.channel.as_deref().unwrap_or_default())))?;

        let (width, height) = (display.size.0, display.size.1);
        let mut out = vec![f16::ZERO; width * height * 4];
        for pixel in out.as_chunks_mut::<4>().0 {
            pixel[3] = f16::ONE;
        }
        // The RGBA slots each shown channel fills: one channel is grey.
        let slots: &[&[usize]] = if picked.len() == 1 { &[&[0, 1, 2]] } else { &[&[0], &[1], &[2]] };
        for (&which, targets) in picked.iter().zip(slots) {
            let (data, values) = samples[which];
            // Where the data window sits in the display window.
            let origin = (
                i64::from(data.attributes.layer_position.0) - i64::from(display.position.0),
                i64::from(data.attributes.layer_position.1) - i64::from(display.position.1),
            );
            let (data_width, data_height) = (data.size.0, data.size.1);
            let sample = |i: usize| match values {
                FlatSamples::F16(v) => v[i],
                FlatSamples::F32(v) => f16::from_f32(v[i]),
                FlatSamples::U32(v) => f16::from_f32(v[i] as f32),
            };
            for row in 0..data_height {
                let y = origin.1 + row as i64;
                if y < 0 || y >= height as i64 {
                    continue;
                }
                for col in 0..data_width {
                    let x = origin.0 + col as i64;
                    if x < 0 || x >= width as i64 {
                        continue;
                    }
                    let value = sample(row * data_width + col);
                    for &slot in *targets {
                        out[(y as usize * width + x as usize) * 4 + slot] = value;
                    }
                }
            }
        }
        Ok(Frame { width: width as u32, height: height as u32, pixels: Arc::new(Pixels::Rgba16f(out)) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::cell::Cell;

    /// File sizes on and around piece boundaries (where a short piece is
    /// first mistaken for the end, or the end for a full piece), and a few
    /// anywhere.
    fn sizes() -> impl Strategy<Value = usize> {
        let piece = PIECE as usize;
        prop_oneof![(0usize..4, -2isize..=2).prop_map(move |(k, d)| (k * piece).saturating_add_signed(d)), 0usize..3 * piece,]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(24))]

        /// A file read whole is the file, whatever its size; a read told to
        /// stop after `allowed` pieces stops exactly when the file needs more.
        #[test]
        fn a_whole_read_is_the_file_or_stops_where_asked(size in sizes(), seed in any::<u8>(), allowed in 0usize..6) {
            let bytes: Vec<u8> = (0..size).map(|i| (i as u8).wrapping_mul(31).wrapping_add(seed)).collect();
            let path = std::env::temp_dir().join(format!("th_media_read_whole_{}_{size}_{seed}.bin", std::process::id()));
            std::fs::write(&path, &bytes).unwrap();
            let read = read_whole(&path, &|| true).unwrap();
            let checks = Cell::new(0usize);
            let limited = read_whole(&path, &|| {
                checks.set(checks.get() + 1);
                checks.get() <= allowed
            })
            .unwrap();
            std::fs::remove_file(&path).unwrap();
            prop_assert!(read.as_deref() == Some(&bytes[..]), "read whole: {:?} of {} bytes", read.as_ref().map(Vec::len), size);
            // The pieces a read takes: every full one, then the short last.
            let pieces = size / PIECE as usize + 1;
            prop_assert_eq!(limited.is_some(), pieces <= allowed, "{} pieces, {} allowed", pieces, allowed);
        }
    }
}
