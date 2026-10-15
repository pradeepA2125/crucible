import json
import sqlite3

from agentd.eval.memory_evidence import budget_replay, evaluate, measure


def test_offline_report_is_read_only_and_does_not_export_source_text(tmp_path):
    chat_path, segments_path = tmp_path / "chat.db", tmp_path / "segments.db"
    secret = "PRIVATE_SOURCE_NOT_FOR_EXPORT"
    with sqlite3.connect(chat_path) as conn:
        conn.execute("CREATE TABLE chat_threads(thread_id TEXT,controller_history_json TEXT)")
        conn.execute("INSERT INTO chat_threads VALUES(?,?)", (
            "t", json.dumps([{"role": "user", "content": secret}]),
        ))
    with sqlite3.connect(segments_path) as conn:
        conn.execute(
            "CREATE TABLE compaction_segments(id TEXT,run_id TEXT,seq INTEGER,"
            "content TEXT,created_at TEXT)",
        )
        conn.execute("INSERT INTO compaction_segments VALUES(?,?,?,?,?)", (
            "s", "t", 1, secret, "2026-10-14T00:00:00+00:00",
        ))
    before = [path.read_bytes() for path in [chat_path, segments_path]]
    report = evaluate(chat_path, segments_path)
    assert report["model_calls"] == 0 and report["read_only"]
    assert secret not in json.dumps(report)
    assert before == [path.read_bytes() for path in [chat_path, segments_path]]
    assert report["totals"]["history"]["source_rows"] == 1
    assert report["totals"]["segments"]["source_rows"] == 1


def test_budget_replay_detects_one_indivisible_batch_and_name_retention_is_diagnostic():
    sources = [
        {"role": "assistant", "content": "one batch"},
        *[{"role": "tool_result", "tool": "read_file", "content":
           "prefix " * 500 + "\nclass LostName:\n" + "suffix " * 500} for _ in range(1000)],
    ]
    stats = measure(sources)
    assert stats["observed_declaration_names"] == 1000
    assert stats["declaration_names_present_anywhere_in_rendering"] == 0
    assert budget_replay(sources, 128000)["blocked_indivisible_input"]
