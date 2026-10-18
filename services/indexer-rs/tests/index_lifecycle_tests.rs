use crucible_indexer::graph::{EdgeKind, SymbolEdge, SymbolGraph};
use crucible_indexer::parser::{LanguageParser, TreeSitterParser};
use std::path::{Path, PathBuf};

#[test]
fn failed_reparse_preserves_the_last_good_graph_and_pending_calls() {
    let parser = TreeSitterParser::new(PathBuf::from("."));
    let path = Path::new("src/service.ts");
    let mut graph = SymbolGraph::default();
    parser
        .parse_file(
            path,
            "export function original() { return fetch(); }",
            &mut graph,
        )
        .unwrap();
    let nodes = graph.all_nodes();
    let edges = graph.all_edges();
    let mut previous = graph.clone();
    let pending = previous.take_placeholders().len();

    assert!(parser
        .parse_file(
            path,
            "export function partial() { return broken(;",
            &mut graph
        )
        .is_err());
    assert_eq!(graph.all_nodes(), nodes);
    assert_eq!(graph.all_edges(), edges);
    assert_eq!(graph.take_placeholders().len(), pending);
}

#[test]
fn successful_reparse_removes_old_symbols_imports_and_calls() {
    let parser = TreeSitterParser::new(PathBuf::from("."));
    let path = Path::new("src/service.ts");
    let mut graph = SymbolGraph::default();
    parser
        .parse_file(
            path,
            "import x from 'old-package'; export function removed() { return oldCall(); }",
            &mut graph,
        )
        .unwrap();
    parser
        .parse_file(
            path,
            "export function replacement() { return newCall(); }",
            &mut graph,
        )
        .unwrap();
    assert!(!graph.all_nodes().iter().any(|n| n.name == "removed"));
    assert!(!graph
        .all_edges()
        .iter()
        .any(|e| e.to.contains("old-package") || e.to.contains("oldCall")));
    assert!(graph.all_nodes().iter().any(|n| n.name == "replacement"));
    assert!(graph
        .take_placeholders()
        .iter()
        .all(|p| !p.external_to_id.contains("oldCall")));
}

#[test]
fn reparse_preserves_incoming_edges_to_unchanged_symbols() {
    let parser = TreeSitterParser::new(PathBuf::from("."));
    let path = Path::new("src/service.ts");
    let mut graph = SymbolGraph::default();
    parser
        .parse_file(path, "export function keep() {}", &mut graph)
        .unwrap();
    let target = graph
        .all_nodes()
        .into_iter()
        .find(|n| n.name == "keep")
        .unwrap()
        .id;
    parser
        .parse_file(
            Path::new("src/caller.ts"),
            "export function caller() {}",
            &mut graph,
        )
        .unwrap();
    let caller = graph
        .all_nodes()
        .into_iter()
        .find(|n| n.name == "caller")
        .unwrap()
        .id;
    let incoming = SymbolEdge {
        from: caller,
        to: target,
        kind: EdgeKind::Calls,
    };
    graph.add_edge(incoming.clone());
    parser
        .parse_file(path, "export function keep() { return 1; }", &mut graph)
        .unwrap();
    assert!(graph.all_edges().contains(&incoming));
}

#[test]
fn removal_preserves_shared_external_targets_and_other_files() {
    let parser = TreeSitterParser::new(PathBuf::from("."));
    let mut graph = SymbolGraph::default();
    for file in ["a.ts", "b.ts"] {
        parser
            .parse_file(
                Path::new(file),
                "export function caller() { return shared(); }",
                &mut graph,
            )
            .unwrap();
    }
    graph.remove_file(Path::new("a.ts"));
    assert!(graph.nodes_in_file("a.ts").is_empty());
    assert!(graph
        .all_nodes()
        .iter()
        .any(|n| n.id == "external:call:shared"));
    let nodes = graph.all_nodes();
    assert!(graph.all_edges().iter().all(
        |edge| nodes.iter().any(|n| n.id == edge.from) && nodes.iter().any(|n| n.id == edge.to)
    ));
    assert!(graph
        .take_placeholders()
        .iter()
        .all(|p| p.file_path == Path::new("b.ts")));
    graph.remove_file(Path::new("b.ts"));
    assert!(graph.all_nodes().is_empty());
    assert!(graph.all_edges().is_empty());
}
