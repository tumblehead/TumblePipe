//! Integration tests: feed URIs through the public `resolve_uri` entry
//! point against a tempdir laid out like a production `$TH_EXPORT_PATH`.

use std::fs;
use std::path::Path;
use std::sync::Mutex;

use th_resolver_core::env::{EXPORT_PATH_VAR, LATEST_MODE_VAR};
use th_resolver_core::resolve_uri;

// Env vars are process-wide; serialize tests that touch them.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct EnvGuard {
    _lock: std::sync::MutexGuard<'static, ()>,
    prior_export: Option<String>,
    prior_latest: Option<String>,
}

impl EnvGuard {
    fn new(export: &Path, latest: bool) -> Self {
        let lock = ENV_LOCK.lock().unwrap();
        let prior_export = std::env::var(EXPORT_PATH_VAR).ok();
        let prior_latest = std::env::var(LATEST_MODE_VAR).ok();
        std::env::set_var(EXPORT_PATH_VAR, export);
        if latest {
            std::env::set_var(LATEST_MODE_VAR, "1");
        } else {
            std::env::remove_var(LATEST_MODE_VAR);
        }
        EnvGuard {
            _lock: lock,
            prior_export,
            prior_latest,
        }
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        match &self.prior_export {
            Some(v) => std::env::set_var(EXPORT_PATH_VAR, v),
            None => std::env::remove_var(EXPORT_PATH_VAR),
        }
        match &self.prior_latest {
            Some(v) => std::env::set_var(LATEST_MODE_VAR, v),
            None => std::env::remove_var(LATEST_MODE_VAR),
        }
    }
}

fn make_tree(root: &Path, dirs: &[&str], files: &[&str]) {
    for d in dirs {
        fs::create_dir_all(root.join(d)).unwrap();
    }
    for f in files {
        let p = root.join(f);
        if let Some(parent) = p.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(p, b"").unwrap();
    }
}

#[test]
fn department_explicit_version() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(
        base,
        &["assets/SET/Arena/default/lookdev/v0013"],
        &["assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd"],
    );

    let _g = EnvGuard::new(base, false);
    let resolved = resolve_uri(
        "entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013",
    )
    .unwrap();
    assert!(resolved.ends_with(
        "assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd"
    ), "got {resolved}");
}

#[test]
fn department_shared_variant_uses_shared_filename() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(
        base,
        &["assets/SET/Arena/_shared/lookdev/v0005"],
        &[],
    );

    let _g = EnvGuard::new(base, false);
    let resolved = resolve_uri(
        "entity:/assets/SET/Arena?dept=lookdev&variant=_shared&version=v0005",
    )
    .unwrap();
    assert!(resolved.ends_with(
        "assets/SET/Arena/_shared/lookdev/v0005/assets_SET_Arena_shared_lookdev_v0005.usd"
    ), "got {resolved}");
}

#[test]
fn root_department_uses_root_layout() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(base, &["shots/sq050/sh446/_root/v0006"], &[]);

    let _g = EnvGuard::new(base, false);
    let resolved =
        resolve_uri("entity:/shots/sq050/sh446?dept=root&version=v0006").unwrap();
    assert!(resolved.ends_with(
        "shots/sq050/sh446/_root/v0006/shots_sq050_sh446_root_v0006.usda"
    ), "got {resolved}");
}

#[test]
fn staged_drops_first_segment_in_filename() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(base, &["assets/CHAR/Crowd/_staged/cheering/v0002"], &[]);

    let _g = EnvGuard::new(base, false);
    let resolved =
        resolve_uri("entity:/assets/CHAR/Crowd?variant=cheering&version=v0002").unwrap();
    assert!(resolved.ends_with(
        "assets/CHAR/Crowd/_staged/cheering/v0002/CHAR_Crowd_v0002.usda"
    ), "got {resolved}");
}

#[test]
fn scene_resolves_with_explicit_version() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(base, &["scenes/arena/_staged/v0001"], &[]);

    let _g = EnvGuard::new(base, false);
    let resolved = resolve_uri("entity:/scenes/arena?version=v0001").unwrap();
    assert!(resolved.ends_with("scenes/arena/_staged/v0001/arena_v0001.usda"), "got {resolved}");
}

#[test]
fn latest_version_auto_discovery() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(
        base,
        &[
            "assets/PROP/Box/default/model/v0001",
            "assets/PROP/Box/default/model/v0002",
            "assets/PROP/Box/default/model/v0009",
        ],
        &[],
    );

    let _g = EnvGuard::new(base, false);
    let resolved =
        resolve_uri("entity:/assets/PROP/Box?dept=model&variant=default").unwrap();
    assert!(resolved.contains("/v0009/"), "got {resolved}");
}

#[test]
fn latest_mode_overrides_explicit_version() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    make_tree(
        base,
        &[
            "assets/PROP/Box/default/model/v0001",
            "assets/PROP/Box/default/model/v0009",
        ],
        &[],
    );

    let _g = EnvGuard::new(base, true);
    let resolved =
        resolve_uri("entity:/assets/PROP/Box?dept=model&variant=default&version=v0001")
            .unwrap();
    assert!(resolved.contains("/v0009/"), "got {resolved}");
}

#[test]
fn missing_export_path_is_error() {
    // Lock and explicitly clear to be deterministic across reorderings.
    let _lock = ENV_LOCK.lock().unwrap();
    let prior = std::env::var(EXPORT_PATH_VAR).ok();
    std::env::remove_var(EXPORT_PATH_VAR);

    let result = resolve_uri("entity:/assets/PROP/Box?dept=model");
    assert!(result.is_err());

    if let Some(v) = prior {
        std::env::set_var(EXPORT_PATH_VAR, v);
    }
}

#[test]
fn no_versions_at_path_is_error() {
    let td = tempfile::tempdir().unwrap();
    let base = td.path();
    let _g = EnvGuard::new(base, false);
    let result = resolve_uri("entity:/assets/NONE/Thing?dept=model&variant=default");
    assert!(result.is_err());
}

#[test]
fn non_entity_scheme_is_parse_error() {
    let td = tempfile::tempdir().unwrap();
    let _g = EnvGuard::new(td.path(), false);
    assert!(resolve_uri("file:///tmp/x").is_err());
}
