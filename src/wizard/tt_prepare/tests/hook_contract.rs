//! The launch-hook contract, against the built binary: whatever the project,
//! stdout is exactly `{"envVars":{}}` and the exit status is 0, so the hook
//! is never the reason a launch fails. Problems go to stderr.

use std::process::{Command, Output};

const PAYLOAD: &str = "{\"envVars\":{}}\n";

/// The hook, non-interactive (no window), with `env` and nothing that names
/// a project unless `env` does.
fn hook(env: &[(&str, &std::ffi::OsStr)]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_tt_prepare"));
    command.env_remove("TH_PROJECT_PATH").env_remove("TT_PROJECT_DIR").env("TT_NONINTERACTIVE", "1");
    for (key, value) in env {
        command.env(key, value);
    }
    command.output().unwrap()
}

fn holds_the_contract(output: &Output) -> bool {
    output.status.code() == Some(0) && String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n") == PAYLOAD
}

#[test]
fn no_project_named_still_yields_the_payload() {
    let output = hook(&[]);
    assert!(holds_the_contract(&output), "{output:?}");
    assert!(!output.stderr.is_empty(), "the reason goes to stderr");
}

#[test]
fn a_missing_project_still_yields_the_payload() {
    let dir = tempfile::tempdir().unwrap();
    let missing = dir.path().join("nowhere");
    let output = hook(&[("TH_PROJECT_PATH", missing.as_os_str())]);
    assert!(holds_the_contract(&output), "{output:?}");
}

#[test]
fn an_unconfigured_project_still_yields_the_payload() {
    let dir = tempfile::tempdir().unwrap();
    let output = hook(&[("TH_PROJECT_PATH", dir.path().as_os_str())]);
    assert!(holds_the_contract(&output), "{output:?}");
    assert!(!output.stderr.is_empty(), "the reason goes to stderr");
}

#[test]
fn an_unreadable_version_file_still_yields_the_payload() {
    // A configured project whose version.json is not JSON: reading the
    // version fails, which must not fail the launch.
    let dir = tempfile::tempdir().unwrap();
    let cfg = dir.path().join("_config");
    std::fs::create_dir_all(cfg.join("db")).unwrap();
    std::fs::write(cfg.join("version.json"), "not json").unwrap();
    let output = hook(&[("TH_PROJECT_PATH", dir.path().as_os_str())]);
    assert!(holds_the_contract(&output), "{output:?}");
}
