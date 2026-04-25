from __future__ import annotations

from pathlib import Path

from agentd.retrieval.chunker import CodeChunk, CodeChunker, _path_to_module, _to_int


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _node(
    node_id: str,
    path: str,
    name: str,
    kind: str,
    line: int,
) -> dict[str, object]:
    return {"id": node_id, "path": path, "name": name, "kind": kind, "line": line}


def _edge(from_id: str, to_id: str, kind: str) -> dict[str, object]:
    return {"from": from_id, "to": to_id, "kind": kind}


def _snapshot(nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> dict[str, object]:
    return {"graph": {"nodes": nodes, "edges": edges}}


# ── _to_int ───────────────────────────────────────────────────────────────────


def test_to_int_int() -> None:
    assert _to_int(42) == 42


def test_to_int_float() -> None:
    assert _to_int(3.7) == 3


def test_to_int_string() -> None:
    assert _to_int("10") == 10


def test_to_int_invalid() -> None:
    assert _to_int("nope", 0) == 0
    assert _to_int(None, 99) == 99  # type: ignore[arg-type]


# ── _path_to_module ───────────────────────────────────────────────────────────


def test_path_to_module_python() -> None:
    assert _path_to_module("src/auth/client.py") == "src.auth.client"


def test_path_to_module_typescript() -> None:
    assert _path_to_module("apps/editor-client/src/index.ts") == "apps.editor-client.src.index"


def test_path_to_module_no_extension() -> None:
    assert _path_to_module("src/util") == "src.util"


# ── CodeChunker.build ─────────────────────────────────────────────────────────


def _write_file(workspace: Path, rel: str, content: str) -> None:
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_build_returns_empty_for_empty_snapshot(tmp_path: Path) -> None:
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, {})
    assert chunks == []


def test_build_skips_file_kind_nodes(tmp_path: Path) -> None:
    _write_file(tmp_path, "src/util.py", "x = 1\n")
    snapshot = _snapshot(
        nodes=[_node("file:src/util.py", "src/util.py", "util.py", "File", 1)],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert chunks == []


def test_build_single_function(tmp_path: Path) -> None:
    src = "def greet(name: str) -> str:\n    return f'hello {name}'\n"
    _write_file(tmp_path, "src/greet.py", src)
    snapshot = _snapshot(
        nodes=[
            _node("file:src/greet.py", "src/greet.py", "greet.py", "File", 1),
            _node("function:file:src/greet.py:greet", "src/greet.py", "greet", "Function", 1),
        ],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.name == "greet"
    assert c.kind == "Function"
    assert c.path == "src/greet.py"
    assert c.language == "python"
    assert c.line_start == 1
    assert "def greet" in c.text
    assert c.chunk_id == "src/greet.py::L1"
    assert c.module_path == "src.greet"
    assert c.is_top_level is True
    assert c.parent_name is None


def test_build_two_functions_correct_boundaries(tmp_path: Path) -> None:
    lines = [
        "def foo() -> int:",
        "    return 1",
        "",
        "",
        "def bar() -> int:",
        "    return 2",
    ]
    _write_file(tmp_path, "mod.py", "\n".join(lines) + "\n")
    snapshot = _snapshot(
        nodes=[
            _node("function:file:mod.py:foo", "mod.py", "foo", "Function", 1),
            _node("function:file:mod.py:bar", "mod.py", "bar", "Function", 5),
        ],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert len(chunks) == 2
    foo, bar = (c for c in sorted(chunks, key=lambda c: c.line_start))
    assert foo.name == "foo"
    assert foo.line_start == 1
    assert foo.line_end == 4  # ends one line before bar's start
    assert bar.name == "bar"
    assert bar.line_start == 5


def test_build_method_extracts_parent(tmp_path: Path) -> None:
    src = "class Auth:\n    def login(self):\n        pass\n"
    _write_file(tmp_path, "auth.py", src)
    snapshot = _snapshot(
        nodes=[
            _node("class:file:auth.py:Auth", "auth.py", "Auth", "Class", 1),
            _node("method:file:auth.py:Auth:login:2", "auth.py", "login", "Method", 2),
        ],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    method_chunks = [c for c in chunks if c.kind == "Method"]
    assert len(method_chunks) == 1
    m = method_chunks[0]
    assert m.parent_name == "Auth"
    assert m.parent_kind == "Class"
    assert m.is_top_level is False


def test_build_max_chunk_lines_respected(tmp_path: Path) -> None:
    many_lines = "\n".join(f"    x{i} = {i}" for i in range(200))
    src = f"def big():\n{many_lines}\n"
    _write_file(tmp_path, "big.py", src)
    snapshot = _snapshot(
        nodes=[_node("function:file:big.py:big", "big.py", "big", "Function", 1)],
        edges=[],
    )
    chunker = CodeChunker(max_chunk_lines=50)
    chunks = chunker.build(tmp_path, snapshot)
    assert len(chunks) == 1
    assert chunks[0].line_count <= 50


def test_build_imports_edge(tmp_path: Path) -> None:
    _write_file(tmp_path, "a.py", "import os\ndef f(): pass\n")
    snapshot = _snapshot(
        nodes=[
            _node("function:file:a.py:f", "a.py", "f", "Function", 2),
        ],
        edges=[
            _edge("file:a.py", "external:module:os", "Imports"),
        ],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert len(chunks) == 1
    assert "os" in chunks[0].imports


def test_build_calls_edge(tmp_path: Path) -> None:
    _write_file(tmp_path, "a.py", "def caller(): callee()\ndef callee(): pass\n")
    snapshot = _snapshot(
        nodes=[
            _node("function:file:a.py:caller", "a.py", "caller", "Function", 1),
            _node("function:file:a.py:callee", "a.py", "callee", "Function", 2),
        ],
        edges=[
            _edge("function:file:a.py:caller", "function:file:a.py:callee", "Calls"),
        ],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    caller = next(c for c in chunks if c.name == "caller")
    callee = next(c for c in chunks if c.name == "callee")
    assert "callee" in caller.calls
    assert "caller" in callee.called_by


def test_build_context_lines(tmp_path: Path) -> None:
    src = "# header\n# comment\ndef f():\n    pass\n# footer\n"
    _write_file(tmp_path, "x.py", src)
    snapshot = _snapshot(
        nodes=[_node("function:file:x.py:f", "x.py", "f", "Function", 3)],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert len(chunks) == 1
    assert "# comment" in chunks[0].context_before


def test_build_skips_missing_file(tmp_path: Path) -> None:
    snapshot = _snapshot(
        nodes=[_node("function:file:missing.py:f", "missing.py", "f", "Function", 1)],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert chunks == []


def test_build_is_test_flag(tmp_path: Path) -> None:
    _write_file(tmp_path, "tests/test_foo.py", "def test_bar(): pass\n")
    snapshot = _snapshot(
        nodes=[_node(
            "function:file:tests/test_foo.py:test_bar",
            "tests/test_foo.py", "test_bar", "Function", 1,
        )],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert chunks[0].is_test is True


def test_build_docstring_python(tmp_path: Path) -> None:
    src = 'def f():\n    """Do the thing."""\n    pass\n'
    _write_file(tmp_path, "d.py", src)
    snapshot = _snapshot(
        nodes=[_node("function:file:d.py:f", "d.py", "f", "Function", 1)],
        edges=[],
    )
    chunker = CodeChunker()
    chunks = chunker.build(tmp_path, snapshot)
    assert chunks[0].docstring == "Do the thing."
    assert chunks[0].has_docstring is True


# ── make_embedding_text ───────────────────────────────────────────────────────


def _make_chunk(**overrides: object) -> CodeChunk:
    defaults: dict[str, object] = dict(
        chunk_id="src/x.py::L1",
        path="src/x.py",
        language="python",
        line_start=1,
        line_end=5,
        line_count=5,
        name="do_thing",
        kind="Function",
        signature="def do_thing() -> None:",
        parent_name=None,
        parent_kind=None,
        module_path="src.x",
        is_top_level=True,
        imports=["os", "json"],
        calls=["helper"],
        called_by=[],
        docstring="Does the thing.",
        text="def do_thing() -> None:\n    pass",
        text_with_lines="   1: def do_thing() -> None:\n   2:     pass",
        context_before="",
        context_after="",
        is_test=False,
        has_docstring=True,
        file_mtime=0.0,
        indexed_at_ms=0,
    )
    defaults.update(overrides)  # type: ignore[arg-type]
    return CodeChunk(**defaults)  # type: ignore[arg-type]


def test_make_embedding_text_includes_kind_and_name() -> None:
    chunk = _make_chunk()
    text = CodeChunker().make_embedding_text(chunk)
    assert "Function: do_thing" in text
    assert "src/x.py:1" in text


def test_make_embedding_text_includes_parent() -> None:
    chunk = _make_chunk(parent_name="MyClass", parent_kind="Class")
    text = CodeChunker().make_embedding_text(chunk)
    assert "Parent: MyClass (Class)" in text


def test_make_embedding_text_includes_docstring() -> None:
    chunk = _make_chunk()
    text = CodeChunker().make_embedding_text(chunk)
    assert "Does the thing." in text


def test_make_embedding_text_includes_code_body() -> None:
    chunk = _make_chunk()
    text = CodeChunker().make_embedding_text(chunk)
    assert "def do_thing" in text
