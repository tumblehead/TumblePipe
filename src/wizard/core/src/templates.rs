//! v12 — what a project's department templates are, judged by text.
//!
//! A project copies `scripts/project_template/_config/templates/` at creation
//! and keeps that copy: "New workfile from template" runs the *project's*
//! `template.py`, not the package's. So when the package removes an API those
//! templates call, every project that has not refreshed them breaks on its
//! next new workfile. The 2026-10 refactor removed several at once
//! (`pipe.houdini.util`, `lops.animate`, `lops.render_vars`,
//! `sops.cache`, `set_shot_uri`, the create_asset `hdaModule()` helpers,
//! `th::puzzlemattes`).
//!
//! v12 refreshes a template only when it is *stock* — byte for byte (bar line
//! endings) a version the scaffold shipped, listed in [`STOCK`] — so a
//! studio's own template is never replaced. A customised one is left alone
//! unless it calls something in [`REMOVED_MODULES`] / [`REMOVED_CALLS`], and
//! then the step refuses: the template would fail either way, and replacing
//! it would throw away the studio's work, so the person who owns it decides.
//!
//! The checks read text and never run it, like [`crate::conventions`]: this
//! crate has no Python, and the point is to judge code that may not import.

/// Every `template.py` the scaffold has shipped, as (path under `templates/`,
/// [`fingerprint`]), with the git blob each came from. Collected from the
/// history of `scripts/project_template/_config/templates/` and its
/// predecessor `examples/config/templates/`; each one is a fixture under
/// `tests/fixtures/templates/`, and the tests fail when the packaged
/// templates change without a row here, because a project left on that
/// version would otherwise read as customised to the next refresh.
pub const STOCK: [(&str, u64); 81] = [
    ("assets/blendshape", 0x3e996f0cd9278155), // 886fc6b725
    ("assets/blendshape", 0x479bbf9dd255f96a), // 072f370dc7
    ("assets/blendshape", 0x6b8a6905dffbc0c1), // f631e117d8
    ("assets/blendshape", 0x740bf80497e647f2), // d5f2408363
    ("assets/blendshape", 0x9d62f20e3517d68b), // 536ee8ae88
    ("assets/blendshape", 0xb60e6ede03b40ab8), // b9f3d2b74a
    ("assets/blendshape", 0xe76b5826a13378a4), // 9be7a5aafe
    ("assets/blendshape", 0xcd06531206b5db8d), // ad76a88205
    ("assets/lookdev", 0x1b3f29bea006e0a5), // a52ecf9e17
    ("assets/lookdev", 0x5760ea8fa74b4ae6), // 0445abd52a
    ("assets/lookdev", 0x7d544393eea3ca05), // 89fb4b2835
    ("assets/lookdev", 0x966b4257d1819a86), // 203617e2b5
    ("assets/lookdev", 0xabdfa1baff88d653), // 199dedf97b
    ("assets/lookdev", 0xac8f53b1cff5a4c0), // 93fb8328c1
    ("assets/lookdev", 0xd75145e0ca70ff57), // 4e182c5f3e
    ("assets/model", 0x34d8dae8309f9b10), // f53431d1cc
    ("assets/model", 0x744cd703d57a3f75), // 4f5f9cf926
    ("assets/model", 0x8901f1f6a57ede7d), // 8c37af36a9
    ("assets/model", 0x8e1505ef83577a55), // ce7839d8fe
    ("assets/model", 0x9118fa8b63a5310e), // 3f88abe8f8
    ("assets/model", 0xc2c288d062ea8963), // c5d932fe8c
    ("assets/model", 0xd2811dba2a653955), // 77e29414e8
    ("assets/model", 0xf37711d269704509), // 82a0ed2d66
    ("assets/rig", 0x0a5da7df4d0e68f3), // e3c23e69af
    ("assets/rig", 0x12d69199fd4502a0), // 977efd09f4
    ("assets/rig", 0x3a1cb5d903c40527), // 802cbd64c8
    ("assets/rig", 0x582d833776ac44ee), // fd10172897
    ("assets/rig", 0x598bfb13fe9c67f0), // 77e1f113c0
    ("assets/rig", 0x86229cf9209def15), // 32b5911187
    ("assets/rig", 0xa3d1d379078aa532), // ecf4742ba1
    ("assets/rig", 0xb7ca50c09544e91e), // 8a42a5e842
    ("assets/rig", 0xcbdc5d35d52a6bb4), // ac08bc8141
    ("assets/rig", 0xe656c48d28c37e01), // ca34f0c471
    ("assets/rig", 0xf2911593e5966d02), // 50fc88e3c3
    ("shots/animation", 0x4f22585e6eed577c), // cb38ffa938
    ("shots/animation", 0x54003ebb35343d6b), // 865cc7cb4c
    ("shots/animation", 0x62d3572d1ccbe70c), // a57c0e9702
    ("shots/animation", 0x70c2edb7abca4908), // 9832b75ab1
    ("shots/animation", 0xb67174b916aaf246), // f2a640c76e
    ("shots/animation", 0xcdf69e8eb8fbf116), // 0aa683c4f1
    ("shots/animation", 0xd2797013b8ae247b), // 873b0c0315
    ("shots/animation", 0xa7f395d4ff858163), // 67282f6a0f
    ("shots/animation", 0x3fd0ad888044dc0a), // b023f4189b
    ("shots/animation", 0x6dae60d826ff0c26), // fe7802c38f
    ("shots/animation", 0xaff97601a315ed3b), // 90bed7d068
    ("shots/animation", 0x4075a1bbe020e68c), // 80def61306
    ("shots/cfx", 0x093be277eb7c4136), // a85f94fadc
    ("shots/cfx", 0x522c75d5c51911e3), // cc26004b79
    ("shots/cfx", 0x7054507e41b63ef9), // 8ae1bb6533
    ("shots/cfx", 0xa54bbc375cbc277d), // d5e0eb38cf
    ("shots/composite", 0x013fd61f3df16d38), // 4058bd9ccd
    ("shots/composite", 0x1c2bf4b91eed5cd8), // a1d0d7a925
    ("shots/composite", 0x223b8564747dd224), // aeff1a962e
    ("shots/composite", 0x4ddb6f31f1714475), // c47e24bc75
    ("shots/composite", 0xb2789ca14695a633), // 5a3e6ef417
    ("shots/crowd", 0x6179daa29cda4014), // 7367b2fb66
    ("shots/crowd", 0xa48d835191483bd7), // 9bfdf0b4b7
    ("shots/crowd", 0xfc0906730461684f), // 8cb06b64b7
    ("shots/crowd", 0xfeb667a023138b31), // 3f412f30a0
    ("shots/effects", 0x1d66313972dca460), // dda0c6c711
    ("shots/effects", 0x1e18e1cc5e040b43), // 4fb9d5c07c
    ("shots/effects", 0x694b6444464210cd), // 0502b7692c
    ("shots/effects", 0xca7144a1d45349af), // 4a84299b45
    ("shots/environment", 0x65a4ab9b587440b6), // 4d5108f9f5
    ("shots/environment", 0x889349d8b350e383), // 0bf9593376
    ("shots/environment", 0xa11ec97508c2af88), // 10c6322e61
    ("shots/environment", 0xacf4d3578c316251), // 5cffcb8b02
    ("shots/environment", 0xef11529ea7a8bd27), // 15814406da
    ("shots/layout", 0x1bb8e988a26aa63c), // cb72b1129e
    ("shots/layout", 0x1da682d6e161acdc), // 747683f395
    ("shots/layout", 0xf792f94ff804f1b9), // e5a3cf17a6
    ("shots/layout", 0xca18ad510e7f6dfe), // 1ac12ada67
    ("shots/light", 0x4675661a9c5030a7), // 7eea2924dc
    ("shots/light", 0x6bb0f682ced4e021), // 33202b26a6
    ("shots/light", 0xd1f2e0f072ade316), // 7438468120
    ("shots/render", 0x1970854ffcacc2ae), // b44fe56de2
    ("shots/render", 0x3252a8cd7f8ffe34), // a82352e8b0
    ("shots/render", 0x6f2fed992f3c2c70), // 572f2ad8d6
    ("shots/render", 0x75792c7f14f9ff8c), // 3faa1d4897
    ("shots/render", 0x8485d2684bb534e1), // ff8e5e9a7b
    ("shots/render", 0xededb9b93f029ac4), // c5404acd57
];

/// Modules a template may no longer import, with what replaced each. A name
/// matches the module itself and anything under it.
pub const REMOVED_MODULES: [(&str, &str); 12] = [
    ("tumblehead", "the package was renamed tumblepipe"),
    (
        "tumblepipe.pipe.houdini.util",
        "split into tumblepipe.pipe.houdini.nodes, .menus, .playbar, .prim_metadata \
         and .prim_queries",
    ),
    (
        "tumblepipe.pipe.houdini.sops.cache",
        "now tumblepipe.pipe.houdini.cache: cache.create(parent, name, cache.SopCache)",
    ),
    ("tumblepipe.pipe.houdini.lops.cache", "now tumblepipe.pipe.houdini.cache"),
    (
        "tumblepipe.pipe.houdini.lops.animate",
        "create the node with tumblepipe.pipe.houdini.nodes.create_node(parent, name, \
         nodes.Node, 'animate')",
    ),
    (
        "tumblepipe.pipe.houdini.lops.render_vars",
        "create the node with tumblepipe.pipe.houdini.nodes.create_node(parent, name, \
         nodes.Node, 'render_vars')",
    ),
    (
        "tumblepipe.pipe.houdini.lops.puzzle_mattes",
        "th::puzzlemattes is retired: use tumblepipe.pipe.houdini.lops.mattes",
    ),
    ("tumblepipe.config.variants", "now tumblepipe.config.channels (list_channels)"),
    ("tumblepipe.config.procedurals", "removed"),
    ("tumblepipe.pipe.usd", "removed"),
    ("tumblepipe.util.cache", "removed"),
    ("tumblepipe.pipe.houdini.tools", "removed"),
];

/// Calls a template may no longer make, as text, with what replaced each.
pub const REMOVED_CALLS: [(&str, &str); 8] = [
    (".parse_unsafe(", "Uri.parse(raw) (the only parser; it raises on a bad URI)"),
    (
        ".set_shot_uri(",
        "pipeline nodes are entity nodes now: set_entity_uri(uri)",
    ),
    (".set_asset_uri(", "set_entity_uri(uri)"),
    (
        "hdaModule()._apply_entity",
        "create_asset.wrap(node).set_entity_uri(uri) (tumblepipe.pipe.houdini.lops)",
    ),
    (
        "hdaModule()._sync_variants",
        "create_asset.wrap(node).sync_variants() (tumblepipe.pipe.houdini.lops)",
    ),
    (
        "hdaModule()._update_labels",
        "set the node's 'entity' parm; its callbacks refresh the labels",
    ),
    (
        "hdaModule().execute",
        "ImportModel(node).execute() (tumblepipe.pipe.houdini.sops.import_model)",
    ),
    ("th::puzzlemattes", "retired: place th::mattes (tumblepipe.pipe.houdini.lops.mattes)"),
];

/// FNV-1a over the text with CRLF folded to LF: a template on a shared drive
/// may have either, and that alone does not make it customised.
pub fn fingerprint(text: &str) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in text.replace("\r\n", "\n").bytes() {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0100_0000_01b3);
    }
    hash
}

/// Whether `text` is a version of the template at `relative` (its directory
/// under `templates/`, `/`-separated) that the scaffold shipped.
pub fn is_stock(relative: &str, text: &str) -> bool {
    let hash = fingerprint(text);
    STOCK
        .iter()
        .any(|(path, stock)| *path == relative && *stock == hash)
}

/// The text with each `#` comment cut off. A `#` inside a string is cut too,
/// which can only hide a call, never invent one.
fn code_lines(text: &str) -> Vec<&str> {
    text.lines()
        .map(|line| match line.find('#') {
            Some(at) => &line[..at],
            None => line,
        })
        .collect()
}

/// Every module a statement imports, and for `from M import a` also `M.a`
/// (which may be a submodule): enough to tell whether a removed module is
/// reached. Parenthesised and backslash-continued imports are joined first.
fn imported_names(text: &str) -> Vec<String> {
    let mut statements = Vec::new();
    let mut current = String::new();
    let mut depth: i32 = 0;
    for line in code_lines(text) {
        if current.is_empty() {
            let trimmed = line.trim_start();
            if !(trimmed.starts_with("from ") || trimmed.starts_with("import ")) {
                continue;
            }
        }
        let body = line.trim_end();
        let continued = body.ends_with('\\');
        current.push(' ');
        current.push_str(body.trim_end_matches('\\'));
        depth += body.matches('(').count() as i32 - body.matches(')').count() as i32;
        if depth <= 0 && !continued {
            statements.push(std::mem::take(&mut current));
            depth = 0;
        }
    }
    if !current.is_empty() {
        statements.push(current);
    }

    let mut names = Vec::new();
    for statement in statements {
        let cleaned = statement.replace(['(', ')'], " ");
        let statement = cleaned.trim();
        if let Some(rest) = statement.strip_prefix("from ") {
            let Some((module, imported)) = rest.split_once(" import ") else {
                continue;
            };
            let module = module.trim();
            names.push(module.to_string());
            for item in imported.split(',') {
                let name = item.split_whitespace().next().unwrap_or("");
                if !name.is_empty() && name != "*" {
                    names.push(format!("{module}.{name}"));
                }
            }
        } else if let Some(rest) = statement.strip_prefix("import ") {
            for item in rest.split(',') {
                if let Some(name) = item.split_whitespace().next() {
                    names.push(name.to_string());
                }
            }
        }
    }
    names
}

/// What in `text` the package no longer provides, one line each naming it and
/// its replacement. Empty when the template only uses what still exists.
pub fn removed_apis(text: &str) -> Vec<String> {
    let mut found = Vec::new();
    let names = imported_names(text);
    for (module, instead) in REMOVED_MODULES {
        let prefix = format!("{module}.");
        if names.iter().any(|n| n == module || n.starts_with(&prefix)) {
            found.push(format!("imports {module} ({instead})"));
        }
    }
    let code = code_lines(text).join("\n");
    for (call, instead) in REMOVED_CALLS {
        if code.contains(call) {
            found.push(format!("calls {} ({instead})", call.trim_end_matches('(')));
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_fingerprint_ignores_line_endings() {
        assert_eq!(fingerprint("a\nb\n"), fingerprint("a\r\nb\r\n"));
        assert_ne!(fingerprint("a\nb\n"), fingerprint("a\nb"));
    }

    #[test]
    fn stock_rows_are_unique() {
        for (i, a) in STOCK.iter().enumerate() {
            for b in &STOCK[i + 1..] {
                assert_ne!(a, b, "duplicate STOCK row");
            }
        }
    }

    #[test]
    fn every_import_form_is_seen() {
        let cases = [
            "from tumblepipe.pipe.houdini import util",
            "from tumblepipe.pipe.houdini.util import disable_layer_save_path",
            "import tumblepipe.pipe.houdini.util as u",
            "import hou, tumblepipe.pipe.houdini.util",
            "from tumblepipe.pipe.houdini import (\n    nodes,\n    util as x\n)",
            "def f():\n    from tumblepipe.pipe.houdini import \\\n        util\n",
        ];
        for text in cases {
            assert!(
                removed_apis(text)
                    .iter()
                    .any(|f| f.contains("tumblepipe.pipe.houdini.util")),
                "missed: {text}"
            );
        }
    }

    #[test]
    fn a_kept_module_with_a_removed_ones_prefix_is_not_flagged() {
        // tumblepipe.pipe.houdini.cache replaced sops.cache; `utils` is not `util`.
        let text = "from tumblepipe.pipe.houdini import cache\n\
                    from tumblepipe.pipe.houdini.nodes import disable_layer_save_path\n\
                    import tumblepipe.pipe.houdini.utils_of_mine\n";
        assert_eq!(removed_apis(text), Vec::<String>::new());
    }

    #[test]
    fn removed_lops_modules_are_flagged_from_a_multi_name_import() {
        let text = "from tumblepipe.pipe.houdini.lops import (\n    import_shot,\n    \
                    render_vars,\n    puzzle_mattes,\n    export_layer\n)\n";
        let found = removed_apis(text);
        assert_eq!(found.len(), 2, "{found:?}");
        assert!(found[0].contains("lops.render_vars"));
        assert!(found[1].contains("lops.puzzle_mattes"));
    }

    #[test]
    fn removed_calls_are_flagged_outside_comments_only() {
        let text = "node.set_shot_uri(uri)\n# node.hdaModule()._apply_entity(node, x)\n";
        let found = removed_apis(text);
        assert_eq!(found.len(), 1, "{found:?}");
        assert!(found[0].starts_with("calls .set_shot_uri"));
    }
}
