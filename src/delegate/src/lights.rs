//! What the post pass lights a surface with ([`crate::composite::light`]):
//! the scene's own UsdLux lights, as Houdini's GL viewport does in its
//! scene-lights mode, or the fixed camera rig ([`crate::composite::LIGHTS`])
//! when the scene has none or `th:lighting` asks for the headlight.
//!
//! The shim syncs the Hydra light sprims (distant, sphere, rect, disk,
//! cylinder and dome lights) and hands their parameters over as they are
//! authored ([`SceneLight`], world space); [`Lighting::new`] turns them into
//! camera space for one frame. A surface is lit without shadows (Houdini GL's
//! "normal" lighting has none), by Lambert's cosine from each light, plus a
//! uniform ambient from the dome lights (their textures ignored) and a faint
//! highlight of the brightest light.
//!
//! Brightness follows Karma's (measured on a 0.5 grey Lambert plane with
//! Karma CPU in Houdini 22.0.429), so a playblast is as bright as the
//! render, and so as Houdini's GL viewport, which follows Karma. Writing L
//! for intensity x 2^exposure x colour x colour temperature x diffuse, a
//! surface of colour `albedo` is lit to `albedo x k`, with per light:
//!
//! | light | k |
//! |---|---|
//! | distant, normalized or of angle 0 | L cos / pi |
//! | distant, of angle a | L Omega cos / pi, Omega = 2 pi (1 - cos(a / 2)) |
//! | sphere | L r^2 cos / d^2 (normalized: L cos / (4 pi d^2)) |
//! | sphere, treatAsPoint | L cos / (pi d^2) (normalized: L cos / (4 pi d^2)) |
//! | rect | L w h cos cos_e / (pi d^2) (normalized: L cos cos_e / (pi d^2)) |
//! | disk | L r^2 cos cos_e / d^2 (normalized: L cos cos_e / (pi d^2)) |
//! | cylinder | 8 L r l cos / (pi^2 d^2) (normalized: 4 L cos / (pi^3 d^2)) |
//! | dome | L, from every direction |
//!
//! `cos` is the angle between the surface's normal and the direction to the
//! light, `d` the distance to the light's centre (held above the light's own
//! size, so a surface inside it is not lit without bound) and `cos_e` the
//! angle between the light's emitting side (its -Z) and the surface: a rect
//! or disk light lights only what is in front of it. Sphere, rect, disk and
//! cylinder lights are points at their centre (exact far from the light; a
//! rect light half its width away came out 2.5% darker in Karma). A
//! default distant light from Houdini's Light LOP (angle 0.53 degrees, not
//! normalized) is so nearly black in Karma, and so here too; the Distant
//! Light LOP normalizes. Sizes are scaled by the light's transform.

use crate::math::{self, Mat4, Vec3};
use std::f32::consts::PI;

/// Most lights a frame is lit by; past it the dimmest go ([`SceneLights::new`]).
pub const MAX_LIGHTS: usize = 16;
/// The least squared distance a light is reckoned from (scene units): a
/// point light on the surface lights it this brightly, not without bound.
const LEAST_SQUARED_DISTANCE: f32 = 1e-4;
/// The scene lights' highlight per unit of the light the brightest gives
/// the surface: the fixed rig's highlight ([`crate::composite::SPECULAR`]) over its
/// key light's intensity, so a scene light as bright as the key shines as much.
pub(crate) const SCENE_SPECULAR: f32 = 0.05 / 0.62;

/// A UsdLux light's shape.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum LightShape {
    #[default]
    Distant,
    Sphere,
    Rect,
    Disk,
    Cylinder,
    Dome,
}

impl LightShape {
    /// The ABI's number for it: 0 distant, 1 sphere, 2 rect, 3 disk,
    /// 4 cylinder, 5 dome; anything else none.
    pub fn from_abi(v: u32) -> Option<LightShape> {
        Some(match v {
            0 => LightShape::Distant,
            1 => LightShape::Sphere,
            2 => LightShape::Rect,
            3 => LightShape::Disk,
            4 => LightShape::Cylinder,
            5 => LightShape::Dome,
            _ => return None,
        })
    }
}

/// A light as the scene has it, in world space, its sizes scaled by its
/// transform ([`SceneLight::placed`]).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SceneLight {
    pub shape: LightShape,
    /// intensity x 2^exposure x colour x colour temperature x diffuse,
    /// linear.
    pub radiance: Vec3,
    /// inputs:normalize: the light's power does not grow with its size.
    pub normalize: bool,
    /// A sphere light's treatAsPoint.
    pub point: bool,
    /// A distant light's angular diameter, degrees.
    pub angle: f32,
    pub radius: f32,
    pub width: f32,
    pub height: f32,
    pub length: f32,
    /// The light's centre.
    pub position: Vec3,
    /// Unit: the light's -Z, along which a distant light shines and
    /// towards which a rect or disk light emits.
    pub facing: Vec3,
}

impl SceneLight {
    const OFF: SceneLight = SceneLight {
        shape: LightShape::Distant,
        radiance: [0.0; 3],
        normalize: false,
        point: false,
        angle: 0.0,
        radius: 0.0,
        width: 0.0,
        height: 0.0,
        length: 0.0,
        position: [0.0; 3],
        facing: [0.0, 0.0, -1.0],
    };

    /// A light of `shape` placed by `to_world` (light to world, USD
    /// row-vector layout): its centre the translation, its facing the -Z
    /// axis, and its sizes (`radius`, `width`, `height`, `length`, as
    /// authored) scaled as its axes are: a rect's width by X and height by
    /// Y, a disk's radius by the mean of X and Y, a cylinder's length by X
    /// (its axis) and radius by Y and Z, a sphere's radius by all three.
    #[allow(clippy::too_many_arguments)]
    pub fn placed(shape: LightShape, radiance: Vec3, normalize: bool, point: bool, angle: f32,
                  [radius, width, height, length]: [f32; 4], to_world: &Mat4) -> SceneLight {
        let row = |r: usize| [to_world[r * 4] as f32, to_world[r * 4 + 1] as f32, to_world[r * 4 + 2] as f32];
        let len = |r: usize| math::dot(row(r), row(r)).sqrt();
        let (sx, sy, sz) = (len(0), len(1), len(2));
        let radius = radius.abs()
            * match shape {
                LightShape::Disk => (sx * sy).sqrt(),
                LightShape::Cylinder => (sy * sz).sqrt(),
                _ => (sx * sy * sz).cbrt(),
            };
        let finite = |v: f32| if v.is_finite() { v } else { 0.0 };
        let radiance = radiance.map(|c| finite(c).max(0.0));
        SceneLight {
            shape,
            radiance,
            normalize,
            point,
            angle: finite(angle),
            radius: finite(radius),
            width: finite(width.abs() * sx),
            height: finite(height.abs() * sy),
            length: finite(length.abs() * sx),
            position: row(3).map(finite),
            facing: math::normalize(math::scale(row(2), -1.0)),
        }
    }

    /// What it adds to `k` (the module's table) at unit distance, before
    /// the cosines: for a distant or dome light the whole of it.
    pub fn strength(&self) -> Vec3 {
        let (r, l) = (self.radius, self.radiance);
        let s = match self.shape {
            LightShape::Dome => 1.0,
            LightShape::Distant => {
                let a = (self.angle.abs() * 0.5).to_radians();
                if self.normalize || a <= 0.0 { 1.0 / PI } else { 2.0 * (1.0 - a.min(PI).cos()) }
            }
            LightShape::Sphere if self.normalize => 1.0 / (4.0 * PI),
            LightShape::Sphere if self.point => 1.0 / PI,
            LightShape::Sphere => r * r,
            LightShape::Rect | LightShape::Disk if self.normalize => 1.0 / PI,
            LightShape::Rect => self.width * self.height / PI,
            LightShape::Disk => r * r,
            LightShape::Cylinder if self.normalize => 4.0 / (PI * PI * PI),
            LightShape::Cylinder => 8.0 * r * self.length / (PI * PI),
        };
        l.map(|c| c * s)
    }

    /// The least squared distance it is reckoned from: about its own size.
    fn floor(&self) -> f32 {
        let size = match self.shape {
            LightShape::Sphere if self.point => 0.0,
            LightShape::Sphere | LightShape::Disk => self.radius * self.radius,
            LightShape::Rect => self.width * self.height / PI,
            LightShape::Cylinder => self.radius * self.length,
            LightShape::Distant | LightShape::Dome => 0.0,
        };
        size.max(LEAST_SQUARED_DISTANCE)
    }
}

/// The scene lights of a frame, at most [`MAX_LIGHTS`]; none lights it
/// with the fixed rig.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SceneLights {
    list: [SceneLight; MAX_LIGHTS],
    count: usize,
}

impl Default for SceneLights {
    fn default() -> Self {
        SceneLights::NONE
    }
}

impl SceneLights {
    /// No scene lights: the fixed rig.
    pub const NONE: SceneLights = SceneLights { list: [SceneLight::OFF; MAX_LIGHTS], count: 0 };

    /// `lights`, the dome lights made one, and of the rest the
    /// [`MAX_LIGHTS`] (one fewer with a dome) brightest by the luminance of
    /// their [`SceneLight::strength`].
    pub fn new(lights: &[SceneLight]) -> SceneLights {
        let mut out = SceneLights::NONE;
        let domes: Vec<&SceneLight> = lights.iter().filter(|l| l.shape == LightShape::Dome).collect();
        let mut rest: Vec<&SceneLight> = lights.iter().filter(|l| l.shape != LightShape::Dome).collect();
        let room = if domes.is_empty() { MAX_LIGHTS } else { MAX_LIGHTS - 1 };
        if rest.len() > room {
            let lum = |l: &SceneLight| luminance(l.strength());
            rest.sort_by(|a, b| lum(b).total_cmp(&lum(a)));
            rest.truncate(room);
        }
        if !domes.is_empty() {
            let sum = domes.iter().fold([0.0; 3], |s, l| math::add(s, l.strength()));
            out.list[0] = SceneLight { shape: LightShape::Dome, radiance: sum, ..SceneLight::OFF };
            out.count = 1;
        }
        for l in rest {
            out.list[out.count] = *l;
            out.count += 1;
        }
        out
    }

    pub fn as_slice(&self) -> &[SceneLight] {
        &self.list[..self.count]
    }

    pub fn is_empty(&self) -> bool {
        self.count == 0
    }
}

fn luminance(c: Vec3) -> f32 {
    0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

/// How a [`Lamp`] lights: from a direction, from a point, or from a point
/// on the side it faces.
pub(crate) const LAMP_DIRECTIONAL: u32 = 0;
pub(crate) const LAMP_POINT: u32 = 1;
pub(crate) const LAMP_FACING: u32 = 2;

/// One light of [`Lighting`], camera space.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Lamp {
    /// [`LAMP_DIRECTIONAL`], [`LAMP_POINT`] or [`LAMP_FACING`].
    pub kind: u32,
    /// Directional: the unit direction towards the light; else its position.
    pub at: Vec3,
    /// Facing: the unit direction it emits towards.
    pub axis: Vec3,
    /// What it adds to `k` (directional), or that at unit distance.
    pub color: Vec3,
    /// The least squared distance it is reckoned from.
    pub floor: f32,
}

impl Lamp {
    const OFF: Lamp = Lamp { kind: LAMP_DIRECTIONAL, at: [0.0, 0.0, 1.0], axis: [0.0, 0.0, -1.0], color: [0.0; 3],
                             floor: 1.0 };

    /// The unit direction towards it from camera-space point `p`, and the
    /// light it gives there before the surface's cosine.
    #[inline]
    pub fn toward(&self, p: Vec3) -> (Vec3, Vec3) {
        if self.kind == LAMP_DIRECTIONAL {
            return (self.at, self.color);
        }
        let d = math::sub(self.at, p);
        let dd = math::dot(d, d);
        let l = if dd > 0.0 { math::scale(d, 1.0 / dd.sqrt()) } else { [0.0, 0.0, 1.0] };
        let mut f = 1.0 / dd.max(self.floor);
        if self.kind == LAMP_FACING {
            f *= (-math::dot(self.axis, l)).max(0.0);
        }
        (l, math::scale(self.color, f))
    }
}

/// Everything [`crate::composite::light`] lights with, for one frame, in
/// camera space (x right, y up, z towards the camera).
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Lighting {
    /// Ambient from above (camera up) and below; the scene's is the same
    /// both ways (its dome lights).
    pub sky: Vec3,
    pub ground: Vec3,
    pub lamps: [Lamp; MAX_LIGHTS],
    pub count: usize,
    /// The lamp the highlight is of, and its strength: the rig's a fixed
    /// white, the scene's the light that lamp gives the surface times this.
    pub shine: usize,
    pub specular: f32,
    pub tinted: bool,
}

impl Lighting {
    /// The fixed rig: key, fill and rim lights and the sky/ground ambient,
    /// fixed to the camera.
    pub fn rig() -> Lighting {
        use crate::composite::{GROUND, LIGHTS, SKY, SPECULAR};
        let mut lamps = [Lamp::OFF; MAX_LIGHTS];
        for (lamp, &(l, i)) in lamps.iter_mut().zip(&LIGHTS) {
            *lamp = Lamp { kind: LAMP_DIRECTIONAL, at: math::normalize(l), axis: [0.0, 0.0, -1.0], color: [i; 3],
                           floor: 1.0 };
        }
        Lighting { sky: [SKY; 3], ground: [GROUND; 3], lamps, count: LIGHTS.len(), shine: 0, specular: SPECULAR,
                   tinted: false }
    }

    /// The frame's lighting: `lights` seen from the camera `view_to_world`
    /// places, or the rig when there are none.
    pub fn new(lights: &SceneLights, view_to_world: &Mat4) -> Lighting {
        if lights.is_empty() {
            return Lighting::rig();
        }
        let view = crate::composite::View::new(view_to_world);
        let [r, u, b] = view.axes;
        let dir = |v: Vec3| math::normalize([math::dot(v, r), math::dot(v, u), math::dot(v, b)]);
        let point = |p: Vec3| {
            let v = math::sub(p, view.eye);
            [math::dot(v, r), math::dot(v, u), math::dot(v, b)]
        };
        let mut out = Lighting { sky: [0.0; 3], ground: [0.0; 3], lamps: [Lamp::OFF; MAX_LIGHTS], count: 0, shine: 0,
                                 specular: SCENE_SPECULAR, tinted: true };
        // The highlight's: the lamp brightest from where the camera is.
        let mut brightest = -1.0f32;
        for l in lights.as_slice() {
            let color = l.strength();
            let lamp = match l.shape {
                LightShape::Dome => {
                    out.sky = math::add(out.sky, color);
                    out.ground = out.sky;
                    continue;
                }
                LightShape::Distant => Lamp { kind: LAMP_DIRECTIONAL, at: dir(math::scale(l.facing, -1.0)),
                                              axis: [0.0, 0.0, -1.0], color, floor: 1.0 },
                shape => Lamp {
                    kind: if matches!(shape, LightShape::Rect | LightShape::Disk) { LAMP_FACING } else { LAMP_POINT },
                    at: point(l.position),
                    axis: dir(l.facing),
                    color,
                    floor: l.floor(),
                },
            };
            let seen = match lamp.kind {
                LAMP_DIRECTIONAL => luminance(color),
                _ => luminance(color) / math::dot(lamp.at, lamp.at).max(lamp.floor),
            };
            if seen > brightest {
                brightest = seen;
                out.shine = out.count;
            }
            out.lamps[out.count] = lamp;
            out.count += 1;
        }
        if out.count == 0 {
            out.specular = 0.0;
        }
        out
    }
}

/// The colour of a blackbody at `kelvin` as linear Rec.709 of luminance 1,
/// as UsdLux's colour temperature tints a light: its chromaticity on the
/// Planckian locus (Kim et al. 2002, 1667 K to 25000 K, held there) taken
/// to Rec.709 and scaled. Within 1% of Karma's tint from 3000 K to 9000 K.
pub fn blackbody(kelvin: f32) -> Vec3 {
    let t = if kelvin.is_finite() { (kelvin as f64).clamp(1667.0, 25000.0) } else { 6500.0 };
    let (t2, t3) = (t * t, t * t * t);
    let x = if t <= 4000.0 {
        -0.266_123_9e9 / t3 - 0.234_358_9e6 / t2 + 0.877_695_6e3 / t + 0.179_910
    } else {
        -3.025_846_9e9 / t3 + 2.107_037_9e6 / t2 + 0.222_634_7e3 / t + 0.240_390
    };
    let (x2, x3) = (x * x, x * x * x);
    let y = if t <= 2222.0 {
        -1.106_381_4 * x3 - 1.348_110_20 * x2 + 2.185_558_32 * x - 0.202_196_83
    } else if t <= 4000.0 {
        -0.954_947_6 * x3 - 1.374_185_93 * x2 + 2.091_370_15 * x - 0.167_488_67
    } else {
        3.081_758_0 * x3 - 5.873_386_70 * x2 + 3.751_129_97 * x - 0.370_014_83
    };
    let (cx, cy, cz) = (x / y, 1.0, (1.0 - x - y) / y);
    let rgb = [
        3.240_454_2 * cx - 1.537_138_5 * cy - 0.498_531_4 * cz,
        -0.969_266_0 * cx + 1.876_010_8 * cy + 0.041_556_0 * cz,
        0.055_643_4 * cx - 0.204_025_9 * cy + 1.057_225_2 * cz,
    ]
    .map(|c: f64| c.max(0.0) as f32);
    let lum = luminance(rgb);
    if lum > 0.0 { rgb.map(|c| c / lum) } else { [1.0; 3] }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A light at `position` facing -Z, unscaled.
    fn at(shape: LightShape, position: Vec3, normalize: bool, point: bool, sizes: [f32; 4]) -> SceneLight {
        let mut m = math::IDENTITY;
        m[12] = position[0] as f64;
        m[13] = position[1] as f64;
        m[14] = position[2] as f64;
        SceneLight::placed(shape, [1.0; 3], normalize, point, 0.53, sizes, &m)
    }

    /// What lights a 0.5 grey plane facing +Z at the origin, from the
    /// light's `k` at its centre (the camera anywhere: k is in camera space
    /// but the same wherever the camera is).
    fn grey_on_plane(l: SceneLight) -> f32 {
        let lights = SceneLights::new(&[l]);
        let lighting = Lighting::new(&lights, &math::IDENTITY);
        let sky = lighting.sky[0];
        let mut k = sky;
        for lamp in &lighting.lamps[..lighting.count] {
            let (dir, e) = lamp.toward([0.0, 0.0, 0.0]);
            k += e[0] * dir[2].max(0.0);
        }
        0.5 * k
    }

    /// Karma CPU (Houdini 22.0.429) on a 0.5 grey Lambert plane, each
    /// light 2 units above it facing down: the brightness this module's
    /// table is taken from.
    #[test]
    fn lights_are_as_bright_as_in_karma() {
        let s = |n, p| at(LightShape::Sphere, [0.0, 0.0, 2.0], n, p, [0.1, 0.0, 0.0, 0.0]);
        let rect = |n| at(LightShape::Rect, [0.0, 0.0, 2.0], n, false, [0.0, 0.5, 0.5, 0.0]);
        let cyl = |n| at(LightShape::Cylinder, [0.0, 0.0, 2.0], n, false, [0.1, 0.0, 0.0, 1.0]);
        let distant = |n| SceneLight { angle: 0.53, ..at(LightShape::Distant, [0.0; 3], n, false, [0.0; 4]) };
        let cases: [(&str, SceneLight, f32, f32); 12] = [
            ("distant", distant(false), 0.000011, 0.1),
            ("distant normalized", distant(true), 0.158447, 0.01),
            ("sphere", s(false, false), 0.001244, 0.01),
            ("sphere normalized", s(true, false), 0.009895, 0.01),
            ("point", s(false, true), 0.039581, 0.01),
            ("point normalized", s(true, true), 0.009895, 0.01),
            ("rect", rect(false), 0.009705, 0.03),
            ("rect normalized", rect(true), 0.038818, 0.03),
            ("disk", at(LightShape::Disk, [0.0, 0.0, 2.0], false, false, [0.25, 0.0, 0.0, 0.0]), 0.007679, 0.03),
            ("cylinder", cyl(false), 0.010117, 0.03),
            ("cylinder normalized", cyl(true), 0.016495, 0.03),
            ("dome", at(LightShape::Dome, [0.0; 3], false, false, [0.0; 4]), 0.497314, 0.01),
        ];
        for (name, light, karma, tolerance) in cases {
            let got = grey_on_plane(light);
            assert!((got - karma).abs() <= tolerance * karma, "{name}: {got} here, {karma} in Karma");
        }
    }

    /// Karma's colour temperature tints (its distant light of normalized
    /// intensity 1 on a 0.5 grey plane, times pi / 0.5).
    #[test]
    fn colour_temperature_is_karmas() {
        let karma = |c: [f32; 3]| c.map(|v| v * PI / 0.5);
        for (kelvin, want) in [
            (3000.0, karma([0.279785, 0.134033, 0.042877])),
            (6500.0, karma([0.166016, 0.155640, 0.164062])),
            (9000.0, karma([0.143188, 0.157593, 0.212769])),
        ] {
            let got = blackbody(kelvin);
            for c in 0..3 {
                assert!((got[c] - want[c]).abs() <= 0.02 * want[c].max(0.2), "{kelvin} K: {got:?} vs Karma {want:?}");
            }
        }
    }

    proptest! {
        /// A colour temperature never darkens or brightens a light, only
        /// tints it: luminance 1, no channel negative.
        #[test]
        fn blackbody_has_luminance_one(kelvin in 0.0f32..40000.0) {
            let c = blackbody(kelvin);
            prop_assert!(c.iter().all(|&v| v >= 0.0 && v.is_finite()), "{c:?}");
            prop_assert!((luminance(c) - 1.0).abs() < 1e-4, "{c:?}");
        }

        /// A point light's light falls off with the square of the distance
        /// (past its own size), whatever its shape.
        #[test]
        fn light_falls_off_with_distance_squared(
            shape in prop::sample::select(vec![LightShape::Sphere, LightShape::Rect, LightShape::Disk,
                                               LightShape::Cylinder]),
            normalize: bool,
            d in 1.0f32..50.0,
            k in 1.5f32..4.0,
        ) {
            let near = grey_on_plane(at(shape, [0.0, 0.0, d], normalize, false, [0.1, 0.3, 0.3, 0.5]));
            let far = grey_on_plane(at(shape, [0.0, 0.0, d * k], normalize, false, [0.1, 0.3, 0.3, 0.5]));
            prop_assert!(near > 0.0);
            prop_assert!((near / far - k * k).abs() <= 1e-3 * k * k, "{near} at {d}, {far} at {}", d * k);
        }

        /// A rect or disk light lights only the side it faces.
        #[test]
        fn rect_and_disk_light_only_their_front(
            shape in prop::sample::select(vec![LightShape::Rect, LightShape::Disk]),
            d in 0.5f32..20.0,
        ) {
            // Facing -Z, above the plane: lit. Below it, facing away: dark.
            prop_assert!(grey_on_plane(at(shape, [0.0, 0.0, d], false, false, [0.2, 0.4, 0.4, 0.0])) > 0.0);
            let mut m = math::IDENTITY;
            m[14] = d as f64;
            m[10] = -1.0; // facing +Z, away from the plane
            let back = SceneLight::placed(shape, [1.0; 3], false, false, 0.0, [0.2, 0.4, 0.4, 0.0], &m);
            prop_assert_eq!(grey_on_plane(back), 0.0);
        }

        /// More than MAX_LIGHTS lights keep the brightest, every dome
        /// summed into one.
        #[test]
        fn the_brightest_lights_are_kept(
            intensities in prop::collection::vec(0.0f32..10.0, 0..40),
            domes in prop::collection::vec(0.0f32..2.0, 0..3),
        ) {
            let mut all: Vec<SceneLight> = intensities.iter()
                .map(|&i| SceneLight { radiance: [i; 3], normalize: true, ..SceneLight::OFF })
                .collect();
            all.extend(domes.iter().map(|&i| SceneLight { shape: LightShape::Dome, radiance: [i; 3], ..SceneLight::OFF }));
            let kept = SceneLights::new(&all);
            let room = if domes.is_empty() { MAX_LIGHTS } else { MAX_LIGHTS - 1 };
            let distant: Vec<f32> = kept.as_slice().iter().filter(|l| l.shape == LightShape::Distant)
                .map(|l| l.radiance[0]).collect();
            prop_assert_eq!(distant.len(), intensities.len().min(room));
            let mut sorted = intensities.clone();
            sorted.sort_by(|a, b| b.total_cmp(a));
            let least_kept = distant.iter().copied().fold(f32::INFINITY, f32::min);
            if let Some(&dropped) = sorted.get(room) {
                prop_assert!(least_kept >= dropped);
            }
            let dome: f32 = kept.as_slice().iter().filter(|l| l.shape == LightShape::Dome).map(|l| l.radiance[0]).sum();
            prop_assert!((dome - domes.iter().sum::<f32>()).abs() < 1e-4);
        }
    }
}
