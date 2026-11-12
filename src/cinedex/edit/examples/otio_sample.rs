//! Writes a sample edit's OTIO timeline to the path given, to check the
//! writer against OpenTimelineIO and Resolve: a cut of two shots on V2 over
//! an animatic on V1, a plain dissolve from a V3 shot, a fade over the
//! animatic (noted, not written), a slug and a music track, and an end card
//! on V1 right after the animatic that fades in from and out to black (#60:
//! transitions against gaps of no frames).
//!
//! `cargo run -p th_edit --example otio_sample -- out.otio`

use th_edit::document::{AudioClip, FrozenEdit, FrozenSource, FrozenTake, Marker, TakeKind, Track, Version, VideoClip};
use th_playlist::{Frames, Media};

fn version(v: &str) -> Version {
    Version::try_from(v.to_string()).unwrap()
}

fn shot(name: &str, v: &str) -> FrozenSource {
    FrozenSource::Shot {
        shot: format!("entity:/shots/Sample/{name}"),
        take: Some(FrozenTake {
            department: "animation".into(),
            kind: TakeKind::Playblast,
            layer: None,
            version: version(v),
            media: Media::Movie {
                path: format!("P:/Sample/render/playblast/shots/Sample/{name}/animation/Sample_{name}_animation_{v}.mp4"),
            },
        }),
    }
}

fn render(name: &str) -> FrozenSource {
    let frames = Frames {
        directory: format!("P:/Sample_render/render/shots/Sample/{name}/light/default/v0003/beauty"),
        prefix: "beauty.".into(),
        suffix: ".exr".into(),
        padding: 4,
        first: 1001,
        last: 1060,
        step: 1,
    };
    FrozenSource::Shot {
        shot: format!("entity:/shots/Sample/{name}"),
        take: Some(FrozenTake {
            department: "light".into(),
            kind: TakeKind::Render,
            layer: Some("default".into()),
            version: version("v0003"),
            media: Media::Exr { layer: "default".into(), frames },
        }),
    }
}

fn clip(at: i64, duration: i64, source_in: i64, source: FrozenSource, fade_in: i64) -> VideoClip<FrozenSource> {
    VideoClip { at, duration, source_in, source, fade_in, fade_out: 0, opacity: 1.0 }
}

fn main() {
    let out = std::env::args().nth(1).expect("the path to write");
    let animatic = FrozenSource::Media {
        item: "animatic".into(),
        version: version("v0002"),
        media: Media::Movie { path: "P:/Sample/export/media/animatic/v0002/animatic.mov".into() },
    };
    let music = FrozenSource::Media {
        item: "music".into(),
        version: version("v0001"),
        media: Media::Movie { path: "P:/Sample/export/media/music/v0001/music.wav".into() },
    };
    let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
    let mut end_card = clip(240, 48, 0, FrozenSource::Slug { text: "The end".into() }, 6);
    end_card.fade_out = 12;
    edit.video.push(Track { name: "V1".into(), clips: vec![clip(0, 240, 0, animatic, 0), end_card] });
    edit.video.push(Track {
        name: "V2".into(),
        clips: vec![
            clip(0, 24, 0, FrozenSource::Slug { text: "Sample - cut 3".into() }, 0),
            clip(24, 60, 1001, shot("010", "v0012"), 12),
            clip(84, 50, 1005, shot("020", "v0004"), 0),
        ],
    });
    edit.video.push(Track { name: "V3".into(), clips: vec![clip(122, 60, 1001, render("030"), 12)] });
    edit.audio.push(Track {
        name: "A1".into(),
        clips: vec![AudioClip { at: 0, duration: 240, source_in: 0, source: music, gain_db: -3.0, fade_in: 0, fade_out: 24 }],
    });
    edit.markers.push(Marker { at: 100, note: "hold longer on the reveal".into() });
    assert_eq!(edit.problem(), None);
    let otio = th_edit::otio::timeline(&edit, "Sample cut");
    std::fs::write(&out, serde_json::to_string_pretty(&otio).unwrap()).unwrap();
}
