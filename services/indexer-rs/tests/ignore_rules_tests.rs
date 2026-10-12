//! The indexer skips what the project itself ignores (.gitignore files on the path and
//! .git/info/exclude), plus a fixed fallback list matched relative to the workspace root.
//!
//! Live (2026-10-12): the snapshot indexed gitignored folders (aider-references/,
//! .superpowers/proto/, 243 tmp-review-files-* test leftovers), whose outlines then filled
//! every chat's retrieval seed. And the old name-based check matched folder names anywhere in
//! the ABSOLUTE path, so a workspace under any folder named `build` indexed nothing.

use crucible_indexer::ignore_rules::IgnoreRules;
use crucible_indexer::service::IndexSnapshot;
use std::path::{Path, PathBuf};
use std::process::Command;

fn workspace(parent_dirs: &[&str]) -> (tempfile::TempDir, PathBuf) {
    let tmp = tempfile::tempdir().expect("tempdir");
    let mut root = tmp.path().canonicalize().expect("canonical tempdir");
    for dir in parent_dirs {
        root = root.join(dir);
    }
    std::fs::create_dir_all(&root).expect("create workspace");
    (tmp, root)
}

fn write(root: &Path, rel: &str, text: &str) -> PathBuf {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).expect("create parent");
    std::fs::write(&path, text).expect("write file");
    path
}

#[test]
fn gitignored_dirs_globs_and_files_are_ignored() {
    let (_tmp, root) = workspace(&[]);
    write(
        &root,
        ".gitignore",
        "gen/\ntmp-review-files-*/\nsrc/generated_*.py\n!src/generated_keep.py\n",
    );
    let mut rules = IgnoreRules::new(&root);

    assert!(rules.is_ignored(&root.join("gen"), true));
    assert!(rules.is_ignored(&root.join("gen/a.py"), false));
    assert!(rules.is_ignored(&root.join("tmp-review-files-abc"), true));
    assert!(rules.is_ignored(&root.join("tmp-review-files-abc/src/a.ts"), false));
    assert!(rules.is_ignored(&root.join("src/generated_x.py"), false));
    assert!(!rules.is_ignored(&root.join("src/generated_keep.py"), false));
    assert!(!rules.is_ignored(&root.join("src/app.py"), false));
}

#[test]
fn a_nested_gitignore_applies_below_its_own_directory() {
    let (_tmp, root) = workspace(&[]);
    write(&root, "pkg/.gitignore", "local/\n");
    let mut rules = IgnoreRules::new(&root);

    assert!(rules.is_ignored(&root.join("pkg/local/x.py"), false));
    assert!(!rules.is_ignored(&root.join("other/local/x.py"), false));
}

#[test]
fn git_info_exclude_is_honoured() {
    let (_tmp, root) = workspace(&[]);
    write(&root, ".git/info/exclude", "scratch/\n");
    let mut rules = IgnoreRules::new(&root);

    assert!(rules.is_ignored(&root.join("scratch/x.py"), false));
}

#[test]
fn the_fixed_list_still_applies_without_a_gitignore() {
    let (_tmp, root) = workspace(&[]);
    let mut rules = IgnoreRules::new(&root);

    assert!(rules.is_ignored(&root.join("node_modules/pkg/index.js"), false));
    assert!(rules.is_ignored(&root.join(".crucible/index-snapshot.json"), false));
    assert!(!rules.is_ignored(&root.join("src/app.py"), false));
}

#[test]
fn a_workspace_under_a_folder_named_build_is_still_indexed() {
    let (_tmp, root) = workspace(&["build", "project"]);
    let mut rules = IgnoreRules::new(&root);

    assert!(!rules.is_ignored(&root.join("src/app.py"), false));
    assert!(rules.is_ignored(&root.join("build/out.py"), false)); // inside the workspace: still skipped
}

#[test]
fn an_edited_gitignore_takes_effect_after_invalidation() {
    let (_tmp, root) = workspace(&[]);
    let gitignore = write(&root, ".gitignore", "");
    let mut rules = IgnoreRules::new(&root);
    assert!(!rules.is_ignored(&root.join("gen/a.py"), false));

    std::fs::write(&gitignore, "gen/\n").expect("edit .gitignore");
    rules.invalidate(&gitignore);

    assert!(rules.is_ignored(&root.join("gen/a.py"), false));
}

#[test]
fn the_snapshot_leaves_out_gitignored_sources() {
    let (_tmp, root) = workspace(&["build", "project"]);
    write(
        &root,
        ".gitignore",
        "aider-references/\nsrc/generated_*.py\n",
    );
    write(&root, "src/app.py", "def kept():\n    pass\n");
    write(&root, "src/generated_x.py", "def generated():\n    pass\n");
    write(
        &root,
        "aider-references/coder.py",
        "def dropped():\n    pass\n",
    );
    let snapshot_path = root.join(".crucible/index-snapshot.json");

    let status = Command::new(env!("CARGO_BIN_EXE_crucible-indexer"))
        .args(["index", "--workspace"])
        .arg(&root)
        .arg("--snapshot-path")
        .arg(&snapshot_path)
        .args(["--watch", "0"])
        .env("CRUCIBLE_LSP_ENABLED", "0")
        .status()
        .expect("run indexer");
    assert!(status.success());

    let snapshot: IndexSnapshot =
        serde_json::from_str(&std::fs::read_to_string(&snapshot_path).expect("read snapshot"))
            .expect("decode snapshot");
    let names: Vec<String> = snapshot
        .graph
        .nodes
        .iter()
        .map(|n| n.name.clone())
        .collect();
    assert!(
        names.iter().any(|n| n == "kept"),
        "src/app.py should be indexed: {names:?}"
    );
    assert!(
        !names.iter().any(|n| n == "dropped"),
        "gitignored file was indexed: {names:?}"
    );
    assert!(
        !names.iter().any(|n| n == "generated"),
        "gitignored single file was indexed: {names:?}"
    );
}
