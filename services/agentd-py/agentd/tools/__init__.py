"""Phase 4 agentic tool-use package.

Tools available to the ReAct loop during step execution:
- search_code: ripgrep exact/regex search across the shadow workspace
- read_file: read a file within the shadow workspace (path-traversal safe)
- run_command: run an allow-listed shell command inside the shadow workspace
- search_semantic: vector similarity search against the live semantic index
"""
from agentd.tools.registry import ToolDefinition, ToolOutput, ToolRegistry

__all__ = ["ToolDefinition", "ToolOutput", "ToolRegistry"]
