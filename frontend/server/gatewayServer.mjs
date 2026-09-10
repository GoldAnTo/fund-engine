/** Built-asset server for a loopback-published, single-principal workstation.
 * The enclosing Compose host port is the local OS trust boundary. This is not
 * an Internet login service and must never be published to other machines.
 */
import http from 'node:http';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const conversation = `/api/v1/research-conversations/${uuid}`;
const study = `/api/v1/company-studies/${uuid}`;
const readRoute = new RegExp(`^(?:/api/v1/(?:research-session|health|research-conversations|company-studies)|${study}|${conversation}(?:/(?:snapshot|events))?|${conversation}/runs/${uuid}/(?:team|research|evidence/${uuid}|tasks/${uuid}/trace))$`);
const writeRoute = new RegExp(`^(?:/api/v1/(?:research-conversations|company-studies)|${study}/(?:activities|links|revisions|monitor|activities/${uuid}/retry)|${conversation}/messages|${conversation}/runs/${uuid}/(?:commands|team/(?:messages|commands|reviews)))$`);
const contentTypes = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.ico': 'image/x-icon' };

function reply(res, status, code) {
  if (res.headersSent) { res.destroy(); return; }
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify({ detail: { code } }));
}

export function createGatewayServer({ target, token, root, publicPort }) {
  const upstream = new URL(target);
  if (upstream.protocol !== 'http:' || !['api', '127.0.0.1', '[::1]'].includes(upstream.hostname)
      || upstream.username || upstream.password || upstream.pathname !== '/' || upstream.search || upstream.hash) {
    throw new Error('Gateway upstream must be the internal API or a literal loopback HTTP origin');
  }
  const secret = token?.trim();
  if (secret && /[\r\n]/.test(secret)) throw new Error('Invalid server identity configuration');
  if (publicPort !== undefined && (!Number.isInteger(publicPort) || publicPort < 1 || publicPort > 65535)) throw new Error('Invalid public port');
  const staticRoot = resolve(root);
  return http.createServer(async (req, res) => {
    res.setHeader('x-content-type-options', 'nosniff');
    res.setHeader('referrer-policy', 'no-referrer');
    const rawPath = (req.url ?? '').split('?', 1)[0];
    if (rawPath === '/health' && req.method === 'GET') { res.end('ok'); return; }
    // Validate the exact browser origin before either static or API service.
    const port = publicPort ?? req.socket.localPort;
    const host = req.headers.host;
    if (![ `localhost:${port}`, `127.0.0.1:${port}`, `[::1]:${port}` ].includes(host)
        || (req.headers.origin !== undefined && req.headers.origin !== `http://${host}`)
        || ![undefined, 'same-origin', 'none'].includes(req.headers['sec-fetch-site'])) {
      reply(res, 403, 'local_origin_required'); return;
    }
    if (rawPath.startsWith('/api')) {
      const permitted = req.method === 'GET' ? readRoute.test(rawPath) : req.method === 'POST' && writeRoute.test(rawPath);
      if (!permitted) { reply(res, 404, 'gateway_route_not_available'); return; }
      if (!secret) { reply(res, 503, 'local_identity_unavailable'); return; }
      const headers = { authorization: `Bearer ${secret}` };
      for (const name of ['accept', 'content-type', 'content-length', 'idempotency-key', 'last-event-id']) {
        if (req.headers[name] !== undefined) headers[name] = req.headers[name];
      }
      if (Number(headers['content-length'] ?? 0) > 1_048_576) { reply(res, 413, 'request_too_large'); return; }
      // No browser Authorization, Cookie, Host or forwarded identity survives.
      const remote = http.request(new URL(req.url, upstream), { method: req.method, headers }, response => {
        if (response.statusCode >= 300 && response.statusCode < 400) {
          response.destroy(); reply(res, 502, 'gateway_upstream_unavailable'); return;
        }
        res.writeHead(response.statusCode ?? 502, {
          'content-type': response.headers['content-type'] ?? 'application/json',
          'cache-control': 'no-store',
          'x-accel-buffering': 'no',
        });
        res.flushHeaders();
        response.on('error', () => res.destroy());
        response.pipe(res);
      });
      remote.on('error', () => reply(res, 502, 'gateway_upstream_unavailable'));
      remote.setTimeout(45_000, () => remote.destroy()); // SSE heartbeats arrive every 15 seconds.
      res.on('close', () => remote.destroy());
      req.on('aborted', () => remote.destroy());
      let bytes = 0;
      req.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 1_048_576) { reply(res, 413, 'request_too_large'); remote.destroy(); }
      });
      req.pipe(remote);
      return;
    }
    if (!['GET', 'HEAD'].includes(req.method)) { reply(res, 404, 'not_found'); return; }
    let pathname;
    try { pathname = decodeURIComponent(rawPath); } catch { reply(res, 404, 'not_found'); return; }
    if (pathname.includes('\\') || pathname.split('/').some(part => part.startsWith('.'))) { reply(res, 404, 'not_found'); return; }
    const file = resolve(staticRoot, `.${pathname}`);
    if (!file.startsWith(staticRoot + sep) && file !== staticRoot) { reply(res, 404, 'not_found'); return; }
    let selected = file;
    try {
      if (!(await stat(selected)).isFile()) selected = resolve(staticRoot, 'index.html');
    } catch {
      if (extname(pathname)) { reply(res, 404, 'not_found'); return; }
      selected = resolve(staticRoot, 'index.html');
    }
    const stream = createReadStream(selected);
    stream.on('error', () => reply(res, 404, 'not_found'));
    res.setHeader('content-type', contentTypes[extname(selected)] ?? 'application/octet-stream');
    res.setHeader('cache-control', 'no-cache');
    if (req.method === 'HEAD') { stream.destroy(); res.end(); } else stream.pipe(res);
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const server = createGatewayServer({
    target: process.env.GATEWAY_API_BASE ?? 'http://api:8000',
    token: process.env.GATEWAY_PROXY_TOKEN,
    root: fileURLToPath(new URL('../dist', import.meta.url)),
    publicPort: Number(process.env.GATEWAY_PUBLIC_PORT ?? 8080),
  });
  server.listen(8080, '0.0.0.0'); // Container only; Compose publishes on 127.0.0.1.
}
