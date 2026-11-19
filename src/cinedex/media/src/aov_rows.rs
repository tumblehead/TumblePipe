//! The rows of a preview's AOV chooser: the layers its EXR's header lists,
//! and the AOV folders beside the one it is in (TumblePipe renders one AOV
//! per folder; a file from elsewhere may hold many layers). Either way a
//! row says when it is not colour ([`LayerKind::note`]).

use crate::exr_sequence::{ExrLayer, ExrView, LayerKind, layer_kind};

/// The chooser's name for an unnamed layer of a file in no AOV folder (its
/// plain RGBA), as the editor's AOV dropdown has it.
pub const UNNAMED: &str = "RGBA";

/// What choosing a row shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AovTarget {
    /// This layer of the files being shown.
    Layer(String),
    /// The frames of AOV folder `i` (of the folders given).
    Folder(usize),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AovRow {
    pub label: String,
    pub target: AovTarget,
    pub kind: LayerKind,
}

fn labelled(name: &str, kind: LayerKind) -> String {
    match kind.note() {
        Some(note) => format!("{name} ({note})"),
        None => name.to_owned(),
    }
}

/// The chooser's rows for files whose header lists `layers` (none while
/// they open), in the AOV folder `shown` (None: in none) of `folders` (the
/// version's AOV folders, in chooser order): each folder a row, the shown
/// one's place taken by the file's layers (one row called as the folder is
/// for a file of one layer; `<aov> · <layer>` rows for more). A chooser of
/// one row or none has nothing to choose and is not shown.
pub fn aov_rows(layers: &[ExrLayer], shown: Option<&str>, folders: &[String]) -> Vec<AovRow> {
    let in_file = || -> Vec<AovRow> {
        if layers.is_empty() {
            // Not open yet: the folder stands for its file.
            return shown
                .map(|aov| {
                    let kind = layer_kind(aov, &[]);
                    AovRow { label: labelled(aov, kind), target: AovTarget::Layer(String::new()), kind }
                })
                .into_iter()
                .collect();
        }
        layers
            .iter()
            .map(|layer| {
                let name = match (shown, layer.name.is_empty()) {
                    (None, true) => UNNAMED.to_owned(),
                    (None, false) => layer.name.clone(),
                    (Some(aov), true) => aov.to_owned(),
                    (Some(aov), false) if layers.len() == 1 => aov.to_owned(),
                    (Some(aov), false) => format!("{aov} \u{b7} {}", layer.name),
                };
                let kind = layer.kind(shown);
                AovRow { label: labelled(&name, kind), target: AovTarget::Layer(layer.name.clone()), kind }
            })
            .collect()
    };
    if !shown.is_some_and(|aov| folders.iter().any(|f| f == aov)) {
        let mut rows = in_file();
        rows.extend(folders.iter().enumerate().map(|(i, folder)| {
            let kind = layer_kind(folder, &[]);
            AovRow { label: labelled(folder, kind), target: AovTarget::Folder(i), kind }
        }));
        return rows;
    }
    let mut rows = Vec::new();
    let mut placed = false;
    for (i, folder) in folders.iter().enumerate() {
        if Some(folder.as_str()) == shown && !placed {
            placed = true;
            rows.extend(in_file());
        } else {
            let kind = layer_kind(folder, &[]);
            rows.push(AovRow { label: labelled(folder, kind), target: AovTarget::Folder(i), kind });
        }
    }
    rows
}

/// The row showing `view`: its layer's.
pub fn aov_selected(rows: &[AovRow], view: &ExrView) -> Option<usize> {
    rows.iter().position(|r| r.target == AovTarget::Layer(view.layer.clone()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn layer(name: &str, channels: &[&str]) -> ExrLayer {
        ExrLayer { name: name.into(), channels: channels.iter().map(|c| (*c).to_string()).collect() }
    }

    fn labels(rows: &[AovRow]) -> Vec<&str> {
        rows.iter().map(|r| r.label.as_str()).collect()
    }

    #[test]
    fn a_render_s_chooser_is_its_version_s_aovs() {
        let folders: Vec<String> = ["beauty", "CryptoObject00", "depth", "diffuse"].iter().map(|s| (*s).to_string()).collect();
        let rgba = [layer("", &["R", "G", "B", "A"])];
        let rows = aov_rows(&rgba, Some("beauty"), &folders);
        assert_eq!(labels(&rows), ["beauty", "CryptoObject00 (cryptomatte ids, shown raw)", "depth (data, shown raw)", "diffuse"]);
        assert_eq!(rows[0].target, AovTarget::Layer(String::new()));
        assert_eq!(rows[2].target, AovTarget::Folder(2));
        assert_eq!(aov_selected(&rows, &ExrView::default()), Some(0));
        // Showing the depth folder: its file's one layer is the depth row.
        let rows = aov_rows(&[layer("", &["Z"])], Some("depth"), &folders);
        assert_eq!(labels(&rows)[2], "depth (data, shown raw)");
        assert_eq!(aov_selected(&rows, &ExrView::default()), Some(2));
        // Before the file opens the folder stands for it.
        assert_eq!(labels(&aov_rows(&[], Some("beauty"), &folders))[0], "beauty");
    }

    #[test]
    fn a_file_of_many_layers_lists_them_and_one_layer_nothing() {
        let many = [layer("", &["R", "G", "B"]), layer("diffuse", &["R", "G", "B"]), layer("depth", &["Z"])];
        assert_eq!(labels(&aov_rows(&many, None, &[])), ["RGBA", "diffuse", "depth (data, shown raw)"]);
        assert_eq!(labels(&aov_rows(&many, Some("beauty"), &["beauty".to_string()]))[1], "beauty \u{b7} diffuse");
        assert_eq!(aov_rows(&many[..1], None, &[]).len(), 1, "nothing to choose");
        assert_eq!(aov_rows(&[], None, &[]).len(), 0, "a movie, JPGs");
    }

    proptest! {
        /// Every layer of the file is a row, once, and so is every folder
        /// but the one shown; the row of any layer's view is found.
        #[test]
        fn every_layer_and_folder_is_a_row_once(
            names in prop::collection::btree_set("[a-z]{0,5}", 0..5),
            folders in prop::collection::btree_set("[a-z]{1,5}", 0..6),
            shown in prop::option::of(any::<prop::sample::Index>()),
        ) {
            let layers: Vec<ExrLayer> = names.iter().map(|n| layer(n, &["R", "G", "B"])).collect();
            let folders: Vec<String> = folders.into_iter().collect();
            let shown = shown.filter(|_| !folders.is_empty()).map(|i| folders[i.index(folders.len())].clone());
            let rows = aov_rows(&layers, shown.as_deref(), &folders);
            for l in &layers {
                let view = ExrView { layer: l.name.clone(), channel: None };
                let at = aov_selected(&rows, &view);
                prop_assert!(at.is_some());
                prop_assert_eq!(rows.iter().filter(|r| r.target == AovTarget::Layer(l.name.clone())).count(), 1);
            }
            for (i, folder) in folders.iter().enumerate() {
                let count = rows.iter().filter(|r| r.target == AovTarget::Folder(i)).count();
                prop_assert_eq!(count, usize::from(Some(folder) != shown.as_ref()));
            }
            let in_file = if layers.is_empty() { usize::from(shown.is_some()) } else { layers.len() };
            prop_assert_eq!(rows.len(), in_file + folders.len() - usize::from(shown.is_some()));
        }
    }
}
