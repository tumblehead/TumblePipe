//! Byte-parity check for the `_config` migrator against the Python original.
//!
//! The migrations rewrite JSON that lives on a shared project drive, so the
//! Rust port has to reproduce `json.dump(data, indent=4)` + `'\n'` exactly.
//! Anything else turns the first migration into a whole-file diff on every
//! project — noise that hides the change that actually matters.
//!
//! Driven by two env vars so it can run against a *real* project rather than a
//! synthesized one:
//!
//! ```text
//! TT_PARITY_INPUT     a db/config.json still labelled "Variants"
//! TT_PARITY_EXPECTED  the same file after the Python migrator relabelled it
//! ```
//!
//! Unset, the test still runs its own round-trip on a synthetic document, so
//! the formatting contract is pinned in ordinary CI.

use std::path::{Path, PathBuf};

use th_project_core::migration::{migrate, Step};

/// A minimal project around `config.json`, complete enough that every step is
/// Ready and the run reaches v5.
fn project_around(config_json: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let cfg = dir.path().join("_config");
    let db = cfg.join("db");
    std::fs::create_dir_all(&db).unwrap();
    std::fs::write(db.join("config.json"), config_json).unwrap();
    std::fs::write(
        db.join("schemas.json"),
        "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {}\n        }\n    }\n}\n",
    )
    .unwrap();
    dir
}

fn scaffold() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(dir.path().join("_config").join("templates")).unwrap();
    std::fs::create_dir_all(dir.path().join("_config").join("ocio")).unwrap();
    // Every step has to be Ready for the run to reach the relabel, and v6
    // blocks when the packaged storage convention is missing.
    std::fs::write(
        dir.path().join("_config").join("storage_convention.py"),
        "class StorageConvention:
    pass
",
    )
    .unwrap();
    dir
}

fn run_relabel(config_json: &str) -> String {
    let project = project_around(config_json);
    let template = scaffold();
    let report = migrate(project.path(), template.path());
    assert!(report.is_ok(), "migration failed: {report:?}");
    assert!(
        report.applied.iter().any(|(s, _)| *s == Step::RelabelVariantsAsChannels),
        "the relabel step did not run"
    );
    std::fs::read_to_string(project.path().join("_config").join("db").join("config.json")).unwrap()
}

/// Formatting contract: 4-space indent, key order preserved, numbers verbatim,
/// exactly one trailing newline — the shape Python's json.dump produces.
#[test]
fn relabelled_output_keeps_pythons_formatting() {
    let input = concat!(
        "{\n",
        "    \"columns\": [\n",
        "        {\n",
        "            \"key\": \"variants\",\n",
        "            \"label\": \"Variants\",\n",
        "            \"type\": \"multi_select\",\n",
        "            \"property_path\": \"variants\",\n",
        "            \"per_entity_choices\": true,\n",
        "            \"width\": 100,\n",
        "            \"tooltip\": \"Variants to render\"\n",
        "        }\n",
        "    ]\n",
        "}\n",
    );
    let expected = input
        .replace("\"label\": \"Variants\"", "\"label\": \"Channels\"")
        .replace("\"Variants to render\"", "\"Channels to render\"");

    let actual = run_relabel(input);
    assert_eq!(
        actual, expected,
        "relabel must touch only the label and tooltip, byte for byte"
    );
}

/// Against a real project: the Rust migrator must land on exactly the bytes the
/// Python migrator already produced on the live share.
#[test]
fn matches_the_python_migrator_on_a_real_project() {
    let (Ok(input_path), Ok(expected_path)) = (
        std::env::var("TT_PARITY_INPUT"),
        std::env::var("TT_PARITY_EXPECTED"),
    ) else {
        eprintln!("TT_PARITY_INPUT / TT_PARITY_EXPECTED unset — skipping the real-project check");
        return;
    };

    let input = std::fs::read_to_string(PathBuf::from(&input_path)).unwrap();
    let expected = std::fs::read_to_string(Path::new(&expected_path)).unwrap();

    let actual = run_relabel(&input);
    // Dump for an external diff when the assertion below is the thing failing.
    if let Ok(out) = std::env::var("TT_PARITY_ACTUAL_OUT") {
        std::fs::write(out, &actual).unwrap();
    }
    assert_eq!(
        actual, expected,
        "Rust output differs from what Python wrote to the live project"
    );
}
