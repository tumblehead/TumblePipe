//! Media is plain data once open: its description (frame count, rate, frame
//! numbers) reads from any thread while a decoder reads the same file, never
//! waiting for the decode. The UI reads it on every redraw (the burn-ins), so
//! a description behind the decoder would freeze the window for as long as a
//! decode takes (a single-keyframe playblast decodes its whole shot on a step
//! back).

use std::path::PathBuf;
use std::sync::{Arc, mpsc};
use std::time::Duration;

use exr::prelude::*;
use th_media::{ExrSequence, Media, MovieDecoder};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

/// Media can be shared with the decode threads and read there.
#[test]
fn media_is_shared_freely() {
    fn shared<T: Send + Sync>() {}
    shared::<Media>();
}

/// Read the media's description from another thread, mid-decode, and give up
/// after a while rather than hang the suite.
#[test]
fn the_description_reads_while_a_decode_runs() {
    let media = Arc::new(Media::movie(&fixture("grey_ramp_single.mp4")).unwrap());
    let Media::Movie(index) = &*media else { panic!("a movie") };
    let mut decoder = MovieDecoder::open(Arc::clone(index), 2).unwrap();
    let mut read = Vec::new();
    decoder
        .decode(
            47,
            |at, _| {
                if at == 20 {
                    let (tx, rx) = mpsc::channel();
                    let reader = Arc::clone(&media);
                    std::thread::spawn(move || {
                        let _ = tx.send((reader.frame_count(), reader.rate(), reader.frame_number(3)));
                    });
                    read.push(rx.recv_timeout(Duration::from_secs(5)));
                }
            },
            || true,
        )
        .unwrap();
    assert_eq!(read.len(), 1);
    let description = read[0].as_ref().expect("the description was readable during the decode");
    assert_eq!(*description, (48, (24, 1), None));
}

fn temp_path(tag: &str) -> PathBuf {
    std::env::temp_dir().join(format!("th_media_props_{}_{tag}.exr", std::process::id()))
}

/// A one-pixel EXR, saying it is in `colour_space` if given.
fn write_exr(path: &PathBuf, colour_space: Option<&str>) {
    let mut attributes = LayerAttributes::default();
    if let Some(cs) = colour_space {
        attributes.other.insert(Text::from("oiio:ColorSpace"), AttributeValue::Text(Text::from(cs)));
    }
    let channels = SpecificChannels::rgb(|_: Vec2<usize>| (0.5_f32, 0.25_f32, 0.125_f32));
    let layer = Layer::new((1, 1), attributes, Encoding::UNCOMPRESSED, channels);
    Image::from_layer(layer).write().to_file(path).unwrap();
}

/// An EXR sequence is shown in the colour space its frames say; one whose
/// frames do not say cannot be shown right and does not open.
#[test]
fn an_exr_says_its_colour_space_or_does_not_open() {
    let said = temp_path("said");
    write_exr(&said, Some("ACEScg"));
    let media = Media::exr(ExrSequence::new(1001, 1, vec![said.clone()]), (25, 1)).unwrap();
    assert_eq!(media.colour_space(), Some("ACEScg"));
    assert_eq!((media.rate(), media.frame_number(0)), ((25, 1), Some(1001)));

    let silent = temp_path("silent");
    write_exr(&silent, None);
    let error = Media::exr(ExrSequence::new(1001, 1, vec![silent.clone()]), (25, 1)).unwrap_err();
    assert!(error.to_string().contains("colour space"), "{error}");
    std::fs::remove_file(said).ok();
    std::fs::remove_file(silent).ok();
}
