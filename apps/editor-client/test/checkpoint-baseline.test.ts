import { expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

const client = (body: unknown, urls: string[] = [], status = 200) => new HttpBackendClient({
  baseUrl: "http://localhost",
  fetchFn: async input => { urls.push(String(input)); return new Response(JSON.stringify(body), { status }); },
});
const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));

test("lists checkpoints as metadata and maps snake_case", async () => {
  const urls: string[] = [];
  const result = await client({ checkpoints: [{ seq: 0, anchor_message_id: "m1", turn_id: "t1", created_at: "2026-10-22T00:00:00+00:00",
    files: [{ path: "a.py", existed: true, oversize: false }, { path: "new.py", existed: false, oversize: false }] }] }, urls).listCheckpoints("thread /x");
  expect(urls).toEqual(["http://localhost/v1/chat/threads/thread%20%2Fx/checkpoints"]);
  expect(result).toEqual([{ seq: 0, anchorMessageId: "m1", turnId: "t1", createdAt: "2026-10-22T00:00:00+00:00",
    files: [{ path: "a.py", existed: true, oversize: false }, { path: "new.py", existed: false, oversize: false }] }]);
  expect(await client({ checkpoints: [] }).listCheckpoints("t")).toEqual([]);
});

test("reads the baseline fold and one file's bytes", async () => {
  expect(await client({ seq: 1, files: [{ path: "b.py", existed: true, oversize: false, from_seq: 1 }] }).getCheckpointBaseline("t", 1))
    .toEqual([{ path: "b.py", existed: true, oversize: false, fromSeq: 1 }]);
  expect(await client({ seq: 1, files: [] }).getCheckpointBaseline("t", 1)).toEqual([]);
  const urls: string[] = [];
  const bytes = new Uint8Array([0, 104, 105, 255]);
  const file = await client({ path: "a b.py", from_seq: 0, state: "captured", size: 4, content_base64: encode(bytes) }, urls)
    .getCheckpointBaselineFile("t", 0, "a b.py");
  expect(urls).toEqual(["http://localhost/v1/chat/threads/t/checkpoints/0/baseline/file?path=a%20b.py"]);
  expect(file).toEqual({ path: "a b.py", fromSeq: 0, state: "captured", size: 4, content: bytes });
  expect(await client({ path: "e.py", from_seq: 0, state: "captured", size: 0, content_base64: "" }).getCheckpointBaselineFile("t", 0, "e.py"))
    .toEqual({ path: "e.py", fromSeq: 0, state: "captured", size: 0, content: new Uint8Array() });
  for (const state of ["absent", "oversize", "missing", "unavailable"])
    expect(await client({ path: "n.py", from_seq: 2, state, size: null }).getCheckpointBaselineFile("t", 0, "n.py")).toEqual({ path: "n.py", fromSeq: 2, state, size: null });
  expect(await client({ path: "big.py", from_seq: 0, state: "too_large", size: 2_000_000 }).getCheckpointBaselineFile("t", 0, "big.py"))
    .toEqual({ path: "big.py", fromSeq: 0, state: "too_large", size: 2_000_000 });
});

test("a malformed or incompatible reply throws instead of meaning no changes", async () => {
  await expect(client({ detail: "Not Found" }, [], 404).listCheckpoints("t")).rejects.toThrow();
  await expect(client({}).listCheckpoints("t")).rejects.toThrow();
  await expect(client({}).getCheckpointBaseline("t", 0)).rejects.toThrow();
  await expect(client({ checkpoints: [{ seq: 0, anchor_message_id: "m", turn_id: "t", created_at: "x", files: [{ path: "a.py", existed: true }] }] }).listCheckpoints("t")).rejects.toThrow();
  await expect(client({ seq: 2, files: [] }).getCheckpointBaseline("t", 1)).rejects.toThrow(/checkpoint 2, not 1/);
  await expect(client({ seq: 1, files: [{ path: "a.py", existed: true, oversize: false, from_seq: 0 }] }).getCheckpointBaseline("t", 1)).rejects.toThrow(/earlier checkpoint/);
  const file = (body: Record<string, unknown>, path = "a.txt", seq = 0) => client(body).getCheckpointBaselineFile("t", seq, path);
  await expect(file({ path: "a.txt", from_seq: 0, state: "captured", size: 5 })).rejects.toThrow();
  await expect(file({ path: "a.txt", from_seq: 0, state: "captured", size: 5, content_base64: encode(new Uint8Array([1, 2])) })).rejects.toThrow(/size does not match/);
  await expect(file({ path: "a.txt", from_seq: 0, state: "absent", size: null, content_base64: encode(new Uint8Array([1])) })).rejects.toThrow();
  await expect(file({ path: "a.txt", from_seq: 0, state: "absent", size: 3 })).rejects.toThrow();
  await expect(file({ path: "a.txt", from_seq: 0, state: "too_large", size: null })).rejects.toThrow();
  await expect(file({ path: "a.txt", from_seq: 0, state: "reticulated", size: null })).rejects.toThrow();
  await expect(file({ path: "b.txt", from_seq: 0, state: "absent", size: null })).rejects.toThrow(/does not match/);
  await expect(file({ path: "a.txt", from_seq: 0, state: "absent", size: null }, "a.txt", 1)).rejects.toThrow(/does not match/);
  await expect(file({ path: "a.txt", from_seq: 0, state: "captured", size: 1, content_base64: "!!" })).rejects.toThrow();
  await expect(file({ path: "a.txt", state: "absent", size: null })).rejects.toThrow();
});

test.each(['list', 'fold', 'file'] as const)('forwards %s cancellation through authenticated fetch', async method => {
  const controller = new AbortController(); let observed: RequestInit | undefined;
  const backend = new HttpBackendClient({ baseUrl: 'http://localhost', authToken: () => 'test-token', fetchFn: async (_input, init) => {
    observed = init;
    return new Promise<Response>((_resolve, reject) => {
      if (init?.signal?.aborted) reject(init.signal.reason);
      else init?.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
    });
  } });
  const pending = method === 'list' ? backend.listCheckpoints('t', controller.signal) : method === 'fold' ? backend.getCheckpointBaseline('t', 0, controller.signal) : backend.getCheckpointBaselineFile('t', 0, 'a.ts', controller.signal);
  expect(observed?.signal).toBe(controller.signal);
  expect((observed?.headers as Record<string,string>).authorization).toBe('Bearer test-token'); expect(observed?.redirect).toBe('manual');
  const reason = new Error('baseline cancelled'); controller.abort(reason); await expect(pending).rejects.toBe(reason);
});

test('bounds actual streamed baseline wire bytes even with missing or lying length and cancels overflow', async () => {
  for (const length of [undefined, '1', String(5*1024*1024)]) {
    let cancelled=false;
    const body=new ReadableStream<Uint8Array>({start(controller) {controller.enqueue(new Uint8Array(4*1024*1024+1));}, cancel(){cancelled=true;}});
    const backend=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(body,{headers:length ? {'content-length':length}: {}})});
    await expect(backend.listCheckpoints('t')).rejects.toThrow(/too large/); expect(cancelled).toBe(true);
  }
});
test('accepts the exact metadata wire limit, refuses invalid UTF8 and does not parse backend failure bodies', async () => {
  const json=JSON.stringify({checkpoints:[]}), limit=4*1024*1024;
  const exact=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(json+' '.repeat(limit-json.length))});
  expect(await exact.listCheckpoints('t')).toEqual([]);
  const invalid=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(new Uint8Array([255]))});
  await expect(invalid.listCheckpoints('t')).rejects.toThrow();
  let cancelled=false; const error=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(new ReadableStream({cancel(){cancelled=true;}}),{status:404})});
  await expect(error.listCheckpoints('t')).rejects.toThrow(/unavailable \(404\)/);expect(cancelled).toBe(true);
});
test('bounds the file wire before decoding, independently of its captured-size claim', async () => {
  let cancelled=false; const oversized=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new Uint8Array(2*1024*1024+1));},cancel(){cancelled=true;}}))});
  await expect(oversized.getCheckpointBaselineFile('t',0,'a.ts')).rejects.toThrow(/too large/);expect(cancelled).toBe(true);
  await expect(client({path:'a.ts',from_seq:0,state:'captured',size:1024*1024+1,content_base64:''}).getCheckpointBaselineFile('t',0,'a.ts')).rejects.toThrow(/1 MiB/);
});

test('rejects a false small captured-size claim before base64 decode and accepts fragmented UTF8 JSON', async () => {
  await expect(client({path:'a.ts',from_seq:0,state:'captured',size:1,content_base64:'AAAA'.repeat(350000)}).getCheckpointBaselineFile('t',0,'a.ts')).rejects.toThrow(/1 MiB/);
  const bytes=new TextEncoder().encode(JSON.stringify({checkpoints:[{seq:0,anchor_message_id:'message-é',turn_id:'t',created_at:'now',files:[]}]}));
  const backend=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async () => new Response(new ReadableStream<Uint8Array>({start(controller){for(const value of bytes) {controller.enqueue(new Uint8Array(0));controller.enqueue(new Uint8Array([value]));}controller.close();}}))});
  expect((await backend.listCheckpoints('t'))[0]?.anchorMessageId).toBe('message-é');
});
