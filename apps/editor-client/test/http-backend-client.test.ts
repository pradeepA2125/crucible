import { describe, expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";
import type { ChatEvent, PatchStreamEvent } from "../src/contracts/task-contracts.js";

describe("HttpBackendClient", () => {
  test("maps snake_case backend payload to camelCase task view", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(
          JSON.stringify({
            task_id: "task-123",
            goal: "goal",
            status: "AWAITING_PLAN_APPROVAL",
            modified_files: ["a.ts"],
            diagnostics: [],
            plan_markdown: "# Plan\n\n- Add route"
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
    });

    const result = await client.getTask("task-123");
    expect(result.taskId).toBe("task-123");
    expect(result.modifiedFiles).toEqual(["a.ts"]);
    expect(result.planMarkdown).toBe("# Plan\n\n- Add route");
  });

  test("accepts diagnostics with null file/line/column fields from backend", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(
          JSON.stringify({
            task_id: "task-124",
            goal: "goal",
            status: "VALIDATING",
            modified_files: ["main.py"],
            diagnostics: [
              {
                source: "validator:python-compileall",
                message: "failed",
                level: "error",
                file: null,
                line: null,
                column: null
              }
            ]
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
    });

    const result = await client.getTask("task-124");
    expect(result.taskId).toBe("task-124");
    expect(result.diagnostics).toEqual([
      {
        source: "validator:python-compileall",
        message: "failed",
        level: "error"
      }
    ]);
  });

  test("maps snake_case backend payload to camelCase task result", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(
          JSON.stringify({
            task_id: "task-123",
            status: "READY_FOR_REVIEW",
            plan: {
              analysis: "a",
              steps: [
                {
                  id: "S1",
                  goal: "g",
                  targets: [{ path: "a.ts", intent: "existing" }],
                  risk: "low"
                }
              ],
              expected_files: ["a.ts"],
              stop_conditions: ["done"]
            },
            patch: {
              patch_ops: [
                {
                  op: "create_file",
                  file: "a.ts",
                  content: "x",
                  reason: "init"
                }
              ]
            },
            modified_files: ["a.ts"],
            diagnostics: [],
            promoted_at: null,
            shadow_workspace_path: "/tmp/shadow/task-123"
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
    });

    const result = await client.getTaskResult("task-123");
    expect(result.taskId).toBe("task-123");
    expect(result.modifiedFiles).toEqual(["a.ts"]);
    expect(result.shadowWorkspacePath).toBe("/tmp/shadow/task-123");
  });

  test("sends workspace_path to backend when creating task", async () => {
    let body = "";

    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        body = String(init?.body ?? "");
        return new Response(JSON.stringify({ task_id: "task-999" }), {
          status: 200,
          headers: { "content-type": "application/json" }
        });
      }
    });

    await client.submitTask({
      goal: "goal",
      workspacePath: "/tmp/repo",
      mode: "project_edit"
    });

    expect(JSON.parse(body)).toEqual({
      goal: "goal",
      workspace_path: "/tmp/repo",
      mode: "project_edit"
    });
  });

  test("posts explicit null when approving a plan without feedback", async () => {
    let body = "";

    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        body = String(init?.body ?? "");
        return new Response(
          JSON.stringify({
            task_id: "task-123",
            goal: "goal",
            status: "AWAITING_PLAN_APPROVAL",
            modified_files: [],
            diagnostics: [],
            plan_markdown: "# Plan"
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        );
      }
    });

    const result = await client.providePlanFeedback("task-123", null);

    expect(JSON.parse(body)).toEqual({ feedback: null });
    expect(result.planMarkdown).toBe("# Plan");
  });

  test("sendScopeDecision posts approve body and parses response", async () => {
    let url = "";
    let body = "";
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (input, init) => {
        url = String(input);
        body = String(init?.body ?? "");
        return new Response(
          JSON.stringify({ task_id: "task-1", status: "EXECUTING" }),
          { status: 200, headers: { "content-type": "application/json" } }
        );
      }
    });
    const result = await client.sendScopeDecision("task-1", {
      decision: "approve",
      files: ["tests/__init__.py"],
      remember: true
    });
    expect(url).toContain("/v1/tasks/task-1/scope-decision");
    expect(JSON.parse(body)).toEqual({
      decision: "approve",
      files: ["tests/__init__.py"],
      remember: true
    });
    expect(result.taskId).toBe("task-1");
    expect(result.status).toBe("EXECUTING");
  });

  test("sendScopeDecision throws on 409", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(JSON.stringify({ detail: "not awaiting" }), { status: 409 })
    });
    await expect(
      client.sendScopeDecision("task-x", { decision: "approve", files: [], remember: false })
    ).rejects.toThrow();
  });

  test("listChatThreads maps snake_case thread list to camelCase summaries", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(
          JSON.stringify({
            threads: [
              {
                thread_id: "chat-abc123",
                workspace_path: "/ws",
                title: "My chat",
                created_at: "2026-05-11T00:00:00Z",
              },
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        ),
    });
    const result = await client.listChatThreads("/ws");
    expect(result).toHaveLength(1);
    expect(result[0].threadId).toBe("chat-abc123");
    expect(result[0].title).toBe("My chat");
    expect(result[0].createdAt).toBe("2026-05-11T00:00:00Z");
  });

  test("getChatThread maps full thread with messages", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(
          JSON.stringify({
            thread_id: "chat-abc123",
            workspace_path: "/ws",
            title: "My chat",
            created_at: "2026-05-11T00:00:00Z",
            messages: [
              {
                role: "user",
                content: "hello",
                type: "text",
                task_id: null,
                timestamp: "2026-05-11T00:00:00Z",
                metadata: {},
              },
            ],
            touched_files: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        ),
    });
    const result = await client.getChatThread("chat-abc123");
    expect(result.threadId).toBe("chat-abc123");
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0].role).toBe("user");
    expect(result.messages[0].content).toBe("hello");
  });

  test("sendChatMessage streams SSE events as an async iterable", async () => {
    const sseBody = [
      'data: {"type":"intent_classified","payload":{"intent":"qa"}}',
      "",
      'data: {"type":"chat_done","payload":{}}',
      "",
    ].join("\n");

    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(sseBody, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    });

    const events: ChatEvent[] = [];
    for await (const event of client.sendChatMessage("chat-abc123", "hello")) {
      events.push(event);
    }
    expect(events[0].type).toBe("intent_classified");
    expect(events[1].type).toBe("chat_done");
  });

  test("streamPatchEvents yields patch events as an async iterable", async () => {
    const sseBody = [
      'data: {"type":"operation_success","op_type":"search_replace","path":"a.ts"}',
      "",
      'data: {"type":"done"}',
      "",
    ].join("\n");

    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async () =>
        new Response(sseBody, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    });

    const events: PatchStreamEvent[] = [];
    for await (const event of client.streamPatchEvents("task-abc")) {
      events.push(event);
    }
    expect(events[0].type).toBe("operation_success");
    expect(events[1].type).toBe("done");
  });
});
