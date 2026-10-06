import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { shlexJoin, CommandGate } from "../components/messages/gates/CommandGate";
import { ScopeGate } from "../components/messages/gates/ScopeGate";
import { ValidationGate } from "../components/messages/gates/ValidationGate";
import { StepGate } from "../components/messages/gates/StepGate";
import { ModeGate } from "../components/messages/gates/ModeGate";
import { EditGate } from "../components/messages/gates/EditGate";
import { McpGate } from "../components/messages/gates/McpGate";
import { TeamPlanGate } from "../components/messages/gates/TeamPlanGate";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  const mod = await import("../vscodeApi");
  postMessage = mod.vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

// ── 1. shlexJoin ──────────────────────────────────────────────────────────────

describe("shlexJoin", () => {
  it("plain tokens join with a single space", () => {
    expect(shlexJoin(["npm", "run", "build"])).toBe("npm run build");
  });

  it("token containing a space is single-quoted", () => {
    expect(shlexJoin(["echo", "hello world"])).toBe("echo 'hello world'");
  });

  it("token containing a single-quote uses the classic '\"'\"' escaping", () => {
    // The token is: it's  →  expected: 'it'"'"'s'
    expect(shlexJoin(["it's"])).toBe("'it'\"'\"'s'");
  });

  it("metachar token with $ is quoted", () => {
    expect(shlexJoin(["$HOME"])).toBe("'$HOME'");
  });

  it("metachar token with | is quoted", () => {
    expect(shlexJoin(["a|b"])).toBe("'a|b'");
  });

  it("empty array returns empty string", () => {
    expect(shlexJoin([])).toBe("");
  });
});

// ── 2. CommandGate ────────────────────────────────────────────────────────────

const CMD_PAYLOAD = {
  command: "npm",
  args: ["run", "build"],
  step_id: "s1",
  decision_id: "d1",
};

describe("CommandGate — renders", () => {
  it("renders the command text in the command block", () => {
    render(<CommandGate gateId="g1" taskId="t1" payload={CMD_PAYLOAD} />);
    // shlexJoin(["npm","run","build"]) = "npm run build"
    expect(screen.getByText("npm run build")).toBeTruthy();
  });

  it("renders the step subtitle when step_id is present", () => {
    render(<CommandGate gateId="g1" taskId="t1" payload={CMD_PAYLOAD} />);
    expect(screen.getByText("step s1")).toBeTruthy();
  });
});

describe("CommandGate — Allow once", () => {
  it("posts commandDecision with approve:true, remember:false, scope:exact", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: false,
      scope: "exact",
    });
  });
});

describe("CommandGate — Allow & remember default scope", () => {
  it("defaults to prefix with the binary+verb pair for a subcommand tool (npm run build)", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    // No radio clicks — exercise the default selection as-is.
    fireEvent.click(screen.getByRole("button", { name: /allow.*remember/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: true,
      scope: "prefix",
      // shlexJoin(["npm","run"]) = "npm run" — refires on any "npm run ..." variant.
      ruleValue: "npm run",
    });
  });

  it("defaults to prefix with just the binary for a single-word test runner (pytest)", () => {
    render(
      <CommandGate
        gateId="g1"
        taskId="task-cmd"
        payload={{ command: "pytest", args: ["tests/test_foo.py::test_bar", "-v"] }}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /allow.*remember/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: true,
      scope: "prefix",
      ruleValue: "pytest",
    });
  });

  it("Allow once still uses scope:exact regardless of the default remember scope", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: false,
      scope: "exact",
    });
  });
});

describe("CommandGate — Allow & remember with binary scope", () => {
  it("posts ruleValue = basename when binary scope is selected", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    // Select binary radio (third radio)
    const radios = screen.getAllByRole("radio");
    // radios[0]=exact, radios[1]=prefix, radios[2]=binary
    fireEvent.click(radios[2]);

    fireEvent.click(screen.getByRole("button", { name: /allow.*remember/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: true,
      scope: "binary",
      ruleValue: "npm", // basename of "npm"
    });
  });
});

describe("CommandGate — Allow & remember with prefix scope", () => {
  it("posts shlexJoin of first 2 tokens when prefixCount=2", () => {
    render(
      <CommandGate
        gateId="g1"
        taskId="task-cmd"
        payload={{ command: "npm", args: ["run", "build"], step_id: "s1", decision_id: "d1" }}
      />
    );

    // Select prefix radio (second radio)
    const radios = screen.getAllByRole("radio");
    fireEvent.click(radios[1]);

    // Set the prefix count number input to 2
    const numInput = screen.getByRole("spinbutton");
    fireEvent.change(numInput, { target: { value: "2" } });

    fireEvent.click(screen.getByRole("button", { name: /allow.*remember/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: true,
      remember: true,
      scope: "prefix",
      // shlexJoin(["npm","run"]) = "npm run"
      ruleValue: "npm run",
    });
  });
});

describe("CommandGate — Reject", () => {
  it("posts approve:false when Reject is clicked", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "commandDecision",
      gateId: "g1",
      taskId: "task-cmd",
      approve: false,
    });
  });
});

describe("CommandGate — keyboard-operable radios", () => {
  it("pressing Space on a radio option selects it (aria-checked=true)", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    // radios[1]=prefix (selected by default for a subcommand tool like npm), radios[2]=binary (not selected)
    const radios = screen.getAllByRole("radio");
    const binaryRadio = radios[2];

    expect(binaryRadio.getAttribute("aria-checked")).toBe("false");

    fireEvent.keyDown(binaryRadio, { key: " " });

    expect(binaryRadio.getAttribute("aria-checked")).toBe("true");
  });

  it("pressing Enter on a radio option selects it (aria-checked=true)", () => {
    render(<CommandGate gateId="g1" taskId="task-cmd" payload={CMD_PAYLOAD} />);

    // radios[0]=exact — not selected by default (prefix is, for a subcommand tool like npm).
    const radios = screen.getAllByRole("radio");
    const exactRadio = radios[0];

    expect(exactRadio.getAttribute("aria-checked")).toBe("false");

    fireEvent.keyDown(exactRadio, { key: "Enter" });

    expect(exactRadio.getAttribute("aria-checked")).toBe("true");
  });
});

describe("CommandGate — one-shot after any action", () => {
  it("all three action buttons are gone after Allow once", () => {
    render(<CommandGate gateId="g1" taskId="t1" payload={CMD_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));

    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /allow.*remember/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^reject$/i })).toBeNull();
  });

  it("all three action buttons are gone after Reject", () => {
    render(<CommandGate gateId="g1" taskId="t1" payload={CMD_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /allow.*remember/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^reject$/i })).toBeNull();
  });
});

// ── 3. ScopeGate ──────────────────────────────────────────────────────────────

const SCOPE_PAYLOAD = {
  files: ["src/foo.ts", "src/bar.py"],
  reason: "Agent wants to update helpers",
  step_id: "s2",
  decision_id: "d2",
};

describe("ScopeGate — renders", () => {
  it("renders the file list", () => {
    render(<ScopeGate taskId="t1" payload={SCOPE_PAYLOAD} />);
    expect(screen.getByText("src/foo.ts")).toBeTruthy();
    expect(screen.getByText("src/bar.py")).toBeTruthy();
  });

  it("renders the reason text", () => {
    render(<ScopeGate taskId="t1" payload={SCOPE_PAYLOAD} />);
    expect(screen.getByText("Agent wants to update helpers")).toBeTruthy();
  });
});

describe("ScopeGate — Approve & remember", () => {
  it("posts remember:true with files array", () => {
    render(<ScopeGate taskId="task-scope" payload={SCOPE_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /approve.*remember/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "scopeDecision",
      taskId: "task-scope",
      files: ["src/foo.ts", "src/bar.py"],
      decision: "approve",
      remember: true,
    });
  });
});

describe("ScopeGate — Approve once", () => {
  it("posts remember:false", () => {
    render(<ScopeGate taskId="task-scope" payload={SCOPE_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "scopeDecision",
      taskId: "task-scope",
      files: ["src/foo.ts", "src/bar.py"],
      decision: "approve",
      remember: false,
    });
  });
});

describe("ScopeGate — Reject", () => {
  it("posts decision:reject", () => {
    render(<ScopeGate taskId="task-scope" payload={SCOPE_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "scopeDecision",
      taskId: "task-scope",
      files: ["src/foo.ts", "src/bar.py"],
      decision: "reject",
      remember: false,
    });
  });
});

// ── 4. ValidationGate ─────────────────────────────────────────────────────────

const VALIDATION_PAYLOAD = {
  task_id: "t1",
  summary: "2 errors remain",
  diagnostics: [
    { level: "error", message: "Undefined variable x", source: "ruff" },
    { level: "warning", message: "Unused import os", source: "ruff" },
  ],
};

describe("ValidationGate — renders", () => {
  it("renders diagnostics with level tags", () => {
    render(<ValidationGate taskId="t1" payload={VALIDATION_PAYLOAD} />);
    expect(screen.getByText("[error]")).toBeTruthy();
    expect(screen.getByText("[warning]")).toBeTruthy();
    expect(screen.getByText("Undefined variable x")).toBeTruthy();
    expect(screen.getByText("Unused import os")).toBeTruthy();
  });
});

describe("ValidationGate — Accept", () => {
  it("posts validationDecision accept", () => {
    render(<ValidationGate taskId="task-val" payload={VALIDATION_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^accept$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "validationDecision",
      taskId: "task-val",
      decision: "accept",
    });
  });
});

describe("ValidationGate — Reject", () => {
  it("posts validationDecision reject", () => {
    render(<ValidationGate taskId="task-val" payload={VALIDATION_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "validationDecision",
      taskId: "task-val",
      decision: "reject",
    });
  });
});

// ── 5. StepGate ───────────────────────────────────────────────────────────────

const STEP_PAYLOAD = {
  step_id: "s3",
  step_title: "Update routes",
  diff_entries: [
    {
      path: "services/agentd-py/agentd/api/routes.py",
      additions: 5,
      deletions: 2,
      temp_path: "/tmp/shadow/routes.py",
    },
  ],
};

describe("StepGate — renders", () => {
  it("renders file basename in the file row", () => {
    render(<StepGate taskId="t1" payload={STEP_PAYLOAD} />);
    expect(screen.getByText("routes.py")).toBeTruthy();
  });
});

describe("StepGate — Accept", () => {
  it("posts stepDecision accept", () => {
    render(<StepGate taskId="task-step" payload={STEP_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^accept$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "stepDecision",
      taskId: "task-step",
      decision: "accept",
    });
  });
});

describe("StepGate — view diff button", () => {
  it("posts viewDiffFile with temp_path when view button clicked", () => {
    render(<StepGate taskId="t1" payload={STEP_PAYLOAD} />);

    const viewBtn = screen.getByTitle("Open diff in editor");
    fireEvent.click(viewBtn);

    expect(postMessage).toHaveBeenCalledWith({
      type: "viewDiffFile",
      path: "services/agentd-py/agentd/api/routes.py",
      shadowPath: "/tmp/shadow/routes.py",
    });
  });
});

// ── 6. ModeGate ───────────────────────────────────────────────────────────────

const MODE_PAYLOAD = {
  plan_sketch: "I'd add a rate_limit decorator in api/deps.py and apply it to three routes.",
  recommended: "create_task",
  reason: "Touches 4 files with a schema change.",
  options: [
    { mode: "create_task", label: "Plan it as a task", description: "Explore then plan." },
    { mode: "edit", label: "Edit inline now", description: "I edit directly." },
    { mode: "explain", label: "Just explain", description: "No changes." },
  ],
};

describe("ModeGate — renders", () => {
  it("renders the plan sketch and the recommended option", () => {
    render(<ModeGate taskId="th1" payload={MODE_PAYLOAD} />);
    expect(screen.getByText(/rate_limit decorator/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /plan it as a task.*recommended/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /edit inline now/i })).toBeTruthy();
  });
});

describe("ModeGate — pick a mode", () => {
  it("posts modeDecision with the chosen mode and threadId", () => {
    render(<ModeGate taskId="thread-1" payload={MODE_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /edit inline now/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "modeDecision",
      threadId: "thread-1",
      mode: "edit",
    });
  });

  it("is one-shot — all option buttons gone after a pick", () => {
    render(<ModeGate taskId="thread-1" payload={MODE_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /just explain/i }));

    expect(screen.queryByRole("button", { name: /plan it as a task/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit inline now/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /just explain/i })).toBeNull();
  });
});

// ── 7. EditGate ───────────────────────────────────────────────────────────────

const EDIT_PAYLOAD = {
  diff_entries: [
    {
      path: "services/agentd-py/agentd/api/deps.py",
      additions: 8,
      deletions: 0,
      temp_path: "/tmp/shadow/deps.py",
    },
  ],
};

describe("EditGate — renders", () => {
  it("renders the changed file basename", () => {
    render(<EditGate gateId="g1" taskId="th1" payload={EDIT_PAYLOAD} />);
    expect(screen.getByText("deps.py")).toBeTruthy();
  });
});

describe("EditGate — Accept", () => {
  it("posts editDecision accept with threadId", () => {
    render(<EditGate gateId="g1" taskId="thread-1" payload={EDIT_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^accept$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "editDecision",
      gateId: "g1",
      threadId: "thread-1",
      decision: "accept",
      reason: "",
    });
  });
});

describe("EditGate — Reject", () => {
  it("opens a reason box on Reject, then posts editDecision reject with threadId on confirm", () => {
    render(<EditGate gateId="g1" taskId="thread-1" payload={EDIT_PAYLOAD} />);

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    expect(postMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "editDecision",
      gateId: "g1",
      threadId: "thread-1",
      decision: "reject",
      reason: "",
    });
  });
});

// ── McpGate ───────────────────────────────────────────────────────────────────

describe("McpGate", () => {
  it("renders server.tool + args and posts mcpDecision on approve", () => {
    render(
      <McpGate
        gateId="g1"
        taskId="th1"
        payload={{ server: "gh", tool: "create_issue", args: { title: "bug" } }}
      />
    );
    expect(screen.getByText(/Call MCP tool: gh\.create_issue/)).toBeTruthy();
    expect(screen.getByText(/"title": "bug"/)).toBeTruthy();
    fireEvent.click(screen.getByText("Approve once"));
    expect(postMessage).toHaveBeenCalledWith({
      type: "mcpDecision", threadId: "th1", gateId: "g1", approve: true, remember: false,
    });
  });

  it("approve & remember posts remember=true", () => {
    render(<McpGate gateId="g1" taskId="th1" payload={{ server: "s", tool: "t", args: {} }} />);
    fireEvent.click(screen.getByText(/Approve & remember/));
    expect(postMessage).toHaveBeenCalledWith({
      type: "mcpDecision", threadId: "th1", gateId: "g1", approve: true, remember: true,
    });
  });

  it("reject posts approve=false", () => {
    render(<McpGate gateId="g1" taskId="th1" payload={{ server: "s", tool: "t", args: {} }} />);
    fireEvent.click(screen.getByText("Reject"));
    expect(postMessage).toHaveBeenCalledWith({
      type: "mcpDecision", threadId: "th1", gateId: "g1", approve: false, remember: false,
    });
  });

  it("one-shot guard: second click posts nothing", () => {
    render(<McpGate gateId="g1" taskId="th1" payload={{ server: "s", tool: "t", args: {} }} />);
    fireEvent.click(screen.getByText("Reject"));
    postMessage.mockClear();
    expect(screen.queryByText("Approve once")).toBeNull();
  });
});

describe("TeamPlanGate", () => {
  const payload = {
    team_id: "team-1", team_name: "auth", proposal_id: "P3", text: "Add a limiter.",
    assignments: [{ member: "alice", part: "limiter", files: ["api/limiter.py"] },
                  { member: "bob", part: "tests", files: [] }],
    shared_files: ["api/routes.py"],
  };

  it("shows the plan with its assignments", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    expect(screen.getByText(/Team auth adopted P3/)).toBeInTheDocument();
    expect(screen.getByText("Add a limiter.")).toBeInTheDocument();
    expect(screen.getByText(/alice → limiter/)).toBeInTheDocument();
    expect(screen.getByText(/api\/limiter\.py/)).toBeInTheDocument();
    expect(screen.getByText(/Shared: api\/routes\.py/)).toBeInTheDocument();
  });

  it("approves once", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByText("Approved"));
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith({ type: "teamPlanDecision", threadId: "thread-1",
                                               gateId: "g1", decision: "approve" });
  });

  it("sends feedback only with text", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(postMessage).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Feedback for the team"),
                     { target: { value: "use redis" } });
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "teamPlanDecision", threadId: "thread-1",
                                               gateId: "g1", decision: "feedback",
                                               feedback: "use redis" });
  });

  it("rejects", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ decision: "reject" }));
  });
});
