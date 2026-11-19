//! A clip's transform on its way to the picture: the edit document's
//! [`Transform`] as the blend's [`Placement`], and the picture the viewer
//! blends transformed clips into.
//!
//! The viewer, Export Movie and Render Edit all blend through
//! `th_media::composite::dissolve_placed` with the placement made here, so
//! a clip sits in the same place in each. The transform's units are
//! picture heights and fitted sizes, never pixels, so the size of the
//! picture being made does not matter either.

use th_edit::document::Transform;
use th_media::composite::Placement;

/// `transform` as the blend takes it.
pub(crate) fn placement(transform: Transform) -> Placement {
    Placement { x: transform.x as f32, y: transform.y as f32, scale: transform.scale as f32, rotation: transform.rotation as f32 }
}

/// The picture the viewer blends `frames` (each layer's width and height)
/// into when a clip is transformed: the edit's shape (`resolution`), since
/// a transform places a clip in the edit's picture, as tall as the tallest
/// frame on screen but no larger than the edit. Without transforms the
/// viewer keeps to the top frame's own size.
pub(crate) fn canvas(resolution: [u32; 2], frames: impl IntoIterator<Item = (u32, u32)>) -> (u32, u32) {
    let [wide, tall] = resolution.map(|n| n.max(1));
    let height = frames.into_iter().map(|f| f.1).max().unwrap_or(tall).clamp(1, tall);
    let width = (u64::from(height) * u64::from(wide) + u64::from(tall) / 2) / u64::from(tall);
    (width.max(1) as u32, height)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// The viewer's picture for transformed clips has the edit's shape
        /// to within a pixel, is never larger than the edit, and is as
        /// tall as the tallest frame that fits: a proxy makes a small
        /// picture, the full frames the edit's own.
        #[test]
        fn the_viewers_picture_has_the_edits_shape(
            resolution in (1u32..8000, 1u32..5000),
            frames in prop::collection::vec((1u32..9000, 1u32..6000), 0..4),
        ) {
            let (wide, tall) = resolution;
            let (w, h) = canvas([wide, tall], frames.iter().copied());
            prop_assert!(w >= 1 && h >= 1 && w <= wide.max(1) && h <= tall);
            let tallest = frames.iter().map(|f| f.1).max();
            prop_assert_eq!(h, tallest.map_or(tall, |t| t.min(tall)));
            let exact = f64::from(h) * f64::from(wide) / f64::from(tall);
            prop_assert!((f64::from(w) - exact).abs() <= 1.0, "{}x{} for {}x{}", w, h, wide, tall);
            if tallest.is_some_and(|t| t >= tall) {
                prop_assert_eq!((w, h), (wide, tall));
            }
        }

        /// The placement is the transform, number for number.
        #[test]
        fn the_placement_is_the_transform(x in -10f64..10.0, y in -10f64..10.0, scale in 0.01f64..100.0, rotation in -3600f64..3600.0) {
            let p = placement(Transform { x, y, scale, rotation });
            prop_assert_eq!((p.x, p.y, p.scale, p.rotation), (x as f32, y as f32, scale as f32, rotation as f32));
            prop_assert!(placement(Transform::IDENTITY).is_identity());
        }
    }
}
