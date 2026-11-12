//! A project's answers to the edit model's questions (`th_edit`): which
//! source frames a clip may show ([`th_edit::ops::Sources`]) and what a take
//! rule picks today ([`th_edit::freeze::Resolver`]).
//!
//! A shot's frames are its range with roll (what a playblast covers); a shot
//! the config does not have yet is *planned* (created when the master edit
//! is published) and has whatever frames its clip gives it. A media item's
//! are its version's (`crate::media`): a movie's frames from 0, a sound's
//! seconds at the edit's rate.

use th_edit::document::{Frame, FrozenTake, Rule, Source, Version, VersionRule};
use th_edit::freeze::Resolver;
use th_edit::ops::{Available, Sources};
use th_edit::takes;
use th_playlist::{Media, Take};

use crate::Project;
use crate::discovery::{playblast_takes, render_takes};
use crate::media::{self, ItemVersion};
use crate::timeline::get_frame_range;
use crate::uri::Uri;

/// A project, asked about an edit's sources, for an edit at `fps`.
pub struct EditSources<'a> {
    pub project: &'a Project,
    pub fps: u32,
}

impl<'a> EditSources<'a> {
    pub fn new(project: &'a Project, fps: u32) -> Self {
        EditSources { project, fps }
    }

    /// The version of `item` that `rule` picks.
    pub fn item(&self, item: &str, rule: &VersionRule) -> Result<ItemVersion, String> {
        let versions = media::item_versions(self.project, item)?;
        match rule {
            VersionRule::Latest => versions.into_iter().last(),
            VersionRule::Pinned(v) => versions.into_iter().find(|i| i.version == v.as_str()),
        }
        .ok_or_else(|| format!("no version of {item} fits"))
    }

    /// A shot clip's entity, or why `shot` names none.
    fn shot_uri(shot: &str) -> Result<Uri, String> {
        let uri = Uri::parse_address(shot).map_err(|e| e.to_string())?;
        if uri.purpose != "entity" || uri.segments.first().map(String::as_str) != Some("shots") || uri.segments.len() < 3 {
            return Err(format!("{shot} is not a shot"));
        }
        Ok(uri)
    }

    /// Every take `shot` has today: its playblasts, then its complete
    /// renders.
    pub fn takes(&self, shot: &str) -> Result<Vec<Take>, String> {
        let uri = Self::shot_uri(shot)?;
        let mut takes = playblast_takes(self.project, &uri)?;
        takes.extend(render_takes(self.project, &uri)?);
        Ok(takes)
    }
}

impl Sources<Source> for EditSources<'_> {
    fn available(&self, source: &Source) -> Available {
        match source {
            Source::Slug { .. } => Available::Any,
            Source::Media { item, version } => match self.item(item, version) {
                Ok(found) => {
                    let frames = match found.measure.frames {
                        Some((n, _)) => n as Frame,
                        None => (found.measure.seconds * f64::from(self.fps)).floor() as Frame,
                    };
                    if frames > 0 { Available::Frames(0, frames - 1) } else { Available::Nothing }
                }
                Err(_) => Available::Nothing,
            },
            Source::Shot { shot, .. } => {
                let Ok(uri) = Self::shot_uri(shot) else { return Available::Nothing };
                match self.project.config.exists(&uri) {
                    Ok(false) => Available::Any,
                    Err(_) => Available::Nothing,
                    Ok(true) => match get_frame_range(&self.project.config, &uri) {
                        Ok(Some(range)) => {
                            let full = range.full_range();
                            Available::Frames(full.first_frame as Frame, full.last_frame as Frame)
                        }
                        _ => Available::Nothing,
                    },
                }
            }
        }
    }
}

impl Resolver for EditSources<'_> {
    /// None for a planned shot (one the config does not have) and for a
    /// shot with no take its rule picks.
    fn take(&self, shot: &str, rule: &Rule) -> Result<Option<FrozenTake>, String> {
        let uri = Self::shot_uri(shot)?;
        if !self.project.config.exists(&uri).map_err(|e| e.to_string())? {
            return Ok(None);
        }
        let takes = self.takes(shot)?;
        takes::pick(&takes, rule).map(|index| takes::frozen(&takes[index])).transpose()
    }

    /// A media item's file, as a movie (a sound file too: the player
    /// opens it by its extension).
    fn media(&self, item: &str, rule: &VersionRule) -> Result<(Version, Media), String> {
        let found = self.item(item, rule)?;
        let path = crate::storage::slashed(&found.path);
        Ok((Version::try_from(found.version)?, Media::Movie { path }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::discovery::playblast_path;
    use crate::entities::create_shot;
    use crate::testing::make_project;
    use th_edit::document::TakeKind;

    #[test]
    fn a_shot_answers_its_range_and_its_takes() {
        let (_dir, root) = make_project();
        let db = root.join("_config").join("db");
        std::fs::write(
            db.join("departments.json"),
            r#"{"properties":{},"children":{"shots":{"properties":{},"children":{"layout":{"properties":{"independent":true,"publishable":true,"renderable":true},"children":{}},"animation":{"properties":{"independent":false,"publishable":true,"renderable":true},"children":{}}}}}}"#,
        )
        .unwrap();
        std::fs::write(
            db.join("schemas.json"),
            r#"{"properties":{},"children":{"entity":{"properties":{"roll_start":5,"roll_end":5},"children":{"shots":{"properties":{},"children":{"sequence":{"properties":{},"children":{"shot":{"properties":{},"children":{}}}}}}}}}}"#,
        )
        .unwrap();
        let project = Project::open(&root, &root).unwrap();
        let uri = create_shot(&project, "S", "010", 1001, 1040).unwrap();
        let project = Project::open(&root, &root).unwrap();
        for (department, version) in [("layout", "v0001"), ("animation", "v0002"), ("animation", "v0003")] {
            let path = playblast_path(&project, &uri, department, version).unwrap();
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, b"movie").unwrap();
        }
        let sources = EditSources::new(&project, 24);
        let shot = |name: &str| Source::Shot { shot: name.into(), take: Rule::best() };
        assert_eq!(sources.available(&shot("entity:/shots/S/010")), Available::Frames(996, 1045), "its range with roll");
        assert_eq!(sources.available(&shot("entity:/shots/S/020")), Available::Any, "a planned shot");
        assert_eq!(sources.available(&shot("entity:/assets/A/b")), Available::Nothing);

        let take = sources.take("entity:/shots/S/010", &Rule::best()).unwrap().unwrap();
        assert_eq!((take.department.as_str(), take.kind, take.version.as_str()), ("animation", TakeKind::Playblast, "v0003"));
        let layout = Rule { department: Some("layout".into()), ..Rule::best() };
        assert_eq!(sources.take("entity:/shots/S/010", &layout).unwrap().unwrap().version.as_str(), "v0001");
        assert_eq!(sources.take("entity:/shots/S/020", &Rule::best()), Ok(None), "a planned shot has no take");
        let missing = Rule { version: VersionRule::Pinned(Version::try_from("v0009".to_string()).unwrap()), ..Rule::best() };
        assert_eq!(sources.take("entity:/shots/S/010", &missing), Ok(None), "no take fits");
        assert!(sources.take("entity:/assets/A/b", &Rule::best()).is_err());
    }
}
