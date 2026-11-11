import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";
import { UtilityChannel, type UtilityChild } from "../src/voice/utility-channel.js";

class FakeChild extends EventEmitter implements UtilityChild {
  sent: unknown[] = [];
  killed = false;
  postMessage(message: unknown): void { this.sent.push(message); }
  kill(): boolean { this.killed = true; queueMicrotask(() => this.emit("exit", 0)); return true; }
}

function open() {
  const child = new FakeChild();
  const fork = vi.fn((_script: string, _args: string[]) => child);
  const lines: string[] = [];
  const failures: Error[] = [];
  const channel = new UtilityChannel(fork, "/app/voice-pocket-worker.cjs", ["--models", "/m"],
    line => lines.push(line), error => failures.push(error));
  return { child, fork, lines, failures, channel };
}

describe("UtilityChannel: a worker over Electron's utilityProcess", () => {
  it("starts the script with its arguments and relays string messages as lines", () => {
    const { child, fork, lines } = open();
    expect(fork).toHaveBeenCalledWith("/app/voice-pocket-worker.cjs", ["--models", "/m"]);
    child.emit("message", '{"type":"ready"}');
    child.emit("message", { not: "a line" });
    expect(lines).toEqual(['{"type":"ready"}']);
  });

  it("sends lines to the worker", () => {
    const { child, channel } = open();
    channel.send('{"type":"speak","id":"a","text":"Hi."}');
    expect(child.sent).toEqual(['{"type":"speak","id":"a","text":"Hi."}']);
  });

  it("an unexpected exit is a failure; a requested close is not", async () => {
    const first = open();
    first.child.emit("exit", 1);
    expect(first.failures).toHaveLength(1);
    expect(() => first.channel.send("x")).toThrow();

    const second = open();
    await second.channel.close();
    expect(second.child.killed).toBe(true);
    expect(second.failures).toHaveLength(0);
  });
});
