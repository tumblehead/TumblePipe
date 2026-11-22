//! `cinedex --shot-audio`, no window: a shot's sound from the edit (Remy's
//! RFE, 2026-11-22), for the shot's Houdini scene to play
//! (`tumblepipe.pipe.shot_audio`).
//!
//! The edit's audio tracks (all, or those `--tracks` names) are mixed as
//! Render Edit mixes them (each clip at its gain, through its fades) over
//! the shot's frames, and written as a 16-bit stereo WAV at
//! [`render::AUDIO_RATE`] whose first sample is the shot's first frame. The
//! shots' own playblast sound under their video clips is left out: it is
//! what the shot scene plays already, and would echo.
//!
//! The shot's frames are `--frames`, else its range roll included (what its
//! scene's playbar covers), else its clip's. Where they reach past the clip
//! the sound is the edit's around the cut; before the edit's start it is
//! silence (`th_pipe::shot_audio`). A summary is printed as JSON: the
//! version mixed, the frames, the edit frame the first lands on, the tracks
//! and how many clips sound in the window.

use std::path::Path;

use th_edit::document::Source;
use th_pipe::shot_audio::{edit_window, sample_window, select_tracks, shot_clip};

use crate::editing::Origin;
use crate::editor::ProjectPaths;
use crate::editor::document_io::open_document;
use crate::editor::entries::{Resolved, resolve};
use crate::render::{AUDIO_RATE, voice, write_wav};

/// Mix `shot`'s sound from edit `edit` (`entity:/edits/<name>@vNNNN`, a
/// published version, or `entity:/edits/<name>`, its newest working one)
/// into `out`; the summary.
pub fn export(
    paths: &ProjectPaths,
    edit: &str,
    shot: &str,
    frames: Option<(i64, i64)>,
    tracks: &[String],
    out: &Path,
) -> Result<serde_json::Value, String> {
    let (doc, _) = open_document(paths, Some(edit), &[])?;
    let version = match &doc.origin {
        Origin::Published { version, .. } => Some(version.clone()),
        Origin::Working { version: Some(version), .. } => Some(version.clone()),
        _ => None,
    };
    let Some(version) = version else { return Err(format!("{edit} has no saved version: it has no sound yet")) };
    let edit_doc = doc.edit;
    let fps = edit_doc.fps.max(1);
    let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
    let clip = shot_clip(&edit_doc, &uri, |s| match s {
        Source::Shot { shot, .. } => Some(shot.as_str()),
        _ => None,
    })?
    .ok_or_else(|| format!("{shot} is not in {edit}"))?;
    let (first, last) = match frames {
        Some(frames) => frames,
        None => {
            let project = paths.open()?;
            match th_pipe::timeline::get_frame_range(&project.config, &uri)? {
                Some(range) => {
                    let full = range.full_range();
                    (full.first_frame, full.last_frame)
                }
                None => clip.shot_frames(),
            }
        }
    };
    let (edit_first, count) = edit_window(&clip, first, last)?;
    let names: Vec<String> = edit_doc.audio.iter().map(|t| t.name.clone()).collect();
    let picked = select_tracks(&names, tracks)?;

    let mut distinct: Vec<&Source> = Vec::new();
    for c in picked.iter().flat_map(|&i| &edit_doc.audio[i].clips) {
        if !distinct.contains(&&c.source) {
            distinct.push(&c.source);
        }
    }
    // Side by side, as a render resolves: on a file server each is a few
    // round trips. Never a live playblast: the cut's own takes.
    let resolved: Vec<Result<Resolved, String>> = std::thread::scope(|scope| {
        let running: Vec<_> = distinct.iter().map(|&s| scope.spawn(move || resolve(paths, s, fps, false, None))).collect();
        running.into_iter().map(|t| t.join().unwrap_or_else(|_| Err("resolving panicked".into()))).collect()
    });
    let resolved: Vec<Resolved> = resolved
        .into_iter()
        .zip(&distinct)
        .map(|(r, s)| r.map_err(|e| format!("{}: {e}", crate::editor::source_name(s))))
        .collect::<Result<_, _>>()?;

    let fps_f = f64::from(fps);
    let mut voices = Vec::new();
    for c in picked.iter().flat_map(|&i| &edit_doc.audio[i].clips) {
        let at = distinct.iter().position(|s| **s == c.source).expect("every clip's source is resolved");
        voices.extend(voice(&resolved[at], c.at, c.duration, c.source_in, c.gain_db, (c.fade_in, c.fade_out), fps_f));
    }
    let (start, total) = sample_window(edit_first, count, fps, AUDIO_RATE);
    let window = (start as f64 / f64::from(AUDIO_RATE), (start + total as i64) as f64 / f64::from(AUDIO_RATE));
    let sounding = voices.iter().filter(|v| v.at < window.1 && v.at + v.length > window.0).count();
    if let Some(parent) = out.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    }
    write_wav(&voices, start, total, out)?;
    let track_names: Vec<String> =
        picked.iter().map(|&i| if names[i].trim().is_empty() { format!("A{}", i + 1) } else { names[i].clone() }).collect();
    Ok(serde_json::json!({
        "edit": edit.split('@').next().unwrap_or(edit),
        "version": version,
        "published": matches!(doc.origin, Origin::Published { .. }),
        "shot": shot,
        "first": first,
        "last": last,
        "edit_first": edit_first,
        "fps": fps,
        "rate": AUDIO_RATE,
        "samples": total,
        "tracks": track_names,
        "clips": sounding,
        "out": out.to_string_lossy(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_media::sound::{Sound, Voice, mix, parse_wav};

    /// A project with shot SQ010/SH010 (1001-1020) cut into working edit
    /// Main at edit frame 10, and audio tracks Dialog and Music each one
    /// clip of a ramp WAV media item (`dialog` at edit frame 0 from its
    /// frame 2, `music` at edit frame 12, at -6 dB).
    fn project() -> (tempfile::TempDir, ProjectPaths) {
        use th_edit::document::{AudioClip, Rule, Track, VersionRule, VideoClip, WorkingEdit};
        let (dir, root) = th_pipe::testing::make_project();
        let project = th_pipe::Project::open(&root, &root).unwrap();
        th_pipe::entities::create_shot(&project, "SQ010", "SH010", 1001, 1020).unwrap();
        let wav_of = |name: &str, scale: f32| {
            let frames = 48_000usize; // one second: 24 frames at 24 fps
            let mut bytes = crate::render::wav_header(frames as u64, AUDIO_RATE);
            bytes.extend((0..frames).flat_map(|i| {
                let s = crate::render::pcm16(scale * (i as f32 / 48_000.0 - 0.5));
                [s.to_le_bytes(), (-s).to_le_bytes()].concat()
            }));
            let path = dir.path().join(format!("{name}.wav"));
            std::fs::write(&path, bytes).unwrap();
            let measure = th_pipe::media::Measure { frames: None, seconds: 1.0, sound: true };
            th_pipe::media::import(&project, &path, name, measure, "").unwrap();
        };
        wav_of("dialog", 1.0);
        wav_of("music", 0.5);
        th_pipe::edit_files::create_edit(&project, "Main").unwrap();
        let mut edit = WorkingEdit::new(24, [16, 9], "00:00:00:00");
        let shot = Source::Shot { shot: "entity:/shots/SQ010/SH010".into(), take: Rule::best() };
        edit.video.push(Track { name: "V1".into(), clips: vec![VideoClip::new(10, 20, 1001, shot)], color: None });
        let media = |item: &str| Source::Media { item: item.into(), version: VersionRule::Latest };
        let clip =
            |at, duration, source_in, source, gain_db| AudioClip { at, duration, source_in, source, gain_db, fade_in: 0, fade_out: 0 };
        edit.audio.push(Track { name: "Dialog".into(), clips: vec![clip(0, 20, 2, media("dialog"), 0.0)], color: None });
        edit.audio.push(Track { name: "Music".into(), clips: vec![clip(12, 10, 0, media("music"), -6.0)], color: None });
        th_pipe::edit_files::save_working(&project, "Main", &edit, None, "").unwrap();
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        (dir, paths)
    }

    /// What the ramp WAV `scale` holds at its sample frame `i` (left).
    fn ramp(scale: f32, i: i64) -> f32 {
        f32::from(crate::render::pcm16(scale * (i as f32 / 48_000.0 - 0.5))) / 32768.0
    }

    /// The shot's WAV holds, from its first sample on, the edit's sound at
    /// the edit frame the shot's first frame lands on: Dialog's clip from
    /// its in point, Music's at its gain where it starts, silence before
    /// the edit's frame 0 and after its clips; one track asked for is that
    /// track alone; the summary says what was mixed.
    #[test]
    fn the_shots_wav_is_the_edits_sound_over_its_frames() {
        let (dir, paths) = project();
        let out = dir.path().join("out").join("shot.wav");
        // Frames 991-1020: ten before the clip's first, at edit frame 0.
        let summary = export(&paths, "entity:/edits/Main", "entity:/shots/SQ010/SH010", Some((991, 1020)), &[], &out).unwrap();
        assert_eq!(summary["first"], 991);
        assert_eq!(summary["edit_first"], 0);
        assert_eq!(summary["version"], "v0001");
        assert_eq!(summary["published"], false);
        assert_eq!(summary["tracks"], serde_json::json!(["Dialog", "Music"]));
        assert_eq!(summary["clips"], 2);
        let wav = parse_wav(&std::fs::read(&out).unwrap()).unwrap();
        assert_eq!(wav.frames(), 30 * 2000);
        let gain = 10f32.powf(-6.0 / 20.0);
        for j in (0..30 * 2000i64).step_by(7) {
            let edit_frame = j / 2000;
            let dialog = if edit_frame < 20 { ramp(1.0, j + 2 * 2000) } else { 0.0 };
            let music = if (12..22).contains(&edit_frame) { ramp(0.5, j - 12 * 2000) * gain } else { 0.0 };
            let got = wav.samples[(j * 2) as usize];
            assert!((got - (dialog + music)).abs() < 2.0 / 32768.0, "sample {j}: {got} for {}", dialog + music);
        }

        // Before the edit: the shot's frame 981 lands on edit frame -10.
        let only = dir.path().join("out").join("dialog.wav");
        let summary =
            export(&paths, "entity:/edits/Main", "entity:/shots/SQ010/SH010", Some((981, 990)), &["dialog".into()], &only).unwrap();
        assert_eq!((summary["edit_first"].as_i64(), summary["clips"].as_u64()), (Some(-10), Some(0)));
        let wav = parse_wav(&std::fs::read(&only).unwrap()).unwrap();
        assert_eq!(wav.frames(), 10 * 2000);
        assert!(wav.samples.iter().all(|s| *s == 0.0), "before the edit is silence");

        // Its range when no frames are given (no roll set: the clip's).
        let summary = export(&paths, "entity:/edits/Main", "entity:/shots/SQ010/SH010", None, &["A2".into()], &only).unwrap();
        assert_eq!((summary["first"].as_i64(), summary["last"].as_i64()), (Some(1001), Some(1020)));
        assert_eq!(summary["tracks"], serde_json::json!(["Music"]));
        let wav = parse_wav(&std::fs::read(&only).unwrap()).unwrap();
        for j in (0..20 * 2000i64).step_by(11) {
            // Shot frame 1001 is edit frame 10; Music starts on 12.
            let edit_frame = 10 + j / 2000;
            let want = if (12..22).contains(&edit_frame) { ramp(0.5, j - 2 * 2000) * gain } else { 0.0 };
            assert!((wav.samples[(j * 2) as usize] - want).abs() < 2.0 / 32768.0, "sample {j}");
        }

        assert!(
            export(&paths, "entity:/edits/Main", "entity:/shots/SQ010/SH010", None, &["Nope".into()], &only)
                .is_err_and(|e| e.contains("Nope"))
        );
        assert!(export(&paths, "entity:/edits/Main", "entity:/shots/SQ010/SH020", None, &[], &only).is_err_and(|e| e.contains("not in")));
    }

    proptest! {
        /// The WAV is the mix sample for sample from its window's start:
        /// silence before the edit's frame 0, then the edit's mix at those
        /// samples, as long as the window.
        #[test]
        fn the_wav_is_the_mix_of_its_window(
            start in -300i64..300, total in 0u64..600,
            at in 0.0f64..0.01, gain in 0.1f32..1.0,
            samples in prop::collection::vec(-1.0f32..1.0, 2..400),
        ) {
            let samples = samples[..samples.len() / 2 * 2].to_vec();
            let sound = Sound { rate: AUDIO_RATE, samples };
            let voices = [Voice { sound: &sound, at, length: 1.0, offset: 0.0, gain, fade_in: 0.0, fade_out: 0.0 }];
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("shot.wav");
            write_wav(&voices, start, total, &path).unwrap();
            let read = parse_wav(&std::fs::read(&path).unwrap()).unwrap();
            prop_assert_eq!(read.rate, AUDIO_RATE);
            prop_assert_eq!(read.frames() as u64, total);
            for i in 0..total as i64 {
                let sample = start + i;
                let want = if sample < 0 {
                    [0.0, 0.0]
                } else {
                    let mut out = [0f32; 2];
                    mix(&voices, AUDIO_RATE, sample as u64, &mut out);
                    out
                };
                for (k, want) in want.iter().enumerate() {
                    let got = read.samples[(i * 2) as usize + k];
                    prop_assert!((got - want.min(32767.0 / 32768.0)).abs() <= 0.5 / 32768.0 + 1e-6, "sample {}: {} for {}", sample, got, want);
                }
            }
        }
    }
}
