#!/usr/bin/env node
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";


const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const stackScript = process.env.LIVE_STACK_SCRIPT?.trim()
  || path.join(root, "scripts", "research-stack.sh");
const token = process.env.LIVE_CONTROL_TOKEN?.trim();
const port = Number(process.env.LIVE_CONTROL_PORT ?? "8899");
const commandTimeout = Number(process.env.LIVE_CONTROL_COMMAND_TIMEOUT_MS ?? "240000");
if (!token || token.length < 24) throw new Error("LIVE_CONTROL_TOKEN must contain at least 24 characters");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("LIVE_CONTROL_PORT is invalid");
if (!Number.isFinite(commandTimeout) || commandTimeout < 50) throw new Error("LIVE_CONTROL_COMMAND_TIMEOUT_MS is invalid");

const commands = new Map([
  ["api", "restart-api"],
  ["acquisition-worker", "crash-acquisition-worker"],
  ["research-worker", "crash-research-worker"],
  ["scheduler", "crash-scheduler"],
]);
const active = new Set();

async function containerMetadata(service) {
  try {
    const envFile = process.env.COMPOSE_ENV_FILE
      || path.join(root, ".env.compose");
    const composeArgs = [
      "compose",
      "--env-file", envFile,
      "-f", path.join(root, "docker-compose.yml"),
      "-f", path.join(root, "docker-compose.live.yml"),
      "ps", "--all", "-q", service,
    ];
    const { stdout } = await execute("docker", composeArgs, {
      cwd: root,
      env: process.env,
      timeout: 30_000,
    });
    const containerIds = stdout.trim().split(/\s+/).filter(Boolean);
    if (!containerIds.length) return null;
    const instances = await Promise.all(containerIds.map(async (containerId) => {
      const inspected = await execute(
        "docker",
        ["inspect", "--format", "{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}", containerId],
        { cwd: root, env: process.env, timeout: 30_000 },
      );
      const [id, startedAt, restartCount] = inspected.stdout.trim().split("|");
      return { id, started_at: startedAt, restart_count: Number(restartCount) };
    }));
    instances.sort((left, right) => left.id.localeCompare(right.id));
    return {
      ...instances[0],
      started_at: instances.reduce(
        (earliest, instance) => instance.started_at < earliest ? instance.started_at : earliest,
        instances[0].started_at,
      ),
      restart_count: instances.reduce((total, instance) => total + instance.restart_count, 0),
      instances,
    };
  } catch {
    return null;
  }
}

function respond(response, status, body) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    respond(response, 200, { status: "ready", project: process.env.COMPOSE_PROJECT_NAME ?? null });
    return;
  }
  const match = request.url?.match(/^\/(restart|stop)\/(api|acquisition-worker|research-worker|scheduler)$/);
  if (request.method !== "POST" || !match) {
    respond(response, 404, { error: "unknown live-control operation" });
    return;
  }
  if (request.headers.authorization !== `Bearer ${token}`) {
    respond(response, 401, { error: "invalid live-control credential" });
    return;
  }
  const operation = match[1];
  const service = match[2];
  if (operation === "stop" && service !== "scheduler") {
    respond(response, 404, { error: "unknown live-control operation" });
    return;
  }
  if (active.has(service)) {
    respond(response, 409, { error: `${service} restart is already running` });
    return;
  }
  active.add(service);
  const requestedAt = new Date().toISOString();
  const before = await containerMetadata(service);
  try {
    const command = operation === "stop" ? "stop-scheduler" : commands.get(service);
    await execute("bash", [stackScript, command], {
      cwd: root,
      env: process.env,
      timeout: commandTimeout,
      maxBuffer: 1024 * 1024,
    });
    const after = await containerMetadata(service);
    respond(response, 200, {
      service,
      operation,
      requested_at: requestedAt,
      completed_at: new Date().toISOString(),
      result: operation === "stop" ? "stopped" : "container_healthy_after_restart",
      before,
      after,
    });
  } catch (error) {
    process.stderr.write(
      `${service} restart failed: ${error instanceof Error ? error.message : "unknown error"}\n`,
    );
    respond(response, 500, {
      service,
      operation,
      requested_at: requestedAt,
      failed_at: new Date().toISOString(),
      error: "restart failed; inspect the server-side live-control log",
    });
  } finally {
    active.delete(service);
  }
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`live stack control ready on http://127.0.0.1:${port}\n`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
