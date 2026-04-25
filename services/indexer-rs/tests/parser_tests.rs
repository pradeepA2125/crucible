use ai_editor_indexer::graph::{EdgeKind, SymbolGraph, SymbolKind};
use ai_editor_indexer::parser::{LanguageParser, TreeSitterParser};
use std::path::Path;

#[test]
fn typescript_parser_emits_symbols_and_edges() {
    let parser = TreeSitterParser::new(std::path::PathBuf::from("."));
    let mut graph = SymbolGraph::default();
    let source = r#"
import { fetchUser } from "./api";
export class UserService extends BaseService {
  getUser() {
    return fetchUser();
  }
}
export function buildService() {
  return new UserService();
}
"#;

    parser
        .parse_file(Path::new("src/service.ts"), source, &mut graph)
        .expect("parse");

    let nodes = graph.all_nodes();
    let edges = graph.all_edges();
    assert!(nodes
        .iter()
        .any(|node| node.kind == SymbolKind::Class && node.name == "UserService"));
    assert!(nodes
        .iter()
        .any(|node| node.kind == SymbolKind::Function && node.name == "buildService"));
    assert!(edges.iter().any(|edge| edge.kind == EdgeKind::Imports));
    assert!(edges.iter().any(|edge| edge.kind == EdgeKind::Calls));
}

#[test]
fn python_parser_emits_symbols_and_edges() {
    use std::io::Write;
    // Create a real workspace so cross-file import resolution can be tested.
    let tmp = tempfile::tempdir().expect("tempdir");
    let ws = tmp.path();
    // Write the module that will be imported so resolve_python_module_to_file can find it.
    std::fs::create_dir_all(ws.join("app")).unwrap();
    let mut f = std::fs::File::create(ws.join("app/db.py")).unwrap();
    writeln!(f, "class Repo: pass").unwrap();

    let parser = TreeSitterParser::new(ws.to_path_buf());
    let mut graph = SymbolGraph::default();
    let source = r#"
from app.db import Repo

class AccountService:
    def get_user(self):
        return Repo.fetch()

def run():
    return AccountService()

async def async_run():
    pass
"#;

    parser
        .parse_file(&ws.join("app/service.py"), source, &mut graph)
        .expect("parse");

    let nodes = graph.all_nodes();
    let edges = graph.all_edges();

    // Class and methods must be extracted accurately (no docstring/assignment noise)
    assert!(nodes.iter().any(|n| n.kind == SymbolKind::Class && n.name == "AccountService"),
        "missing class AccountService");
    assert!(nodes.iter().any(|n| n.kind == SymbolKind::Method && n.name == "get_user"),
        "missing method get_user");
    assert!(nodes.iter().any(|n| n.kind == SymbolKind::Function && n.name == "run"),
        "missing function run");
    assert!(nodes.iter().any(|n| n.kind == SymbolKind::Function && n.name == "async_run"),
        "missing async function async_run");

    // File-to-file import edge: app/service.py → app/db.py (the key new capability)
    let target_id = format!("file:{}", ws.join("app/db.py").display());
    assert!(edges.iter().any(|e| e.kind == EdgeKind::Imports && e.to == target_id),
        "missing file-to-file import edge to app/db.py; edges: {edges:?}");

    // No garbage variable nodes from __slots__ or assignment tokenization
    assert!(!nodes.iter().any(|n| n.kind == SymbolKind::Variable && n.name == "self"),
        "spurious 'self' variable node");
    assert!(!nodes.iter().any(|n| n.kind == SymbolKind::Variable && n.name == "return"),
        "spurious 'return' variable node");
}

#[test]
fn rust_parser_emits_symbols_and_edges() {
    let parser = TreeSitterParser::new(std::path::PathBuf::from("."));
    let mut graph = SymbolGraph::default();
    let source = r#"
use crate::storage::Store;

struct App;
trait Runner { fn run(&self); }

impl App {
    fn build(&self) {
        Store::new();
    }
}

fn main() {
    App::build(&App);
}
"#;

    parser
        .parse_file(Path::new("src/main.rs"), source, &mut graph)
        .expect("parse");

    let nodes = graph.all_nodes();
    let edges = graph.all_edges();
    assert!(nodes
        .iter()
        .any(|node| node.kind == SymbolKind::Class && node.name == "App"));
    assert!(nodes
        .iter()
        .any(|node| node.kind == SymbolKind::Interface && node.name == "Runner"));
    assert!(nodes
        .iter()
        .any(|node| node.kind == SymbolKind::Function && node.name == "main"));
    assert!(edges.iter().any(|edge| edge.kind == EdgeKind::Imports));
}
