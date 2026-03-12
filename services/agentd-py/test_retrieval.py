import sys
import json
from pathlib import Path
from agentd.retrieval.artifact_client import RetrievalArtifactClient

def main():
    goal = "Implement GET /v1/tasks/{task_id}/events in agentd-py. Return ordered lifecycle events with fields at, from_status, to_status, reason. Add API tests"
    workspace = "/Users/pradeepkumar/projects/AI editor/workspaces/shadow-forge-stress"
    
    import os
    os.environ["AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH"] = f"{workspace}/.ai-editor/index-snapshot.json"
    os.environ["AI_EDITOR_INDEXER_INDEX_CMD"] = "source /Users/pradeepkumar/.cargo/env && cd '/Users/pradeepkumar/projects/AI editor/services/indexer-rs' && cargo run --release -- index --workspace {workspace} --snapshot-path {snapshot_path} --watch 0"
    
    client = RetrievalArtifactClient.from_env()
    context, diagnostics = client.load_context(workspace, goal)
    
    payload = context.as_prompt_payload()
    print(json.dumps({
        "repository_structure_length": len(payload.get("repository_structure", [])),
        "repository_structure_sample": payload.get("repository_structure", [])[:5],
        "related_files": payload["related_files"],
        "related_symbols": payload["related_symbols"],
        "graph_neighbors": payload["graph_neighbors"],
        "diagnostics_excerpt": payload["diagnostics_excerpt"],
        "counts": {
            "files": len(payload["related_files"]),
            "symbols": len(payload["related_symbols"]),
            "neighbors": len(payload["graph_neighbors"])
        }
    }, indent=2))
    
    if diagnostics:
        print("\nDiagnostics:")
        for d in diagnostics:
            print(f"- [{d.level}] {d.source}: {d.message}")

if __name__ == "__main__":
    main()
