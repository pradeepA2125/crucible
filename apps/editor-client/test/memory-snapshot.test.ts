import { expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

test("snapshot uses thread identity and retains capability state", async () => {
  let url = "";
  const client = new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: async input => {
    url = input;
    return new Response(JSON.stringify({ pipeline: "durable", status: "unavailable", memories: [] }));
  } });
  expect(await client.getMemorySnapshot("thread /x")).toEqual({ pipeline: "durable", status: "unavailable", memories: [] });
  expect(url).toBe("http://localhost/v1/memory/snapshot?thread_id=thread%20%2Fx");
});
