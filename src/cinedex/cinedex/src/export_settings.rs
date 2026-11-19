//! What File > Export Movie… (and the agent's `export_movie`) can be asked
//! for: the movie's size, which frames of the cut, the codec and how hard
//! it squeezes, the sound, and the viewer's burn-ins.
//!
//! [`Settings`] is the choice as it is kept (this user's `export.json`,
//! beside the layout file) and as an agent gives it. [`Settings::plan`]
//! turns it into a [`Plan`] for one edit, or says why it cannot be made.
//! [`Form`] is the dialog's state: the same choice with its number fields
//! as typed, read through `number_field` like every other number field.
//!
//! Frames are counted as the dialog counts them, from 1 at the cut's start
//! to its length, both ends included.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use th_pipe::movie::{Codec, Quality};

use crate::number_field;

/// The smallest and largest side a movie is made.
pub const SIDE_RANGE: (u32, u32) = (16, 16384);

/// The movie's size.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Size {
    /// The edit's resolution.
    #[default]
    Edit,
    /// Half of it each way.
    Half,
    /// Another size of the edit's shape.
    Custom { width: u32, height: u32 },
}

/// Which frames of the cut.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Span {
    /// All of it.
    #[default]
    Whole,
    /// From the first selected clip's start to the last one's end.
    Selection,
    /// The range playback loops over (a Live view's shot).
    Loop,
    /// Frames `first` to `last`, counted from 1, both included.
    Custom { first: i64, last: i64 },
}

/// The kinds of [`Size`] and [`Span`], as the dialog's buttons.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SizeKind {
    Edit,
    Half,
    Custom,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpanKind {
    Whole,
    Selection,
    Loop,
    Custom,
}

impl SizeKind {
    pub const ALL: [SizeKind; 3] = [SizeKind::Edit, SizeKind::Half, SizeKind::Custom];
}

/// The spans an edit offers now: its length, and where its selection and
/// its loop are (`start..end`, from 0) when it has them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Spans {
    pub length: usize,
    pub selection: Option<(usize, usize)>,
    pub looped: Option<(usize, usize)>,
}

impl Spans {
    /// The kinds on offer, in the dialog's order: the selection and the
    /// loop only when there is one.
    pub fn kinds(&self) -> Vec<SpanKind> {
        let mut kinds = vec![SpanKind::Whole];
        if self.selection.is_some() {
            kinds.push(SpanKind::Selection);
        }
        if self.looped.is_some() {
            kinds.push(SpanKind::Loop);
        }
        kinds.push(SpanKind::Custom);
        kinds
    }
}

/// The frames a set of clips covers, each `(start, end)` from 0: from the
/// first start to the last end; None for no clips, or none with a frame.
pub fn extent(clips: impl IntoIterator<Item = (i64, i64)>) -> Option<(usize, usize)> {
    let (start, end) = clips
        .into_iter()
        .fold(None, |all: Option<(i64, i64)>, (at, end)| Some(all.map_or((at, end), |(first, last)| (first.min(at), last.max(end)))))?;
    let (start, end) = (start.max(0) as usize, end.max(0) as usize);
    (start < end).then_some((start, end))
}

/// What to export, as kept and as asked for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Settings {
    pub size: Size,
    pub span: Span,
    pub codec: Codec,
    pub quality: Quality,
    /// The cut's sound in the movie.
    pub audio: bool,
    /// The viewer's burn-ins on the picture.
    pub burnins: bool,
}

impl Default for Settings {
    /// What Export Movie made before it had settings: the whole cut at the
    /// edit's size, the farm's H.264, with its sound, clean.
    fn default() -> Self {
        Self { size: Size::Edit, span: Span::Whole, codec: Codec::H264, quality: Quality::Standard, audio: true, burnins: false }
    }
}

/// One export, worked out for one edit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Plan {
    pub width: u32,
    pub height: u32,
    /// The first frame of the cut exported, from 0, and how many.
    pub first: usize,
    pub frames: usize,
    pub codec: Codec,
    pub quality: Quality,
    pub audio: bool,
    pub burnins: bool,
}

impl Plan {
    /// The frames of the cut it exports, from 0, in order.
    pub fn range(&self) -> std::ops::Range<usize> {
        self.first..self.first + self.frames
    }
}

fn rounded(value: f64) -> u32 {
    value.round().clamp(1.0, f64::from(u32::MAX)) as u32
}

/// Half of `resolution` each way, a half pixel rounded up.
pub fn half(resolution: [u32; 2]) -> [u32; 2] {
    resolution.map(|side| side.div_ceil(2).max(1))
}

/// The size `width` wide of `resolution`'s shape.
pub fn from_width(resolution: [u32; 2], width: u32) -> [u32; 2] {
    let [w, h] = resolution.map(|side| f64::from(side.max(1)));
    [width, rounded(f64::from(width) * h / w)]
}

/// The size `height` tall of `resolution`'s shape.
pub fn from_height(resolution: [u32; 2], height: u32) -> [u32; 2] {
    let [w, h] = resolution.map(|side| f64::from(side.max(1)));
    [rounded(f64::from(height) * w / h), height]
}

/// Whether `size` is of `resolution`'s shape, to the pixel a side rounds.
pub fn keeps_shape(resolution: [u32; 2], size: [u32; 2]) -> bool {
    from_width(resolution, size[0]) == size || from_height(resolution, size[1]) == size
}

impl Settings {
    /// The export of an edit `resolution` big that offers `spans`, or why
    /// there is none.
    pub fn plan(&self, resolution: [u32; 2], spans: &Spans) -> Result<Plan, String> {
        let [width, height] = match self.size {
            Size::Edit => resolution,
            Size::Half => half(resolution),
            Size::Custom { width, height } => {
                let within = |side: u32| (SIDE_RANGE.0..=SIDE_RANGE.1).contains(&side);
                if !within(width) || !within(height) {
                    return Err(format!("A movie is {} to {} pixels a side, not {width}x{height}", SIDE_RANGE.0, SIDE_RANGE.1));
                }
                if !keeps_shape(resolution, [width, height]) {
                    let [w, h] = from_width(resolution, width);
                    return Err(format!("{width}x{height} is not the edit's shape ({}x{}): {w}x{h} is", resolution[0], resolution[1]));
                }
                [width, height]
            }
        };
        let length = spans.length;
        let (first, end) = match self.span {
            Span::Whole => (0, length),
            Span::Selection => spans.selection.ok_or("No clip is selected: there is no selection to export")?,
            Span::Loop => spans.looped.ok_or("Nothing loops: there is no loop range to export")?,
            Span::Custom { first, last } => {
                if first < 1 || last < first || last > length as i64 {
                    return Err(format!("Frames {first} to {last} are not in the cut: it has frames 1 to {length}"));
                }
                ((first - 1) as usize, last as usize)
            }
        };
        let end = end.min(length);
        if first >= end {
            return Err("The edit is empty there: there is nothing to export".into());
        }
        Ok(Plan {
            width,
            height,
            first,
            frames: end - first,
            codec: self.codec,
            quality: self.quality,
            audio: self.audio,
            burnins: self.burnins,
        })
    }

    /// These settings as they suit another edit: a kept choice that edit
    /// cannot honour (a selection or loop it has none of, a custom range
    /// outside it, a custom size of another shape) goes back to the whole
    /// cut, or to the size as wide of this edit's shape.
    pub fn fitted(mut self, resolution: [u32; 2], spans: &Spans) -> Self {
        if let Size::Custom { width, height } = self.size
            && !keeps_shape(resolution, [width, height])
        {
            let [width, height] = from_width(resolution, width);
            self.size = Size::Custom { width, height };
        }
        if self.plan(resolution, spans).is_err() {
            self.span = Span::Whole;
        }
        if self.plan(resolution, spans).is_err() {
            self.size = Size::Edit;
        }
        self
    }

    pub fn to_json(self) -> Value {
        let resolution = match self.size {
            Size::Edit => json!("edit"),
            Size::Half => json!("half"),
            Size::Custom { width, height } => json!({"width": width, "height": height}),
        };
        let range = match self.span {
            Span::Whole => json!("whole"),
            Span::Selection => json!("selection"),
            Span::Loop => json!("loop"),
            Span::Custom { first, last } => json!({"first": first, "last": last}),
        };
        json!({
            "resolution": resolution,
            "range": range,
            "codec": self.codec.word(),
            "quality": self.quality.word(),
            "audio": self.audio,
            "burnins": self.burnins,
        })
    }

    /// What `value` asks for, each thing it does not say as [`Default`];
    /// refused when it says something that is not a choice. A custom size
    /// may give one side: the other is the edit's shape's (`resolution`).
    pub fn parse(value: &Value, resolution: [u32; 2]) -> Result<Self, String> {
        let mut settings = Self::default();
        let side = |v: &Value, name: &str| -> Result<u32, String> {
            v.as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .filter(|n| *n > 0)
                .ok_or_else(|| format!("{name} is a whole number of pixels, not {v}"))
        };
        match value.get("resolution") {
            None | Some(Value::Null) => {}
            Some(Value::String(word)) if word == "edit" => settings.size = Size::Edit,
            Some(Value::String(word)) if word == "half" => settings.size = Size::Half,
            Some(Value::Object(sides)) => {
                let [width, height] = match (sides.get("width"), sides.get("height")) {
                    (Some(w), Some(h)) => [side(w, "width")?, side(h, "height")?],
                    (Some(w), None) => from_width(resolution, side(w, "width")?),
                    (None, Some(h)) => from_height(resolution, side(h, "height")?),
                    (None, None) => return Err("resolution names a width, a height or both".into()),
                };
                settings.size = Size::Custom { width, height };
            }
            Some(other) => return Err(format!("resolution is \"edit\", \"half\" or {{\"width\": n}}, not {other}")),
        }
        match value.get("range") {
            None | Some(Value::Null) => {}
            Some(Value::String(word)) if word == "whole" => settings.span = Span::Whole,
            Some(Value::String(word)) if word == "selection" => settings.span = Span::Selection,
            Some(Value::String(word)) if word == "loop" => settings.span = Span::Loop,
            Some(Value::Object(ends)) => match (ends.get("first").and_then(Value::as_i64), ends.get("last").and_then(Value::as_i64)) {
                (Some(first), Some(last)) => settings.span = Span::Custom { first, last },
                _ => return Err("range names a first and a last frame, whole numbers from 1".into()),
            },
            Some(other) => {
                return Err(format!("range is \"whole\", \"selection\", \"loop\" or {{\"first\": a, \"last\": b}}, not {other}"));
            }
        }
        if let Some(word) = value.get("codec").filter(|v| !v.is_null()) {
            settings.codec = word.as_str().and_then(Codec::named).ok_or_else(|| format!("codec is \"h264\" or \"mjpeg\", not {word}"))?;
        }
        if let Some(word) = value.get("quality").filter(|v| !v.is_null()) {
            settings.quality = word
                .as_str()
                .and_then(Quality::named)
                .ok_or_else(|| format!("quality is \"best\", \"high\", \"standard\" or \"small\", not {word}"))?;
        }
        for (key, flag) in [("audio", &mut settings.audio), ("burnins", &mut settings.burnins)] {
            if let Some(v) = value.get(key).filter(|v| !v.is_null()) {
                *flag = v.as_bool().ok_or_else(|| format!("{key} is true or false, not {v}"))?;
            }
        }
        Ok(settings)
    }

    /// The settings a file's JSON says: each thing it says that is a
    /// choice, the rest (and nonsense) at its default.
    pub fn from_json(value: &Value, resolution: [u32; 2]) -> Self {
        let mut settings = Self::default();
        for key in ["resolution", "range", "codec", "quality", "audio", "burnins"] {
            let Some(said) = value.get(key) else { continue };
            let Ok(one) = Self::parse(&json!({ key: said }), resolution) else { continue };
            match key {
                "resolution" => settings.size = one.size,
                "range" => settings.span = one.span,
                "codec" => settings.codec = one.codec,
                "quality" => settings.quality = one.quality,
                "audio" => settings.audio = one.audio,
                _ => settings.burnins = one.burnins,
            }
        }
        settings
    }
}

/// This user's export settings file, beside their layout file.
pub fn path(layout: &Path) -> PathBuf {
    layout.with_file_name("export.json")
}

/// The settings saved at `path` for an edit `resolution` big; the default
/// when there are none, or they do not read.
pub fn load(path: &Path, resolution: [u32; 2]) -> Settings {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .map_or_else(Settings::default, |v| Settings::from_json(&v, resolution))
}

/// Save `settings` at `path`, whole (written beside it and renamed).
pub fn save(path: &Path, settings: &Settings) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let partial = path.with_extension(format!("json.{}.partial", std::process::id()));
    std::fs::write(&partial, serde_json::to_vec_pretty(&settings.to_json()).map_err(std::io::Error::other)?)?;
    std::fs::rename(&partial, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&partial);
    })
}

/// The dialog's choice: [`Settings`] with its number fields as typed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Form {
    pub size: SizeKind,
    pub width: String,
    pub height: String,
    pub span: SpanKind,
    pub first: String,
    pub last: String,
    pub codec: Codec,
    pub quality: Quality,
    pub audio: bool,
    pub burnins: bool,
}

impl Form {
    /// The form showing `settings` for an edit `resolution` big offering
    /// `spans`. The custom fields start at the edit's size and whole cut.
    pub fn new(settings: &Settings, resolution: [u32; 2], spans: &Spans) -> Self {
        let settings = settings.fitted(resolution, spans);
        let (size, [width, height]) = match settings.size {
            Size::Edit => (SizeKind::Edit, resolution),
            Size::Half => (SizeKind::Half, resolution),
            Size::Custom { width, height } => (SizeKind::Custom, [width, height]),
        };
        let (span, first, last) = match settings.span {
            Span::Whole => (SpanKind::Whole, 1, spans.length as i64),
            Span::Selection => (SpanKind::Selection, 1, spans.length as i64),
            Span::Loop => (SpanKind::Loop, 1, spans.length as i64),
            Span::Custom { first, last } => (SpanKind::Custom, first, last),
        };
        Self {
            size,
            width: width.to_string(),
            height: height.to_string(),
            span,
            first: first.to_string(),
            last: last.to_string(),
            codec: settings.codec,
            quality: settings.quality,
            audio: settings.audio,
            burnins: settings.burnins,
        }
    }

    /// A side as typed (a number, or a sum), its present value for `+=`.
    fn side(typed: &str, held: &str, name: &str) -> Result<u32, String> {
        let current = held.trim().parse::<i64>().ok();
        match number_field::whole(typed, current) {
            Ok(n) => u32::try_from(n).ok().filter(|n| *n > 0).ok_or_else(|| format!("The {name} is a number of pixels, not {n}")),
            Err(problem) => Err(format!("The {name}: {}", number_field::refusal(typed, Some(problem), "a number of pixels"))),
        }
    }

    /// The width was typed: the height follows, in the edit's shape.
    pub fn type_width(&mut self, typed: String, resolution: [u32; 2]) {
        if let Ok(width) = Self::side(&typed, &self.width, "width") {
            self.height = from_width(resolution, width)[1].to_string();
        }
        self.width = typed;
    }

    /// The height was typed: the width follows.
    pub fn type_height(&mut self, typed: String, resolution: [u32; 2]) {
        if let Ok(height) = Self::side(&typed, &self.height, "height") {
            self.width = from_height(resolution, height)[0].to_string();
        }
        self.height = typed;
    }

    /// What the form asks for, its custom fields read; or the field that
    /// does not read.
    pub fn settings(&self) -> Result<Settings, String> {
        let size = match self.size {
            SizeKind::Edit => Size::Edit,
            SizeKind::Half => Size::Half,
            SizeKind::Custom => {
                Size::Custom { width: Self::side(&self.width, "", "width")?, height: Self::side(&self.height, "", "height")? }
            }
        };
        let frame = |typed: &str, name: &str| {
            number_field::whole(typed, None).map_err(|problem| {
                format!("The {name} frame: {}", number_field::refusal(typed, Some(problem), "a frame of the cut, from 1"))
            })
        };
        let span = match self.span {
            SpanKind::Whole => Span::Whole,
            SpanKind::Selection => Span::Selection,
            SpanKind::Loop => Span::Loop,
            SpanKind::Custom => Span::Custom { first: frame(&self.first, "first")?, last: frame(&self.last, "last")? },
        };
        Ok(Settings { size, span, codec: self.codec, quality: self.quality, audio: self.audio, burnins: self.burnins })
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use proptest::prelude::*;

    pub(crate) fn sizes() -> impl Strategy<Value = Size> {
        prop_oneof![
            Just(Size::Edit),
            Just(Size::Half),
            (1u32..20_000, 1u32..20_000).prop_map(|(width, height)| Size::Custom { width, height })
        ]
    }

    pub(crate) fn span_choices() -> impl Strategy<Value = Span> {
        prop_oneof![
            Just(Span::Whole),
            Just(Span::Selection),
            Just(Span::Loop),
            (-3i64..400, -3i64..400).prop_map(|(first, last)| Span::Custom { first, last })
        ]
    }

    pub(crate) fn settings() -> impl Strategy<Value = Settings> {
        (
            sizes(),
            span_choices(),
            prop::sample::select(Codec::ALL.to_vec()),
            prop::sample::select(Quality::ALL.to_vec()),
            any::<bool>(),
            any::<bool>(),
        )
            .prop_map(|(size, span, codec, quality, audio, burnins)| Settings { size, span, codec, quality, audio, burnins })
    }

    fn range(length: usize) -> impl Strategy<Value = Option<(usize, usize)>> {
        prop::option::of(
            (0..length.max(1), 1..=length.max(1)).prop_map(|(a, b)| (a.min(b), a.max(b))).prop_filter("not empty", |(a, b)| a < b),
        )
    }

    pub(crate) fn spans() -> impl Strategy<Value = Spans> {
        (1usize..300).prop_flat_map(|length| (Just(length), range(length), range(length))).prop_map(|(length, selection, looped)| Spans {
            length,
            selection,
            looped,
        })
    }

    fn resolutions() -> impl Strategy<Value = [u32; 2]> {
        prop_oneof![Just([1920, 1080]), Just([2048, 858]), Just([1080, 1920]), (16u32..4000, 16u32..4000).prop_map(|(w, h)| [w, h])]
    }

    proptest! {
        /// A plan is the frames asked for, counted by hand: the whole cut,
        /// the selection, the loop, or first to last from 1; at the size
        /// asked for. Anything outside the cut or its shape has no plan.
        #[test]
        fn a_plan_is_the_frames_and_size_asked_for(settings in settings(), resolution in resolutions(), spans in spans()) {
            let frames: Option<Vec<usize>> = match settings.span {
                Span::Whole => Some((0..spans.length).collect()),
                Span::Selection => spans.selection.map(|(a, b)| (a..b).collect()),
                Span::Loop => spans.looped.map(|(a, b)| (a..b).collect()),
                Span::Custom { first, last } => {
                    let all: Vec<i64> = (1..=spans.length as i64).collect();
                    (all.contains(&first) && all.contains(&last) && first <= last).then(|| (first - 1..last).map(|f| f as usize).collect())
                }
            };
            let size = match settings.size {
                Size::Edit => Some(resolution),
                Size::Half => Some([resolution[0].div_ceil(2), resolution[1].div_ceil(2)]),
                Size::Custom { width, height } => {
                    let shaped = |w: u32, h: u32| {
                        // The shape to the pixel: one side's ideal partner rounds to the other.
                        let ideal_h = f64::from(w) * f64::from(resolution[1]) / f64::from(resolution[0]);
                        let ideal_w = f64::from(h) * f64::from(resolution[0]) / f64::from(resolution[1]);
                        ideal_h.round() as u32 == h || ideal_w.round() as u32 == w
                    };
                    ((16..=16384).contains(&width) && (16..=16384).contains(&height) && shaped(width, height)).then_some([width, height])
                }
            };
            match (settings.plan(resolution, &spans), frames, size) {
                (Ok(plan), Some(frames), Some(size)) => {
                    prop_assert_eq!((plan.first, plan.frames), (frames[0], frames.len()));
                    prop_assert_eq!(plan.range().collect::<Vec<_>>(), frames);
                    prop_assert_eq!([plan.width, plan.height], size);
                    prop_assert_eq!((plan.codec, plan.quality, plan.audio, plan.burnins), (settings.codec, settings.quality, settings.audio, settings.burnins));
                }
                (Err(_), frames, size) => prop_assert!(frames.is_none() || size.is_none(), "refused though it fits"),
                (Ok(plan), frames, size) => prop_assert!(false, "{:?} planned for {:?} {:?}", plan, frames, size),
            }
        }

        /// A custom size from one side is the edit's shape to the pixel,
        /// and typing its other side gives it back.
        #[test]
        fn one_side_gives_the_other(resolution in resolutions(), side in 16u32..8000) {
            let [w, h] = from_width(resolution, side);
            prop_assert_eq!(w, side);
            prop_assert!((f64::from(h) - f64::from(side) * f64::from(resolution[1]) / f64::from(resolution[0])).abs() <= 0.5);
            let [w2, h2] = from_height(resolution, side);
            prop_assert_eq!(h2, side);
            prop_assert!((f64::from(w2) - f64::from(side) * f64::from(resolution[0]) / f64::from(resolution[1])).abs() <= 0.5);
            prop_assert!(keeps_shape(resolution, [w, h]) && keeps_shape(resolution, [w2, h2]));
            prop_assert_eq!(from_width(resolution, resolution[0]), resolution);
            prop_assert_eq!(from_height(resolution, resolution[1]), resolution);
        }

        /// Settings read back from their JSON; a file that says nonsense
        /// for one thing keeps the rest.
        #[test]
        fn settings_read_back(settings in settings(), resolution in resolutions(), spoil in prop::option::of(0usize..6)) {
            let mut value = settings.to_json();
            prop_assert_eq!(Settings::parse(&value, resolution), Ok(settings));
            prop_assert_eq!(Settings::from_json(&value, resolution), settings);
            let keys = ["resolution", "range", "codec", "quality", "audio", "burnins"];
            if let Some(i) = spoil {
                value[keys[i]] = json!([1, 2]);
                prop_assert!(Settings::parse(&value, resolution).is_err());
                let read = Settings::from_json(&value, resolution);
                let default = Settings::default();
                let want = Settings {
                    size: if i == 0 { default.size } else { settings.size },
                    span: if i == 1 { default.span } else { settings.span },
                    codec: if i == 2 { default.codec } else { settings.codec },
                    quality: if i == 3 { default.quality } else { settings.quality },
                    audio: if i == 4 { default.audio } else { settings.audio },
                    burnins: if i == 5 { default.burnins } else { settings.burnins },
                };
                prop_assert_eq!(read, want);
            }
            prop_assert_eq!(Settings::parse(&json!({}), resolution), Ok(Settings::default()));
        }

        /// An agent may name one side of a custom size: the other is the
        /// edit's shape's. Both named are taken as named (and judged by
        /// the plan); a side that is no whole number of pixels is refused.
        #[test]
        fn an_agents_custom_size_may_name_one_side(resolution in resolutions(), width in 16u32..8000, height in 16u32..8000) {
            let size = |value: Value| Settings::parse(&json!({"resolution": value}), resolution).map(|s| s.size);
            let [w, h] = from_width(resolution, width);
            prop_assert_eq!(size(json!({"width": width})), Ok(Size::Custom { width: w, height: h }));
            let [w, h] = from_height(resolution, height);
            prop_assert_eq!(size(json!({"height": height})), Ok(Size::Custom { width: w, height: h }));
            prop_assert_eq!(size(json!({"width": width, "height": height})), Ok(Size::Custom { width, height }));
            for nonsense in [json!({}), json!({"width": 0}), json!({"height": "tall"}), json!("quarter")] {
                let refused = size(nonsense.clone()).is_err();
                prop_assert!(refused, "{} taken as a size", nonsense);
            }
        }

        /// Settings kept from another edit always make a plan here, and
        /// change only what this edit cannot honour.
        #[test]
        fn kept_settings_fit_the_edit_at_hand(settings in settings(), resolution in resolutions(), spans in spans()) {
            let fitted = settings.fitted(resolution, &spans);
            prop_assert!(fitted.plan(resolution, &spans).is_ok());
            if settings.plan(resolution, &spans).is_ok() {
                prop_assert_eq!(fitted, settings);
            }
            prop_assert_eq!((fitted.codec, fitted.quality, fitted.audio, fitted.burnins), (settings.codec, settings.quality, settings.audio, settings.burnins));
            // A form of them asks for the same again.
            let form = Form::new(&settings, resolution, &spans);
            prop_assert_eq!(form.settings(), Ok(fitted));
            prop_assert!(spans.kinds().contains(&form.span));
        }

        /// One side typed into the form (a number or a sum) sets the other
        /// in the edit's shape, and the form then asks for that size.
        #[test]
        fn a_typed_side_brings_the_other(resolution in resolutions(), side in 16u32..4000, wide in any::<bool>(), sum in any::<bool>()) {
            let spans = Spans { length: 10, ..Spans::default() };
            let mut form = Form::new(&Settings::default(), resolution, &spans);
            form.size = SizeKind::Custom;
            let typed = if sum { format!("{}/2", u64::from(side) * 2) } else { side.to_string() };
            let want = if wide {
                form.type_width(typed, resolution);
                from_width(resolution, side)
            } else {
                form.type_height(typed, resolution);
                from_height(resolution, side)
            };
            prop_assert_eq!(form.settings().map(|s| s.size), Ok(Size::Custom { width: want[0], height: want[1] }));
            // Something that is no number leaves the other side alone and is said.
            let before = form.clone();
            form.type_width("wide".into(), resolution);
            prop_assert_eq!(&form.height, &before.height);
            prop_assert!(form.settings().unwrap_err().contains("width"));
        }
    }

    proptest! {
        /// Selected clips' extent is every frame from the first of them to
        /// the last, the gaps between them too.
        #[test]
        fn a_selections_extent_spans_its_clips(clips in prop::collection::vec((0i64..200, 1i64..50), 0..6)) {
            let clips: Vec<(i64, i64)> = clips.into_iter().map(|(at, long)| (at, at + long)).collect();
            let covered: Vec<i64> = (0..260).filter(|f| clips.iter().any(|(a, b)| (a..b).contains(&f))).collect();
            match extent(clips.iter().copied()) {
                None => prop_assert!(covered.is_empty()),
                Some((start, end)) => {
                    prop_assert_eq!(start as i64, covered[0]);
                    prop_assert_eq!(end as i64, covered[covered.len() - 1] + 1);
                }
            }
        }
    }

    /// The offered spans: the selection and the loop only when there are.
    #[test]
    fn spans_offer_what_there_is() {
        let none = Spans { length: 5, ..Spans::default() };
        assert_eq!(none.kinds(), [SpanKind::Whole, SpanKind::Custom]);
        let both = Spans { length: 5, selection: Some((1, 3)), looped: Some((0, 2)) };
        assert_eq!(both.kinds(), [SpanKind::Whole, SpanKind::Selection, SpanKind::Loop, SpanKind::Custom]);
        // Custom frames are typed from 1 and may be sums.
        let mut form = Form::new(&Settings::default(), [1920, 1080], &both);
        form.span = SpanKind::Custom;
        form.first = "1+1".into();
        form.last = "10/2".into();
        assert_eq!(form.settings().unwrap().span, Span::Custom { first: 2, last: 5 });
        assert_eq!(form.settings().unwrap().plan([1920, 1080], &both).map(|p| (p.first, p.frames)), Ok((1, 4)));
        form.last = "x".into();
        assert!(form.settings().unwrap_err().contains("last frame"));
    }

    /// Settings saved are the settings loaded; no file is the default.
    #[test]
    fn settings_are_kept_in_a_file() {
        let dir = tempfile::tempdir().unwrap();
        let file = path(&dir.path().join("layout.json"));
        assert_eq!(file, dir.path().join("export.json"));
        assert_eq!(load(&file, [1920, 1080]), Settings::default());
        let kept =
            Settings { size: Size::Half, codec: Codec::Mjpeg, quality: Quality::Best, audio: false, burnins: true, ..Settings::default() };
        save(&file, &kept).unwrap();
        assert_eq!(load(&file, [1920, 1080]), kept);
        std::fs::write(&file, b"not json").unwrap();
        assert_eq!(load(&file, [1920, 1080]), Settings::default());
    }
}
