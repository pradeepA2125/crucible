import { describe, expect, it, vi } from "vitest";
import { AuthStatusSink } from "../src/backend-auth/auth-status-sink.js";

describe("AuthStatusSink", () => {
  it("sets the error, notifies once per base, clears on the next ok", () => {
    const ui = { setError: vi.fn(), notify: vi.fn() };
    const sink = new AuthStatusSink(ui);
    sink.report("http://127.0.0.1:9", false, "backend refused the request (401)");
    sink.report("http://127.0.0.1:9", false, "backend refused the request (401)");
    expect(ui.notify).toHaveBeenCalledTimes(1);
    expect(ui.setError).toHaveBeenLastCalledWith("backend refused the request (401)");
    sink.report("http://127.0.0.1:9", true);
    expect(ui.setError).toHaveBeenLastCalledWith(null);
  });
});
