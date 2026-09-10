import type { IncomingMessage } from "node:http";
import type { Plugin } from "vite";

const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const conversation = `/api/v1/research-conversations/${uuid}`;
const study = `/api/v1/company-studies/${uuid}`;
const runReadRoute = `${conversation}/runs/${uuid}/(?:research|evidence/${uuid}|tasks/${uuid}/trace|team)`;
const readRoute = new RegExp(`^(?:/api/v1/(?:research-session|health|research-conversations|company-studies)|${study}|${conversation}(?:/(?:snapshot|events))?|${runReadRoute})$`);
const writeRoute = new RegExp(`^(?:/api/v1/(?:research-conversations|company-studies)|${study}/(?:activities|links|revisions|monitor|activities/${uuid}/retry)|${conversation}/messages|${conversation}/runs/${uuid}/(?:commands|team/(?:messages|commands|reviews)))$`);

function isLoopback(address: string | undefined): boolean {
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

function trustedLocalRequest(req: IncomingMessage): boolean {
  if (!isLoopback(req.socket.localAddress) || !isLoopback(req.socket.remoteAddress)) return false;
  const port = req.socket.localPort;
  const host = req.headers.host;
  if (!port || !host || ![`localhost:${port}`, `127.0.0.1:${port}`, `[::1]:${port}`].includes(host)) return false;
  const origin = req.headers.origin;
  if (origin !== undefined && origin !== `http://${host}`) return false;
  const site = req.headers["sec-fetch-site"];
  return site === undefined || site === "same-origin" || site === "none";
}

/** Development only: the local OS user is the trust boundary, not a login service. */
export function localGatewayProxy({ target, token }: { target: string; token?: string }): Plugin {
  const upstream = new URL(target);
  // Literal loopback prevents DNS changes from sending the credential elsewhere.
  if (upstream.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(upstream.hostname)
      || upstream.username || upstream.password || upstream.pathname !== "/" || upstream.search || upstream.hash) {
    throw new Error("Local Gateway target must be a literal loopback HTTP origin");
  }
  const secret = token?.trim();
  if (secret && /[\r\n]/.test(secret)) throw new Error("Invalid local Gateway identity configuration");

  return {
    name: "fundclaw-local-gateway-identity",
    apply: "serve",
    config() {
      return {
        preview: { proxy: {} },
        server: {
          proxy: {
            "/api": { target: upstream.origin, changeOrigin: true, followRedirects: false, ws: false },
          },
        },
      };
    },
    configResolved(config) {
      if (!["127.0.0.1", "localhost", "::1"].includes(String(config.server.host)) || config.server.https) {
        throw new Error("Local Gateway identity requires a loopback-only HTTP development server");
      }
    },
    configurePreviewServer(server) {
      // Vite otherwise inherits server.proxy but does not run configureServer.
      // Static build preview is intentionally not an authentication service.
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api")) return next();
        res.statusCode = 503;
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        res.end(JSON.stringify({ detail: { code: "local_identity_unavailable" } }));
      });
    },
    configureServer(server) {
      // Installed before Vite's proxy. Failures never reach the upstream.
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api")) return next();
        delete req.headers.authorization;
        const deny = (status: number, code: string) => {
          res.statusCode = status;
          res.setHeader("Content-Type", "application/json");
          res.setHeader("Cache-Control", "no-store");
          res.end(JSON.stringify({ detail: { code } }));
        };
        if (!trustedLocalRequest(req)) return deny(403, "local_origin_required");
        const pathname = req.url.split("?", 1)[0] ?? "";
        const permitted = req.method === "GET" ? readRoute.test(pathname)
          : req.method === "POST" && writeRoute.test(pathname);
        if (!permitted) return deny(404, "gateway_route_not_available");
        if (!secret) return deny(503, "local_identity_unavailable");
        req.headers.authorization = `Bearer ${secret}`;
        next();
      });
    },
  };
}
