//! Which workspace paths the indexer skips.
//!
//! A path is skipped when a `.gitignore` on its way from the workspace root ignores it
//! (deeper files override shallower ones; `!` re-includes), when `.git/info/exclude` does,
//! or when a component of its path RELATIVE to the root is on a fixed fallback list (for
//! workspaces without a `.gitignore`). Matching relative to the root matters: the old
//! name check ran over the absolute path, so a workspace under any folder named `build`
//! or `.tmp` indexed nothing.

use ignore::gitignore::{Gitignore, GitignoreBuilder};
use ignore::Match;
use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

const FALLBACK_IGNORED_DIRS: [&str; 14] = [
    ".git",
    "node_modules",
    ".venv",
    "target",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".agentd",
    ".ai-editor",
    ".crucible",
    ".tmp",
];

pub struct IgnoreRules {
    /// The root as configured and canonicalized: walked paths start from the former,
    /// filesystem events (FSEvents) report the latter (`/private/var` vs `/var`).
    roots: Vec<PathBuf>,
    /// Parsed `.gitignore` per directory; `None` when the directory has none.
    per_dir: HashMap<PathBuf, Option<Gitignore>>,
    exclude: Option<Gitignore>,
}

impl IgnoreRules {
    pub fn new(root: &Path) -> Self {
        let mut roots = vec![root.to_path_buf()];
        if let Ok(canonical) = root.canonicalize() {
            if canonical != root {
                roots.push(canonical);
            }
        }
        let exclude_path = root.join(".git/info/exclude");
        let exclude = exclude_path
            .is_file()
            .then(|| {
                let mut builder = GitignoreBuilder::new(root);
                builder.add(&exclude_path);
                builder.build().ok()
            })
            .flatten();
        Self {
            roots,
            per_dir: HashMap::new(),
            exclude,
        }
    }

    /// Forget a directory's parsed `.gitignore` after the file changed.
    pub fn invalidate(&mut self, changed: &Path) {
        if changed.file_name().is_some_and(|name| name == ".gitignore") {
            if let Some(dir) = changed.parent() {
                self.per_dir.remove(dir);
            }
        }
    }

    pub fn is_ignored(&mut self, path: &Path, is_dir: bool) -> bool {
        let Some((root, relative)) = self.split(path) else {
            // Outside the workspace: only the fixed names can say anything.
            return path.components().any(is_fallback_ignored);
        };
        if relative.components().any(is_fallback_ignored) {
            return true;
        }
        // Directories that may hold a .gitignore for this path: the root and every parent
        // below it. The deepest one with an opinion wins, as in git.
        let mut dirs = vec![root.clone()];
        if let Some(parent) = relative.parent() {
            let mut dir = root.clone();
            for component in parent.components() {
                dir.push(component);
                dirs.push(dir.clone());
            }
        }
        let full = root.join(&relative);
        for dir in dirs.iter().rev() {
            if let Some(gitignore) = self.gitignore_for(dir) {
                match gitignore.matched_path_or_any_parents(&full, is_dir) {
                    Match::Ignore(_) => return true,
                    Match::Whitelist(_) => return false,
                    Match::None => {}
                }
            }
        }
        self.exclude.as_ref().is_some_and(|exclude| {
            exclude
                .matched_path_or_any_parents(&full, is_dir)
                .is_ignore()
        })
    }

    fn split(&self, path: &Path) -> Option<(PathBuf, PathBuf)> {
        self.roots.iter().find_map(|root| {
            path.strip_prefix(root)
                .ok()
                .map(|relative| (root.clone(), relative.to_path_buf()))
        })
    }

    fn gitignore_for(&mut self, dir: &Path) -> Option<&Gitignore> {
        self.per_dir
            .entry(dir.to_path_buf())
            .or_insert_with(|| {
                let file = dir.join(".gitignore");
                file.is_file().then(|| Gitignore::new(&file).0)
            })
            .as_ref()
    }
}

fn is_fallback_ignored(component: Component<'_>) -> bool {
    let name = component.as_os_str().to_string_lossy();
    FALLBACK_IGNORED_DIRS
        .iter()
        .any(|candidate| *candidate == name)
}
