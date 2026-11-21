//! The overview's tree ([`Row`]s with children) as the visible rows the
//! window's tree widget is handed: each with its depth, a key that names
//! its place, and whether it is a branch and open.

use std::collections::HashSet;

use th_cleanup::overview::{Row, Tone};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Flat {
    /// The row's place: its index under each parent, `"0/2/1"`.
    pub key: String,
    pub depth: usize,
    pub cells: [String; 4],
    pub tone: Tone,
    pub tip: String,
    /// None for a leaf; whether a branch is open.
    pub open: Option<bool>,
}

/// The keys of every row that starts open.
pub fn initially_open(rows: &[Row]) -> HashSet<String> {
    let mut open = HashSet::new();
    fn walk(rows: &[Row], prefix: &str, open: &mut HashSet<String>) {
        for (index, row) in rows.iter().enumerate() {
            let key = if prefix.is_empty() { index.to_string() } else { format!("{prefix}/{index}") };
            if row.expanded && !row.children.is_empty() {
                open.insert(key.clone());
            }
            walk(&row.children, &key, open);
        }
    }
    walk(rows, "", &mut open);
    open
}

/// The rows showing: every top row, and the children of each open row.
pub fn visible(rows: &[Row], open: &HashSet<String>) -> Vec<Flat> {
    let mut out = Vec::new();
    fn walk(rows: &[Row], prefix: &str, depth: usize, open: &HashSet<String>, out: &mut Vec<Flat>) {
        for (index, row) in rows.iter().enumerate() {
            let key = if prefix.is_empty() { index.to_string() } else { format!("{prefix}/{index}") };
            let is_open = open.contains(&key);
            out.push(Flat {
                key: key.clone(),
                depth,
                cells: row.cells.clone(),
                tone: row.tone,
                tip: row.tip.clone(),
                open: (!row.children.is_empty()).then_some(is_open),
            });
            if is_open {
                walk(&row.children, &key, depth + 1, open, out);
            }
        }
    }
    walk(rows, "", 0, open, &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn rows(depth: u32) -> impl Strategy<Value = Vec<Row>> {
        let leaf = (any::<bool>(), "[a-z]{0,4}").prop_map(|(expanded, name)| Row {
            cells: [name, String::new(), String::new(), String::new()],
            tone: Tone::Normal,
            tip: String::new(),
            children: Vec::new(),
            expanded,
        });
        let node = leaf.prop_recursive(depth, 40, 4, |inner| {
            (any::<bool>(), "[a-z]{0,4}", proptest::collection::vec(inner, 0..4)).prop_map(|(expanded, name, children)| Row {
                cells: [name, String::new(), String::new(), String::new()],
                tone: Tone::Heading,
                tip: String::new(),
                children,
                expanded,
            })
        });
        proptest::collection::vec(node, 0..5)
    }

    fn count(rows: &[Row]) -> usize {
        rows.iter().map(|r| 1 + count(&r.children)).sum()
    }

    proptest! {
        /// Opening every branch shows every row, in order, each once and
        /// one level under its parent; nothing open shows only the top.
        #[test]
        fn opening_everything_shows_everything(rows in rows(3)) {
            let all: HashSet<String> = {
                let mut keys = HashSet::new();
                let mut stack = vec![(String::new(), &rows[..])];
                while let Some((prefix, level)) = stack.pop() {
                    for (i, row) in level.iter().enumerate() {
                        let key = if prefix.is_empty() { i.to_string() } else { format!("{prefix}/{i}") };
                        stack.push((key.clone(), &row.children[..]));
                        keys.insert(key);
                    }
                }
                keys
            };
            let shown = visible(&rows, &all);
            prop_assert_eq!(shown.len(), count(&rows));
            let keys: HashSet<&String> = shown.iter().map(|f| &f.key).collect();
            prop_assert_eq!(keys.len(), shown.len());
            for pair in shown.windows(2) {
                prop_assert!(pair[1].depth <= pair[0].depth + 1);
                if pair[1].depth == pair[0].depth + 1 {
                    prop_assert_eq!(pair[0].open, Some(true));
                }
            }
            prop_assert_eq!(visible(&rows, &HashSet::new()).len(), rows.len());
            // What starts open is only branches that say so.
            for key in initially_open(&rows) {
                prop_assert!(all.contains(&key));
            }
        }
    }
}
