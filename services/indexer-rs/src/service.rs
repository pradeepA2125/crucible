use anyhow::Result;
use notify::{Event, EventKind, RecursiveMode, Watcher};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::mpsc as std_mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::config::IndexerConfig;
use crate::graph::{SymbolEdge, SymbolGraph, SymbolNode};
use crate::ignore_rules::IgnoreRules;
use crate::lsp::{LspAdapter, LspDiagnostic};
use crate::parser::{LanguageParser, TreeSitterParser};

const WATCH_DEBOUNCE_MS: u128 = 150;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphSnapshot {
    pub nodes: Vec<SymbolNode>,
    pub edges: Vec<SymbolEdge>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SnapshotStats {
    pub node_count: usize,
    pub edge_count: usize,
    pub diagnostic_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexSnapshot {
    pub schema_version: u32,
    pub workspace_root: String,
    pub generated_at_ms: u128,
    pub graph: GraphSnapshot,
    pub diagnostics: Vec<LspDiagnostic>,
    pub stats: SnapshotStats,
}

pub struct IndexerService {
    config: IndexerConfig,
    graph: SymbolGraph,
    parser: Box<dyn LanguageParser>,
    lsp: LspAdapter,
    tracked_files: HashSet<PathBuf>,
    canonical_workspace_root: PathBuf,
    ignore_rules: IgnoreRules,
    diagnostics_snapshot: Vec<LspDiagnostic>,
    index_warnings: Vec<LspDiagnostic>,
    last_watch_event: HashMap<PathBuf, Instant>,
    notifier: Option<std::sync::Arc<crate::backend_auth::BackendNotifier>>,
}

impl IndexerService {
    pub fn new(config: IndexerConfig) -> Result<Self> {
        let lsp = LspAdapter::new(&config);
        let workspace_root = config.workspace_root.clone();
        let canonical_workspace_root = workspace_root.canonicalize()
            .unwrap_or_else(|_| workspace_root.clone());
        #[allow(deprecated)] // home_dir is correct on Windows from Rust 1.86; toolchain is 1.93
        let notifier = match (&config.backend_url, std::env::home_dir()) {
            (Some(_), Some(home)) => Some(std::sync::Arc::new(
                crate::backend_auth::BackendNotifier::new(home)?)),
            _ => None,
        };
        Ok(Self {
            config,
            graph: SymbolGraph::default(),
            parser: Box::new(TreeSitterParser::new(workspace_root.clone())),
            lsp,
            tracked_files: HashSet::new(),
            canonical_workspace_root,
            ignore_rules: IgnoreRules::new(&workspace_root),
            diagnostics_snapshot: Vec::new(),
            index_warnings: Vec::new(),
            last_watch_event: HashMap::new(),
            notifier,
        })
    }

    pub async fn bootstrap(&mut self) -> Result<()> {
        self.tracked_files.clear();
        self.index_warnings.clear();

        let root = self.config.workspace_root.clone();
        self.index_path(&root).await?;

        if self.config.lsp_enabled {
            let files: Vec<PathBuf> = self.tracked_files.iter().cloned().collect();
            self.lsp.open_workspace_files(&files).await?;
            // Wait for the servers to finish initial indexing BEFORE resolving.
            // Without this the resolver races a cold rust-analyzer/pyright and
            // gets nulls/timeouts for thousands of call sites, leaving the
            // bootstrap snapshot under-resolved until files change. Budget the
            // wait off the LSP startup timeout (rust-analyzer cache-priming on a
            // large workspace dominates); a server that reports no progress for
            // `quiet_window` is assumed already-warm and skipped.
            let deadline = Instant::now()
                + Duration::from_millis(self.config.lsp_startup_timeout_ms);
            self.lsp
                .wait_for_indexing(deadline, Duration::from_secs(10));
            // Run the LSP-backed Calls/Implements/Inherits resolver now that
            // the servers are warm and answer definition/implementation fast.
            crate::resolver::resolve_placeholders(&mut self.graph, &mut self.lsp);
            self.refresh_diagnostics().await?;
        } else {
            // Drop placeholders we collected during parse — LSP isn't going to
            // help us resolve them, and keeping them around would leak memory.
            let _ = self.graph.take_placeholders();
        }

        self.persist_snapshot().await?;
        Ok(())
    }

    pub async fn run(&mut self) -> Result<()> {
        if self.config.watch_enabled {
            tracing::info!(
                workspace = %self.config.workspace_root.display(),
                workers = self.config.max_parse_workers,
                parser = self.parser.language_name(),
                lsp_enabled = self.config.lsp_enabled,
                "indexer watch loop started"
            );
            self.watch_loop().await?;
        }

        self.refresh_diagnostics().await?;
        self.persist_snapshot().await?;

        tracing::info!(
            node_count = self.graph.node_count(),
            edge_count = self.graph.edge_count(),
            diagnostic_count = self.diagnostics_snapshot.len(),
            "index graph snapshot"
        );

        Ok(())
    }

    pub fn get_diagnostics_snapshot(&self) -> Vec<LspDiagnostic> {
        let mut diagnostics = self.diagnostics_snapshot.clone();
        diagnostics.extend(self.index_warnings.clone());
        diagnostics.sort_by(|a, b| {
            a.file
                .cmp(&b.file)
                .then(a.line.cmp(&b.line))
                .then(a.column.cmp(&b.column))
                .then(a.message.cmp(&b.message))
        });
        diagnostics
    }

    pub fn get_graph_snapshot(&self) -> GraphSnapshot {
        GraphSnapshot {
            nodes: self.graph.all_nodes(),
            edges: self.graph.all_edges(),
        }
    }

    pub fn get_index_snapshot(&self) -> IndexSnapshot {
        let graph = self.get_graph_snapshot();
        let diagnostics = self.get_diagnostics_snapshot();
        IndexSnapshot {
            schema_version: 1,
            workspace_root: self.config.workspace_root.display().to_string(),
            generated_at_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|value| value.as_millis())
                .unwrap_or(0),
            graph: graph.clone(),
            diagnostics: diagnostics.clone(),
            stats: SnapshotStats {
                node_count: graph.nodes.len(),
                edge_count: graph.edges.len(),
                diagnostic_count: diagnostics.len(),
            },
        }
    }

    async fn watch_loop(&mut self) -> Result<()> {
        // The notify FSEvents backend on macOS delivers events on its own CF runloop
        // thread.  The callback posts to a std::sync::mpsc; a spawn_blocking thread
        // bridges that to a tokio channel so the async loop can .await events without
        // blocking a tokio worker.  The watcher MUST stay alive in this scope — moving
        // it into the spawn_blocking closure would drop std_tx (inside the watcher)
        // when the closure exits, immediately closing the std channel.
        let (std_tx, std_rx) = std_mpsc::channel::<notify::Result<Event>>();
        let mut watcher =
            notify::recommended_watcher(move |res| { let _ = std_tx.send(res); })?;
        watcher.watch(&self.config.workspace_root, RecursiveMode::Recursive)?;

        let (async_tx, mut async_rx) = tokio::sync::mpsc::channel::<notify::Result<Event>>(256);
        // Bridge: dedicated OS thread pumps the std channel → async channel.
        tokio::task::spawn_blocking(move || {
            while let Ok(event) = std_rx.recv() {
                if async_tx.blocking_send(event).is_err() {
                    break;
                }
            }
        });

        loop {
            let event_result = match async_rx.recv().await {
                Some(result) => result,
                None => break,
            };

            match event_result {
                Ok(event) => self.handle_watch_event(event).await?,
                Err(error) => {
                    tracing::warn!(error = %error, "filesystem watch event error");
                }
            }
        }

        // watcher drops here, after the loop exits (async_rx closed).
        drop(watcher);
        Ok(())
    }

    async fn handle_watch_event(&mut self, event: Event) -> Result<()> {
        // Track whether the event actually moved the graph forward. If every
        // path was filtered (ignored dir like `.crucible`, unsupported
        // extension, or per-path debounce skip), persist_snapshot must NOT run
        // — otherwise our own snapshot write under `.crucible` would re-trigger
        // a watch event, get filtered, and still rewrite the snapshot, looping
        // forever and flooding the backend with `/v1/index/build` POSTs.
        let mut processed_any = false;
        for path in event.paths {
            // FSEvents reports canonical paths even when bootstrap walked an
            // alias (/var vs /private/var, or a symlinked workspace). Use the
            // bootstrap namespace for identity, including already-deleted paths.
            let path = match path.strip_prefix(&self.canonical_workspace_root) {
                Ok(relative) => self.config.workspace_root.join(relative),
                Err(_) => path,
            };
            // An edited .gitignore changes what is ignored from here on.
            self.ignore_rules.invalidate(&path);
            // Deletion and the old side of a rename must bypass both extension
            // filtering (directory removals) and debounce (rapid save/delete).
            if matches!(event.kind, EventKind::Remove(_)) || !path.exists() {
                // Include imported stubs and failed-bootstrap warnings: those
                // files need cleanup even when they were never tracked as parsed.
                let removed: HashSet<PathBuf> = self
                    .tracked_files
                    .iter()
                    .cloned()
                    .chain(self.graph.file_paths().map(PathBuf::from))
                    .chain(self.index_warnings.iter().map(|w| PathBuf::from(&w.file)))
                    .filter(|file| file.starts_with(&path))
                    .collect();
                for file in removed {
                    self.tracked_files.remove(&file);
                    self.graph.remove_file(&file);
                    self.index_warnings
                        .retain(|warning| warning.file != file.display().to_string());
                    self.last_watch_event.remove(&file);
                    self.lsp.close_file(&file).await?;
                    processed_any = true;
                }
                continue;
            }
            if !is_supported_source_path(&path) || self.ignore_rules.is_ignored(&path, false) {
                continue;
            }

            let now = Instant::now();
            if let Some(previous) = self.last_watch_event.get(&path) {
                if now.duration_since(*previous).as_millis() < WATCH_DEBOUNCE_MS {
                    continue;
                }
            }
            self.last_watch_event.insert(path.clone(), now);

            match event.kind {
                EventKind::Modify(_) | EventKind::Create(_) | EventKind::Any => {
                    if !path.exists() || !path.is_file() {
                        continue;
                    }

                    match std::fs::read_to_string(&path) {
                        Ok(source) => {
                            // The parser stages a whole-file replacement: failed
                            // parses preserve last-good nodes AND resolved edges.
                            if let Err(error) = self.parser.parse_file(&path, &source, &mut self.graph) {
                                self.push_index_warning(
                                    &path,
                                    format!("parser failed for changed file: {error}"),
                                );
                                processed_any = true; // publish the degradation warning
                                continue;
                            }
                            self.index_warnings
                                .retain(|warning| warning.file != path.display().to_string());
                            self.tracked_files.insert(path.clone());
                            self.lsp.upsert_file(&path, source).await?;
                            if self.config.lsp_enabled {
                                crate::resolver::resolve_placeholders_for_file(
                                    &mut self.graph,
                                    &mut self.lsp,
                                    &path,
                                );
                            } else {
                                let _ = self.graph.take_placeholders_for_file(&path);
                            }
                            processed_any = true;
                        }
                        Err(error) => {
                            self.push_index_warning(
                                &path,
                                format!("unable to process changed file: {error}"),
                            );
                            tracing::warn!(
                                path = %path.display(),
                                error = %error,
                                "unable to process changed file"
                            );
                        }
                    }
                }
                _ => {}
            }
        }

        if processed_any {
            self.refresh_diagnostics().await?;
            self.persist_snapshot().await?;
        }
        Ok(())
    }

    async fn refresh_diagnostics(&mut self) -> Result<()> {
        self.diagnostics_snapshot = self
            .lsp
            .collect_diagnostics(&self.config.workspace_root)
            .await?;
        Ok(())
    }

    async fn persist_snapshot(&self) -> Result<()> {
        let snapshot = self.get_index_snapshot();
        if let Some(parent) = self.config.snapshot_output_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let payload = serde_json::to_vec_pretty(&snapshot)?;
        // Atomic write: serialize to a temp sibling then rename, so a reader (the backend's
        // trigger_index_build) never observes a half-written snapshot (torn read).
        let tmp_path = self.config.snapshot_output_path.with_extension("json.tmp");
        tokio::fs::write(&tmp_path, payload).await?;
        tokio::fs::rename(&tmp_path, &self.config.snapshot_output_path).await?;

        if let (Some(notifier), Some(backend_url)) = (&self.notifier, &self.config.backend_url) {
            let notifier = notifier.clone();
            let backend_url = backend_url.clone();
            let workspace = self.config.workspace_root.display().to_string();
            tokio::spawn(async move {
                notifier.notify_index_build(&backend_url, &workspace).await;
            });
        }

        Ok(())
    }

    async fn index_path(&mut self, path: &Path) -> Result<()> {
        self.index_path_inner(path)
    }

    fn index_path_inner(&mut self, path: &Path) -> Result<()> {
        if self.ignore_rules.is_ignored(path, path.is_dir()) {
            return Ok(());
        }

        if path.is_file() {
            return self.index_file(path);
        }

        for entry in std::fs::read_dir(path)? {
            let entry = entry?;
            let child = entry.path();
            if child.is_dir() {
                self.index_path_inner(&child)?;
                continue;
            }
            if self.ignore_rules.is_ignored(&child, false) {
                continue;
            }
            self.index_file(&child)?;
        }

        Ok(())
    }

    fn index_file(&mut self, file_path: &Path) -> Result<()> {
        if !is_supported_source_path(file_path) {
            return Ok(());
        }

        match std::fs::read_to_string(file_path) {
            Ok(source) => {
                if let Err(error) = self.parser.parse_file(file_path, &source, &mut self.graph) {
                    self.push_index_warning(file_path, format!("parser failed while indexing: {error}"));
                    return Ok(());
                }
                self.tracked_files.insert(file_path.to_path_buf());
            }
            Err(error) => {
                self.push_index_warning(
                    file_path,
                    format!("unable to index source file (likely non-UTF8): {error}"),
                );
                tracing::warn!(
                    path = %file_path.display(),
                    error = %error,
                    "unable to index source file"
                );
            }
        }
        Ok(())
    }

    fn push_index_warning(&mut self, file_path: &Path, message: String) {
        let warning = LspDiagnostic {
            severity: "warning".to_string(),
            source: Some("indexer".to_string()),
            code: None,
            message,
            file: file_path.display().to_string(),
            line: 1,
            column: 1,
            language: language_for_path(file_path).to_string(),
        };

        if self
            .index_warnings
            .iter()
            .any(|existing| existing.file == warning.file && existing.message == warning.message)
        {
            return;
        }
        self.index_warnings.push(warning);
    }
}

fn is_supported_source_path(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|ext| ext.to_str()),
        Some("ts" | "tsx" | "js" | "jsx" | "mjs" | "cjs" | "py" | "rs" | "go" | "java")
    )
}

fn language_for_path(path: &Path) -> &'static str {
    match path.extension().and_then(|ext| ext.to_str()) {
        Some("ts" | "tsx") => "typescript",
        Some("js" | "jsx" | "mjs" | "cjs") => "javascript",
        Some("py") => "python",
        Some("rs") => "rust",
        Some("go") => "go",
        Some("java") => "java",
        _ => "unknown",
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    use notify::event::{CreateKind, ModifyKind, RemoveKind, RenameMode};

    async fn fixture() -> (tempfile::TempDir, IndexerService) {
        let temp = tempfile::tempdir().unwrap();
        std::fs::create_dir(temp.path().join("src")).unwrap();
        std::fs::write(
            temp.path().join("src/a.ts"),
            "export function original() { return fetch(); }",
        )
        .unwrap();
        let mut config = IndexerConfig::from_env();
        config.workspace_root = temp.path().to_path_buf();
        config.snapshot_output_path = temp.path().join(".crucible/index-snapshot.json");
        config.lsp_enabled = false;
        config.backend_url = None;
        let mut service = IndexerService::new(config).unwrap();
        service.bootstrap().await.unwrap();
        (temp, service)
    }

    #[tokio::test]
    async fn deletion_after_a_recent_event_removes_nodes_and_incident_edges() {
        let (temp, mut service) = fixture().await;
        let path = temp.path().join("src/a.ts");
        service
            .last_watch_event
            .insert(path.clone(), Instant::now());
        std::fs::remove_file(&path).unwrap();
        service
            .handle_watch_event(Event::new(EventKind::Remove(RemoveKind::File)).add_path(path))
            .await
            .unwrap();
        let snapshot: IndexSnapshot =
            serde_json::from_slice(&std::fs::read(&service.config.snapshot_output_path).unwrap())
                .unwrap();
        assert!(
            snapshot.graph.nodes.is_empty(),
            "deleted source must disappear from the published graph"
        );
        assert!(snapshot.graph.edges.is_empty());
        assert!(service.tracked_files.is_empty());
    }

    #[tokio::test]
    async fn directory_deletion_removes_descendant_source() {
        let (temp, mut service) = fixture().await;
        let path = temp.path().join("src");
        std::fs::remove_dir_all(&path).unwrap();
        service
            .handle_watch_event(Event::new(EventKind::Remove(RemoveKind::Folder)).add_path(path))
            .await
            .unwrap();
        assert!(service.graph.all_nodes().is_empty());
        assert!(service.tracked_files.is_empty());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn canonical_watcher_paths_replace_and_delete_configured_alias_paths() {
        let (temp, service) = fixture().await;
        let alias_parent = tempfile::tempdir().unwrap();
        let alias = alias_parent.path().join("workspace");
        std::os::unix::fs::symlink(temp.path(), &alias).unwrap();
        let mut config = service.config.clone();
        config.workspace_root = alias.clone();
        config.snapshot_output_path = alias.join(".crucible/index-snapshot.json");
        let mut service = IndexerService::new(config).unwrap();
        service.bootstrap().await.unwrap();
        let physical = temp.path().canonicalize().unwrap().join("src/a.ts");
        std::fs::write(&physical, "export function replacement() {}").unwrap();
        service
            .handle_watch_event(
                Event::new(EventKind::Modify(ModifyKind::Any)).add_path(physical.clone()),
            )
            .await
            .unwrap();
        assert!(!service
            .graph
            .all_nodes()
            .iter()
            .any(|n| n.name == "original"));
        assert_eq!(service.tracked_files.len(), 1);
        std::fs::remove_file(&physical).unwrap();
        service
            .handle_watch_event(Event::new(EventKind::Remove(RemoveKind::File)).add_path(physical))
            .await
            .unwrap();
        assert!(service.graph.all_nodes().is_empty());
    }

    #[tokio::test]
    async fn rename_removes_old_path_and_indexes_new_path() {
        let (temp, mut service) = fixture().await;
        let old = temp.path().join("src/a.ts");
        let new = temp.path().join("src/b.ts");
        std::fs::rename(&old, &new).unwrap();
        service
            .handle_watch_event(
                Event::new(EventKind::Modify(ModifyKind::Name(RenameMode::Both)))
                    .add_path(old.clone())
                    .add_path(new.clone()),
            )
            .await
            .unwrap();
        assert!(service
            .graph
            .nodes_in_file(&old.display().to_string())
            .is_empty());
        assert!(!service
            .graph
            .nodes_in_file(&new.display().to_string())
            .is_empty());
    }

    #[tokio::test]
    async fn deletion_cleans_an_import_stub_even_when_target_never_parsed() {
        let (temp, mut service) = fixture().await;
        let target = temp.path().join("broken.py");
        let importer = temp.path().join("importer.py");
        std::fs::write(&target, "def invalid(:").unwrap();
        std::fs::write(&importer, "import broken\n").unwrap();
        service.index_file(&target).unwrap();
        service.index_file(&importer).unwrap();
        assert!(!service.tracked_files.contains(&target));
        assert!(!service
            .graph
            .nodes_in_file(&target.display().to_string())
            .is_empty());
        std::fs::remove_file(&target).unwrap();
        service
            .handle_watch_event(
                Event::new(EventKind::Remove(RemoveKind::File)).add_path(target.clone()),
            )
            .await
            .unwrap();
        assert!(service
            .graph
            .nodes_in_file(&target.display().to_string())
            .is_empty());
        assert!(!service
            .index_warnings
            .iter()
            .any(|w| w.file == target.display().to_string()));
    }

    #[tokio::test]
    async fn failed_watch_parse_publishes_warning_and_preserves_resolved_graph() {
        let (temp, mut service) = fixture().await;
        let path = temp.path().join("src/a.ts");
        let before_nodes = service.graph.all_nodes();
        let before_edges = service.graph.all_edges();
        std::fs::write(&path, "export function partial() { broken(;").unwrap();
        service
            .handle_watch_event(
                Event::new(EventKind::Modify(ModifyKind::Any)).add_path(path.clone()),
            )
            .await
            .unwrap();
        assert_eq!(service.graph.all_nodes(), before_nodes);
        assert_eq!(service.graph.all_edges(), before_edges);
        let snapshot: IndexSnapshot =
            serde_json::from_slice(&std::fs::read(&service.config.snapshot_output_path).unwrap())
                .unwrap();
        assert!(snapshot
            .diagnostics
            .iter()
            .any(|d| d.message.contains("parser failed")));
        // The next valid parse removes both obsolete data and the resolved warning.
        service.last_watch_event.remove(&path);
        std::fs::write(&path, "export function replacement() {}").unwrap();
        service
            .handle_watch_event(Event::new(EventKind::Create(CreateKind::File)).add_path(path))
            .await
            .unwrap();
        assert!(!service
            .graph
            .all_nodes()
            .iter()
            .any(|n| n.name == "original" || n.name == "partial"));
        assert!(service.index_warnings.is_empty());
    }
}
