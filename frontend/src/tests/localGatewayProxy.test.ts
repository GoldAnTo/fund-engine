// @vitest-environment node
import { createServer as createHttpServer, request, Server } from "node:http";
import { once } from "node:events";
import { createServer, preview, type ViteDevServer } from "vite";
import { afterEach, describe, expect, it } from "vitest";
import { localGatewayProxy } from "../../server/localGatewayProxy";

const token = "test-server-only-identity";
const conversation = "590c0380-a31b-4402-a3d2-e22f8c823af8";
const runReadingPath = `/api/v1/research-conversations/${conversation}/runs/4001171b-f697-4085-b111-2a49935dee93`;
const servers: Server[] = [];
const vites: ViteDevServer[] = [];

afterEach(async () => {
  await Promise.all(vites.splice(0).map((vite) => vite.close()));
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => {
    server.closeAllConnections();
    server.close(() => resolve());
  })));
});

async function start(secret: string | undefined = token) {
  const received: { url: string; authorization: string | undefined; idempotencyKey: string | string[] | undefined; body: string }[] = [];
  const upstream = createHttpServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => { body += String(chunk); });
    req.on("end", () => {
      received.push({ url: req.url ?? "", authorization: req.headers.authorization, idempotencyKey: req.headers["idempotency-key"], body });
      if (req.url?.includes("/events")) {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write('event: heartbeat\ndata: {"sequence":1}\n\n');
        // Deliberately stay open: the proxy must deliver the frame immediately.
        return;
      }
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ subject_id: "researcher", ok: true }));
    });
  });
  servers.push(upstream);
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const address = upstream.address();
  if (!address || typeof address === "string") throw new Error("Missing upstream port");
  const vite = await createServer({
    configFile: false,
    root: process.cwd(),
    logLevel: "silent",
    plugins: [localGatewayProxy({ target: `http://127.0.0.1:${address.port}`, token: secret })],
    server: { host: "127.0.0.1", port: 0, watch: null },
  });
  vites.push(vite);
  await vite.listen();
  const listenAddress = vite.httpServer?.address();
  if (!listenAddress || typeof listenAddress === "string") throw new Error("Missing frontend port");
  const port = listenAddress.port;
  async function call(path: string, headers: Record<string, string | undefined> = {}, method = "GET", body = "") {
    return new Promise<{ status: number; body: string; headers: unknown }>((resolve, reject) => {
      const req = request({ hostname: "127.0.0.1", port, path, method, headers }, (res) => {
        let responseBody = "";
        res.on("data", (chunk) => { responseBody += String(chunk); });
        res.on("end", () => resolve({ status: res.statusCode ?? 0, body: responseBody, headers: res.headers }));
      });
      req.on("error", reject);
      req.end(body);
    });
  }
  return { call, received, port };
}

describe("local server-owned Gateway identity", () => {
  it.each([
    '/api/v1/company-studies',
    `/api/v1/company-studies/${conversation}`,
    `${runReadingPath}/research`,
    `${runReadingPath}/evidence/${conversation}`,
    `${runReadingPath}/tasks/${conversation}/trace`,
    `${runReadingPath}/team`,
  ])("authenticates the exact private reading route %s without a browser token", async (path) => {
    const { call, received } = await start();
    const response = await call(path);
    expect(response.status).toBe(200);
    expect(received[0]).toMatchObject({ url: path, authorization: `Bearer ${token}` });
    expect(JSON.stringify(response)).not.toContain(token);
  });

  it.each(['',`/${conversation}/activities`,`/${conversation}/links`,`/${conversation}/monitor`,`/${conversation}/revisions`,`/${conversation}/activities/${conversation}/retry`])('permits scoped company study writes %s',async(suffix)=>{
    const {call,received}=await start();const path=`/api/v1/company-studies${suffix}`;
    expect((await call(path,{'Content-Type':'application/json','Idempotency-Key':'study-write'},'POST','{}')).status).toBe(200);
    expect(received[0]?.idempotencyKey).toBe('study-write');
    expect((await call(path,{},'DELETE')).status).toBe(404);
  });

  it.each(["messages", "commands", "reviews"])("forwards the exact professional team %s write with its idempotency key", async (action) => {
    const { call, received } = await start();
    const path = `${runReadingPath}/team/${action}`;
    const body = JSON.stringify({ expected_revision: 3, text: "研究补充" });
    const response = await call(path, {
      "Content-Type": "application/json", "Idempotency-Key": "team-retry-key",
      Authorization: "Bearer browser-chosen-actor",
    }, "POST", body);
    expect(response.status).toBe(200);
    expect(received[0]).toMatchObject({ url: path, body, idempotencyKey: "team-retry-key", authorization: `Bearer ${token}` });
  });

  it.each([
    [`${runReadingPath}/research`, "POST"],
    [`${runReadingPath}/research`, "DELETE"],
    [`${runReadingPath}/evidence/${conversation}`, "POST"],
    [`${runReadingPath}/tasks/${conversation}/trace`, "POST"],
    [`${runReadingPath}/evidence/not-a-uuid`, "GET"],
    [`${runReadingPath}/tasks/${conversation}/payload`, "GET"],
    [`${runReadingPath}/evidence/${conversation}/raw`, "GET"],
    [`${runReadingPath}/research/../../documents`, "GET"],
    [`${runReadingPath}/team`, "POST"],
    [`${runReadingPath}/team/messages`, "GET"],
    [`${runReadingPath}/team/commands`, "DELETE"],
    [`${runReadingPath}/team/reviews/${conversation}`, "POST"],
    [`${runReadingPath}/team/arbitrary`, "POST"],
  ])("does not expand private reading into arbitrary routes or writes %s %s", async (path, method) => {
    const { call, received } = await start();
    expect((await call(path, {}, method)).status).toBe(404);
    expect(received).toHaveLength(0);
  });

  it("automatically authenticates repeated same-origin bootstrap reads without returning its token", async () => {
    const { call, received, port } = await start();
    for (let i = 0; i < 2; i++) {
      const response = await call("/api/v1/research-session", { Origin: `http://localhost:${port}`, Host: `localhost:${port}`, "Sec-Fetch-Site": "same-origin" });
      expect(response.status).toBe(200);
      expect(response.body).toContain("researcher");
      expect(JSON.stringify(response)).not.toContain(token);
    }
    expect(received.map((req) => req.authorization)).toEqual([`Bearer ${token}`, `Bearer ${token}`]);
  });

  it("uses only the server identity and preserves a write's body and idempotency semantics", async () => {
    const { call, received, port } = await start();
    const response = await call("/api/v1/research-conversations", {
      Authorization: "Bearer browser-chosen-actor", Origin: `http://127.0.0.1:${port}`,
      "Content-Type": "application/json", "Idempotency-Key": "same-retry-key",
    }, "POST", '{"initial_message":"研究半导体"}');
    expect(response.status).toBe(200);
    expect(received[0]).toMatchObject({ authorization: `Bearer ${token}`, idempotencyKey: "same-retry-key", body: '{"initial_message":"研究半导体"}' });
  });

  it.each([
    { Origin: "https://untrusted.example" },
    { Origin: "null" },
    { "Sec-Fetch-Site": "cross-site" },
    { "Sec-Fetch-Site": "same-site" },
    { Host: "untrusted.example" },
    { Host: "127.0.0.1:1" },
  ])("rejects untrusted browser context before forwarding: %j", async (headers) => {
    const { call, received } = await start();
    expect((await call("/api/v1/research-session", headers)).status).toBe(403);
    expect(received).toHaveLength(0);
  });

  it.each([
    ["/api/v1/jobs", "GET"], ["/api/v1/research-session", "POST"],
    ["/api/v1/research-conversations", "DELETE"],
    ["/api/v1/research-conversations/../jobs", "GET"],
    ["/api/v1/research-conversations/%2e%2e/jobs", "GET"],
    ["/api/v1/research-session/", "GET"],
  ])("does not lend identity to an unsupported route %s %s", async (path, method) => {
    const { call, received } = await start();
    expect((await call(path, {}, method)).status).toBe(404);
    expect(received).toHaveLength(0);
  });

  it("fails closed without configured identity, even when the browser supplies a bearer", async () => {
    const { call, received } = await start("");
    const response = await call("/api/v1/research-session", { Authorization: "Bearer browser-chosen-actor" });
    expect(response.status).toBe(503);
    expect(response.body).toContain("local_identity_unavailable");
    expect(received).toHaveLength(0);
  });

  it.each(["https://example.com", "http://localhost:8018", "http://127.0.0.1:8018/private", "http://user:password@127.0.0.1:8018", "http://127.0.0.1:8018/?token=x"])("rejects unsafe/nonliteral upstream %s", (target) => {
    expect(() => localGatewayProxy({ target, token })).toThrow("loopback");
  });

  it("refuses a network-exposed development listener", async () => {
    await expect(createServer({
      configFile: false, logLevel: "silent",
      plugins: [localGatewayProxy({ target: "http://127.0.0.1:8018", token })],
      server: { host: "0.0.0.0" },
    })).rejects.toThrow("loopback-only");
  });

  it("does not expose the development API bridge through build preview", async () => {
    const app = await preview({
      configFile: false, logLevel: "silent",
      plugins: [localGatewayProxy({ target: "http://127.0.0.1:1", token })],
      server: { host: "127.0.0.1" }, preview: { host: "127.0.0.1", port: 0 },
    });
    if (!(app.httpServer instanceof Server)) throw new Error("Expected HTTP preview server");
    servers.push(app.httpServer);
    const address = app.httpServer.address();
    if (!address || typeof address === "string") throw new Error("Missing preview port");
    const response = await fetch(`http://127.0.0.1:${address.port}/api/v1/research-session`);
    expect(response.status).toBe(503);
    expect(await response.text()).toContain("local_identity_unavailable");
    expect(app.config.preview.proxy).toEqual({});
  });

  it("delivers an SSE frame before the upstream stream ends", async () => {
    const { port, received } = await start();
    const firstFrame = await new Promise<string>((resolve, reject) => {
      const req = request({ hostname: "127.0.0.1", port, path: `/api/v1/research-conversations/${conversation}/events?after_sequence=0` }, (res) => {
        res.once("data", (chunk) => { resolve(String(chunk)); res.destroy(); req.destroy(); });
      });
      req.on("error", reject);
      req.end();
    });
    expect(firstFrame).toContain("event: heartbeat");
    expect(received[0]?.authorization).toBe(`Bearer ${token}`);
  });
});
