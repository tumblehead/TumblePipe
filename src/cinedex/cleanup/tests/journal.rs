//! Tests in other crates than th_pipe never write the user's change
//! journal: through th_pipe's `testing` feature, a config written here
//! announces to `th_pipe::events::TEST_JOURNAL`, and to nothing while no
//! test names one.

use th_pipe::events::TEST_JOURNAL;
use th_pipe::{Project, Uri};

#[test]
fn a_config_written_here_announces_only_to_the_test_journal() {
    let (dir, root) = th_pipe::testing::make_project();
    let project = Project::open(&root, &root).unwrap();
    let journal = dir.path().join("journal.jsonl");
    let sequence = Uri::parse("entity:/shots/S").unwrap();

    project.config.set_own_properties(&sequence, serde_json::Map::new()).unwrap();
    assert!(!journal.exists(), "announced with no test journal named");

    TEST_JOURNAL.with(|j| *j.borrow_mut() = Some(journal.clone()));
    let shot = Uri::parse("entity:/shots/S/010").unwrap();
    project.config.set_own_properties(&shot, serde_json::Map::new()).unwrap();
    TEST_JOURNAL.with(|j| *j.borrow_mut() = None);
    let written = std::fs::read_to_string(&journal).unwrap();
    let changes: Vec<serde_json::Value> = written.lines().skip(1).map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(changes.len(), 1, "{written}");
    assert_eq!((changes[0]["kind"].as_str(), changes[0]["detail"].as_str()), (Some("config"), Some("entity")), "{written}");

    project.config.set_own_properties(&sequence, serde_json::Map::new()).unwrap();
    assert_eq!(std::fs::read_to_string(&journal).unwrap(), written, "announced after the test journal was let go");
}
