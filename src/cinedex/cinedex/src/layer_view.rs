//! What the viewer shows of the take on screen (#58): which AOV of a render
//! (its EXR layer, `ExrView`) and which layers of a stack (`Content::hidden`).
//! The viewer's AOV dropdown and layer toggles set them. View only: they are
//! never saved in the edit, and a take resolved again keeps them where it
//! still has the AOV or the layer.

use th_media::{ExrLayer, ExrView};

use crate::engine::is_hidden;

/// The AOV dropdown's name for a render's unnamed layer (its plain RGBA).
pub const UNNAMED: &str = "RGBA";

/// The AOV dropdown's rows: a render's EXR layers in file order.
pub fn aov_names(layers: &[ExrLayer]) -> Vec<String> {
    layers.iter().map(|l| if l.name.is_empty() { UNNAMED.to_string() } else { l.name.clone() }).collect()
}

/// The dropdown row `view` shows, if it is one of `layers`.
pub fn aov_index(layers: &[ExrLayer], view: &ExrView) -> Option<usize> {
    layers.iter().position(|l| l.name == view.layer)
}

/// The view of dropdown row `index`: that layer's colour.
pub fn aov_view(layers: &[ExrLayer], index: usize) -> Option<ExrView> {
    layers.get(index).map(|l| ExrView { layer: l.name.clone(), channel: None })
}

/// The view a take resolved again opens on: the one shown before when the
/// new media still has that layer, else `default`.
pub fn kept_view(before: Option<&ExrView>, layers: &[ExrLayer], default: ExrView) -> ExrView {
    match before {
        Some(view) if aov_index(layers, view).is_some() => view.clone(),
        _ => default,
    }
}

/// `hidden` with stack layer `layer` shown or hidden; layers past the 64
/// a mask holds are always shown.
pub fn set_shown(hidden: u64, layer: usize, shown: bool) -> u64 {
    if layer >= 64 {
        return hidden;
    }
    match shown {
        true => hidden & !(1 << layer),
        false => hidden | (1 << layer),
    }
}

/// How many stack layers the display row shows as tick boxes side by side;
/// a stack of more shows them in a strip that scrolls sideways, so a stack
/// of many layers no longer crowds the row (#58).
pub const LAYERS_INLINE: usize = 4;

/// The display row's label of a stack of `count` layers, `hidden` hidden:
/// "Layers" while every one shows, else how many show of how many (a strip
/// that scrolls may have hidden ones out of sight).
pub fn layers_label(count: usize, hidden: u64) -> String {
    let shown = (0..count).filter(|&i| !is_hidden(hidden, i)).count();
    if shown == count { "Layers".into() } else { format!("Layers {shown} of {count}") }
}

/// The hidden mask of a stack resolved again, `after` its layer names: a
/// layer hidden `before` (by name) stays hidden; a new layer shows.
pub fn kept_hidden(before: &[String], hidden: u64, after: &[&str]) -> u64 {
    after.iter().enumerate().fold(0, |mask, (i, name)| {
        let was_hidden = before.iter().position(|b| b == name).is_some_and(|j| is_hidden(hidden, j));
        set_shown(mask, i, !was_hidden)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn layers(names: &[&str]) -> Vec<ExrLayer> {
        names.iter().map(|n| ExrLayer { name: (*n).into(), channels: vec!["R".into(), "G".into(), "B".into()] }).collect()
    }

    #[test]
    fn the_unnamed_layer_is_rgba() {
        let l = layers(&["", "diffuse", "Pworld"]);
        assert_eq!(aov_names(&l), ["RGBA", "diffuse", "Pworld"]);
        assert_eq!(aov_view(&l, 1), Some(ExrView { layer: "diffuse".into(), channel: None }));
        assert_eq!(aov_view(&l, 3), None);
    }

    fn arb_names() -> impl Strategy<Value = Vec<String>> {
        prop::collection::btree_set("[a-z]{1,6}", 0..8).prop_map(|s| s.into_iter().collect())
    }

    proptest! {
        /// The display row's Layers label counts the layers that show: plain
        /// while all do; else "Layers <shown> of <count>", <shown> the tick
        /// boxes ticked (layers past the mask's 64 always show).
        #[test]
        fn the_layers_label_counts_what_shows(count in 1usize..80, hidden in any::<u64>(), toggles in prop::collection::vec((0usize..80, any::<bool>()), 0..10)) {
            let hidden = toggles.iter().fold(hidden, |mask, &(i, on)| set_shown(mask, i, on));
            let shown = (0..count).filter(|&i| !is_hidden(hidden, i)).count();
            let label = layers_label(count, hidden);
            if shown == count {
                prop_assert_eq!(label, "Layers");
            } else {
                prop_assert_eq!(label, format!("Layers {shown} of {count}"));
                prop_assert!(shown < count);
            }
            // Every layer shown again: plain.
            let all = (0..count).fold(hidden, |mask, i| set_shown(mask, i, true));
            prop_assert_eq!(layers_label(count, all), "Layers");
        }
    }

    proptest! {
        /// Each dropdown row's view is that row again; a view kept across a
        /// resolve is the one before exactly when the new media has its
        /// layer, else the default.
        #[test]
        fn a_dropdown_row_shows_its_layer_and_a_resolve_keeps_it(
            names in arb_names(),
            again in arb_names(),
            pick in any::<prop::sample::Index>(),
        ) {
            let refs: Vec<&str> = names.iter().map(String::as_str).collect();
            let l = layers(&refs);
            prop_assert_eq!(aov_names(&l).len(), l.len());
            if !l.is_empty() {
                let i = pick.index(l.len());
                let view = aov_view(&l, i).unwrap();
                prop_assert_eq!(aov_index(&l, &view), Some(i));
                let refs: Vec<&str> = again.iter().map(String::as_str).collect();
                let after = layers(&refs);
                let default = ExrView { layer: "default".into(), channel: None };
                let kept = kept_view(Some(&view), &after, default.clone());
                match again.contains(&names[i]) {
                    true => prop_assert_eq!(kept, view),
                    false => prop_assert_eq!(kept, default),
                }
            }
        }

        /// A toggle changes its own layer only; a stack resolved again
        /// keeps hidden exactly the layers of the same name hidden before.
        #[test]
        fn a_toggle_changes_one_layer_and_a_resolve_keeps_them_by_name(
            hidden in any::<u64>(),
            layer in 0usize..70,
            shown in any::<bool>(),
            before in arb_names(),
            after in arb_names(),
        ) {
            let set = set_shown(hidden, layer, shown);
            for i in 0..70 {
                if i == layer && i < 64 {
                    prop_assert_eq!(is_hidden(set, i), !shown);
                } else {
                    prop_assert_eq!(is_hidden(set, i), is_hidden(hidden, i));
                }
            }
            let names: Vec<&str> = after.iter().map(String::as_str).collect();
            let kept = kept_hidden(&before, hidden, &names);
            for (i, name) in names.iter().enumerate() {
                let was = before.iter().position(|b| b == name).is_some_and(|j| is_hidden(hidden, j));
                prop_assert_eq!(is_hidden(kept, i), was);
            }
            prop_assert_eq!(kept >> names.len(), 0, "nothing past its layers hidden");
        }
    }
}
