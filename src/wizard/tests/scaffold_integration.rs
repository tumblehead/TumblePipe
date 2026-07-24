//! Integration test: scaffold a project from the *real* bundled
//! `scripts/project_template/` and check the result.
//!
//! When `TT_WIZARD_OUT` is set, the three patched config DBs are also copied
//! there so an external harness can diff them byte-for-byte against the Python
//! reference (see the sibling scratch comparison). Unset, the test still runs
//! its structural assertions against a tempdir.

use std::path::{Path, PathBuf};

fn template_dir() -> PathBuf {
    // src/wizard/ -> src/ -> repo root -> scripts/project_template
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("scripts")
        .join("project_template")
}

#[test]
fn scaffolds_the_real_template() {
    let template = template_dir();
    assert!(
        template.is_dir(),
        "real template missing at {}",
        template.display()
    );

    let tmp = tempfile::tempdir().unwrap();
    let target = tmp.path().join("testfilm");

    th_project_wizard::scaffold_project(&template, &target, "testfilm", 48)
        .expect("scaffold should succeed on the real template");

    // Top-level dirs created.
    for sub in th_project_wizard::TOP_LEVEL_DIRS {
        assert!(target.join(sub).is_dir(), "missing top-level dir {sub}");
    }

    // Template content copied.
    let db = target.join("_config").join("db");
    assert!(db.join("entity.json").is_file());
    assert!(db.join("config.json").is_file());
    assert!(db.join("schemas.json").is_file());

    // __pycache__ / *.pyc were filtered out of the copy.
    let mut pyc_found = false;
    for entry in walk(&target) {
        let name = entry.file_name().unwrap().to_string_lossy().into_owned();
        assert_ne!(name, "__pycache__", "__pycache__ should not be copied");
        if name.ends_with(".pyc") {
            pyc_found = true;
        }
    }
    assert!(!pyc_found, "*.pyc should not be copied");

    // Patched values landed, and files end with a single trailing newline.
    let entity = std::fs::read_to_string(db.join("entity.json")).unwrap();
    assert!(entity.contains("\"pools\": [\n                \"testfilm\"\n            ]"));
    assert!(entity.contains("\"default_pool\": \"testfilm\""));
    assert!(entity.ends_with("}\n") && !entity.ends_with("}\n\n"));

    let config = std::fs::read_to_string(db.join("config.json")).unwrap();
    assert!(config.contains("\"fps\": 48"));

    let schemas = std::fs::read_to_string(db.join("schemas.json")).unwrap();
    assert_eq!(schemas.matches("\"fps\": 48").count(), 2, "fps set in both places");
    // Untouched floats survive verbatim (arbitrary_precision).
    assert!(schemas.contains("0.0"));
    assert!(schemas.contains("0.5"));

    if let Some(out) = std::env::var_os("TT_WIZARD_OUT") {
        let out = PathBuf::from(out);
        std::fs::create_dir_all(&out).unwrap();
        for f in ["entity.json", "config.json", "schemas.json"] {
            std::fs::copy(db.join(f), out.join(f)).unwrap();
        }
    }
}

fn walk(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if let Ok(rd) = std::fs::read_dir(&dir) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    stack.push(p.clone());
                }
                out.push(p);
            }
        }
    }
    out
}
