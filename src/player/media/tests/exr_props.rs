//! Properties of `ExrSequence`: which channels a view shows, and that every
//! layer and channel it lists decodes to the samples that were written.
//!
//! The round trip writes small EXRs with the `exr` crate: random layers
//! (named, unnamed, nested), as prefixed channels of one part or as parts of
//! their own, f16 or f32 samples, a data window offset into or cropped by the
//! display window. Each sample is a small integer naming its channel and
//! pixel, exact in a half float, so a decoded value says where it came from.

use std::collections::HashSet;
use std::path::PathBuf;

use exr::prelude::*;
use proptest::prelude::*;
use th_media::{ExrLayer, ExrSequence, ExrView, Pixels, sequence_frames, split_frame_name};

/// Channel names a layer is drawn from: colour, vector and odd ones, in two
/// cases.
const NAMES: &[&str] = &["R", "G", "B", "A", "r", "g", "b", "X", "Y", "Z", "x", "y", "z", "id", "depth", "coverage"];
const LAYERS: &[&str] = &["beauty", "N", "diffuse.direct", "crypto00", "P"];

fn layer(names: Vec<&'static str>) -> ExrLayer {
    ExrLayer { name: String::new(), channels: names.into_iter().map(str::to_owned).collect() }
}

fn channel_names() -> impl Strategy<Value = Vec<&'static str>> {
    prop::sample::subsequence(NAMES, 0..=NAMES.len()).prop_shuffle()
}

/// The first channel equal (ignoring case) to each of `wanted`.
fn all_of(layer: &ExrLayer, wanted: [&str; 3]) -> Option<Vec<usize>> {
    wanted.iter().map(|w| layer.channels.iter().position(|c| c.eq_ignore_ascii_case(w))).collect()
}

proptest! {
    #[test]
    fn a_named_channel_shows_alone(names in channel_names(), wanted in prop::sample::select(NAMES)) {
        let layer = layer(names);
        let expected = layer.channels.iter().position(|c| c == wanted).map(|i| vec![i]);
        prop_assert_eq!(layer.shown(Some(wanted)), expected);
    }

    #[test]
    fn the_colour_view_prefers_rgb_then_xyz_then_the_first_channel(names in channel_names()) {
        let layer = layer(names);
        let shown = layer.shown(None);
        if layer.channels.is_empty() {
            prop_assert_eq!(shown, None);
        } else if let Some(rgb) = all_of(&layer, ["r", "g", "b"]) {
            prop_assert_eq!(shown, Some(rgb));
        } else if let Some(xyz) = all_of(&layer, ["x", "y", "z"]) {
            prop_assert_eq!(shown, Some(xyz));
        } else {
            prop_assert_eq!(shown, Some(vec![0]));
        }
    }
}

/// One layer to write: its name and channels, each channel f32 or f16.
#[derive(Debug, Clone)]
struct LayerSpec {
    name: String,
    channels: Vec<(String, bool)>,
}

#[derive(Debug, Clone)]
struct ImageSpec {
    display: (i32, i32, usize, usize),
    data: (i32, i32, usize, usize),
    layers: Vec<LayerSpec>,
    /// One part per layer, channels unprefixed, instead of one part.
    multipart: bool,
}

fn image_spec() -> impl Strategy<Value = ImageSpec> {
    let layers = prop::collection::vec(
        (
            prop::sample::select(LAYERS).prop_map(str::to_owned),
            prop::collection::vec((prop::sample::select(NAMES).prop_map(str::to_owned), any::<bool>()), 1..5),
        ),
        1..4,
    );
    (
        (-3i32..3, -3i32..3, 1usize..7, 1usize..7),
        (-3i32..3, -3i32..3, 1usize..7, 1usize..7),
        layers,
        any::<bool>(),
        any::<bool>(),
    )
        .prop_map(|(display, data, layers, multipart, with_unnamed)| {
            // Unique layer names, unique channels per layer.
            let mut seen = HashSet::new();
            let mut layers: Vec<LayerSpec> = layers
                .into_iter()
                .filter(|(name, _)| seen.insert(name.clone()))
                .map(|(name, channels)| {
                    let mut seen = HashSet::new();
                    LayerSpec { name, channels: channels.into_iter().filter(|(c, _)| seen.insert(c.clone())).collect() }
                })
                .collect();
            // A single-part file may also hold an unnamed layer (bare R, G, B).
            if with_unnamed && !multipart {
                layers.push(LayerSpec {
                    name: String::new(),
                    channels: vec![("R".into(), true), ("G".into(), false), ("B".into(), true)],
                });
            }
            ImageSpec { display, data, layers, multipart }
        })
}

/// The value written at data-window pixel `pixel` of channel `channel` of
/// layer `layer`: below 2048, so exact as a half float.
fn value(layer: usize, channel: usize, pixel: usize) -> f32 {
    ((layer * 8 + channel) * 49 + pixel) as f32
}

fn samples(spec: &ImageSpec, layer: usize, channel: usize, f32_samples: bool) -> FlatSamples {
    let count = spec.data.2 * spec.data.3;
    let values = (0..count).map(|p| value(layer, channel, p));
    if f32_samples { FlatSamples::F32(values.collect()) } else { FlatSamples::F16(values.map(f16::from_f32).collect()) }
}

fn write(spec: &ImageSpec, path: &std::path::Path) {
    let (dx, dy, dw, dh) = spec.display;
    let (x, y, w, h) = spec.data;
    let mut attributes = ImageAttributes::new(IntegerBounds::new((dx, dy), (dw, dh)));
    attributes.display_window = IntegerBounds::new((dx, dy), (dw, dh));
    let layer_at = |name: Option<&str>| {
        let mut a = match name {
            Some(n) => LayerAttributes::named(n),
            None => LayerAttributes::default(),
        };
        a.layer_position = Vec2(x, y);
        a
    };
    let mut parts: Vec<Layer<AnyChannels<FlatSamples>>> = Vec::new();
    if spec.multipart {
        for (li, l) in spec.layers.iter().enumerate() {
            let channels = l
                .channels
                .iter()
                .enumerate()
                .map(|(ci, (c, f))| AnyChannel::new(c.as_str(), samples(spec, li, ci, *f)))
                .collect();
            parts.push(Layer::new((w, h), layer_at(Some(&l.name)), Encoding::FAST_LOSSLESS, AnyChannels::sort(channels)));
        }
    } else {
        let mut channels = SmallVec::new();
        for (li, l) in spec.layers.iter().enumerate() {
            for (ci, (c, f)) in l.channels.iter().enumerate() {
                let full = if l.name.is_empty() { c.clone() } else { format!("{}.{c}", l.name) };
                channels.push(AnyChannel::new(full.as_str(), samples(spec, li, ci, *f)));
            }
        }
        parts.push(Layer::new((w, h), layer_at(None), Encoding::FAST_LOSSLESS, AnyChannels::sort(channels)));
    }
    Image { attributes, layer_data: SmallVec::from_vec(parts) }.write().to_file(path).unwrap();
}

fn temp_path(tag: u64) -> PathBuf {
    let dir = std::env::temp_dir().join("th_media_exr_props");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(format!("{}_{tag}.exr", std::process::id()))
}

fn sorted(mut v: Vec<String>) -> Vec<String> {
    v.sort();
    v
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 96, ..ProptestConfig::default() })]

    /// `layers()` lists what was written, and every view of every listed
    /// layer decodes to the written samples, placed by the data window
    /// inside the display window and zero elsewhere.
    #[test]
    fn every_listed_view_decodes_what_was_written(spec in image_spec(), tag in any::<u64>()) {
        let path = temp_path(tag);
        write(&spec, &path);
        let sequence = ExrSequence::new(vec![path.clone()]);
        let layers = sequence.layers().unwrap();

        let listed: Vec<(String, Vec<String>)> =
            sorted_layers(layers.iter().map(|l| (l.name.clone(), sorted(l.channels.clone()))).collect());
        let written: Vec<(String, Vec<String>)> = sorted_layers(
            spec.layers.iter().map(|l| (l.name.clone(), sorted(l.channels.iter().map(|c| c.0.clone()).collect()))).collect(),
        );
        prop_assert_eq!(listed, written);

        let (dx, dy, dw, dh) = spec.display;
        let (x, y, w, h) = spec.data;
        for layer in &layers {
            let li = spec.layers.iter().position(|l| l.name == layer.name).unwrap();
            let views = std::iter::once(None).chain(layer.channels.iter().map(|c| Some(c.clone())));
            for channel in views {
                let view = ExrView { layer: layer.name.clone(), channel: channel.clone() };
                let frame = sequence.decode(0, &view).unwrap();
                prop_assert_eq!((frame.width as usize, frame.height as usize), (dw, dh));
                let Pixels::Half { data, channels } = &*frame.pixels else { panic!("an EXR frame is half floats") };
                let shown = layer.shown(channel.as_deref()).unwrap();
                prop_assert_eq!(*channels, shown.len());
                for (slot, &which) in shown.iter().enumerate() {
                    let name = &layer.channels[which];
                    let ci = spec.layers[li].channels.iter().position(|c| &c.0 == name).unwrap();
                    for py in 0..dh {
                        for px in 0..dw {
                            // Display pixel -> data-window pixel, if it has one.
                            let (ax, ay) = (dx + px as i32 - x, dy + py as i32 - y);
                            let expected = if ax >= 0 && ay >= 0 && (ax as usize) < w && (ay as usize) < h {
                                value(li, ci, ay as usize * w + ax as usize)
                            } else {
                                0.0
                            };
                            let got = data[(py * dw + px) * channels + slot].to_f32();
                            prop_assert_eq!(got, expected, "{:?} channel {} at display ({}, {})", view, name, px, py);
                        }
                    }
                }
            }
        }
        let missing = ExrView { layer: "no such layer".into(), channel: None };
        prop_assert!(sequence.decode(0, &missing).is_err());
        let _ = std::fs::remove_file(path);
    }
}

fn sorted_layers(mut v: Vec<(String, Vec<String>)>) -> Vec<(String, Vec<String>)> {
    v.sort();
    v
}

/// The view a sequence opens on is its beauty.
#[test]
fn default_view_is_the_beauty() {
    let l = |name: &str, channels: &[&str]| ExrLayer { name: name.into(), channels: channels.iter().map(|&c| c.into()).collect() };
    let view = |layers: &[ExrLayer]| ExrSequence::default_view(layers).layer;
    assert_eq!(view(&[l("N", &["X", "Y", "Z"]), l("", &["A", "B", "G", "R"])]), "");
    assert_eq!(view(&[l("", &["Z"]), l("beauty", &["R", "G", "B"])]), "beauty");
    assert_eq!(view(&[l("depth", &["Z"])]), "depth");
    assert_eq!(view(&[]), "");
}

/// A frame-file name around a frame number: a prefix that does not end in a
/// digit (so the number's run starts where it should) and a suffix with no
/// digits.
fn name_parts() -> impl Strategy<Value = (String, String)> {
    ("[a-zA-Z0-9_]{0,12}[._a-z]|", "[._]?(exr|EXR|tif)?")
}

proptest! {
    #[test]
    fn split_frame_name_finds_the_last_number((prefix, suffix) in name_parts(), number in 0i64..100_000, pad in 1usize..8) {
        let name = format!("{prefix}{number:0pad$}{suffix}");
        prop_assert_eq!(split_frame_name(&name), Some((prefix.as_str(), number, suffix.as_str())));
    }

    /// Scanning keeps every frame of the sequence in its numbered slot,
    /// gaps as None, and ignores other sequences in the same folder.
    #[test]
    fn sequence_frames_slots_by_number(
        (prefix, suffix) in name_parts(),
        numbers in prop::collection::btree_set(0i64..300, 1..40),
        pad in 1usize..6,
        given in any::<prop::sample::Index>(),
    ) {
        let name = |n: i64| format!("{prefix}{n:0pad$}{suffix}");
        let mut names: Vec<String> = numbers.iter().map(|&n| name(n)).collect();
        // Another AOV's frames, and a stray file.
        names.extend(numbers.iter().map(|&n| format!("other_{n:04}.exr")));
        names.push("notes.txt".into());
        let numbers: Vec<i64> = numbers.into_iter().collect();
        let picked = name(numbers[given.index(numbers.len())]);
        let (first, frames) = sequence_frames(&names, &picked);
        prop_assert_eq!(first, numbers[0]);
        prop_assert_eq!(frames.len() as i64, numbers.last().unwrap() - numbers[0] + 1);
        for (slot, frame) in frames.iter().enumerate() {
            let n = first + slot as i64;
            let expected = numbers.contains(&n).then(|| name(n));
            prop_assert_eq!(frame, &expected);
        }
    }
}

#[test]
fn a_gap_is_a_decode_error_not_a_shifted_frame() {
    let sequence = ExrSequence::with_gaps(1001, vec![None]);
    assert!(sequence.decode(0, &ExrView::default()).is_err());
    assert_eq!(sequence.first_frame(), 1001);
}

/// A decode stops once it is no longer wanted: at once when it never was,
/// and between the pieces of a large file when that changes mid-read.
#[test]
fn a_decode_no_longer_wanted_stops() {
    let path = temp_path(u64::MAX);
    // 1024 x 1024 RGBA f32, uncompressed: 16 MB, read in several pieces.
    let channels = SpecificChannels::rgba(|_: Vec2<usize>| (0.5_f32, 0.25_f32, 0.125_f32, 1.0_f32));
    let layer = Layer::new((1024, 1024), LayerAttributes::default(), Encoding::UNCOMPRESSED, channels);
    Image::from_layer(layer).write().to_file(&path).unwrap();
    let sequence = ExrSequence::new(vec![path.clone()]);
    let view = ExrView::default();

    assert!(matches!(sequence.decode_while(0, &view, &|| false), Err(th_media::Error::Cancelled { index: 0 })));
    let asked = std::cell::Cell::new(0);
    let first_piece_only = || {
        asked.set(asked.get() + 1);
        asked.get() == 1
    };
    assert!(matches!(sequence.decode_while(0, &view, &first_piece_only), Err(th_media::Error::Cancelled { .. })));
    assert_eq!(asked.get(), 2, "it stopped after the first piece");
    let frame = sequence.decode_while(0, &view, &|| true).unwrap();
    assert_eq!((frame.width, frame.height), (1024, 1024));
    std::fs::remove_file(&path).ok();
}
