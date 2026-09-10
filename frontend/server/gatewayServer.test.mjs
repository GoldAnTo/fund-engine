import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createGatewayServer } from './gatewayServer.mjs';

async function listen(t, server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}`;
}
async function setup(t, handler, options = {}) {
  const upstream = await listen(t, http.createServer(handler));
  const root = await mkdtemp(join(tmpdir(), 'gateway-static-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(join(root, 'index.html'), '<h1>FundClaw</h1>');
  const server = createGatewayServer({ target: upstream, token: 'server-only-sentinel', root, ...options });
  const origin = await listen(t, server);
  return { origin, server };
}

test('static SPA and health work without returning server identity', async t => {
  const { origin } = await setup(t, () => assert.fail('static request reached API'));
  for (const path of ['/', '/research/example', '/companies/example']) {
    const result = await fetch(origin + path);
    assert.equal(result.status, 200);
    assert.equal(await result.text(), '<h1>FundClaw</h1>');
  }
  assert.equal((await fetch(origin + '/health')).status, 200);
  assert.equal((await fetch(origin + '/.env')).status, 404);
});

test('company studies allow only exact private methods and scoped paths',async t=>{
  const id='22222222-2222-4222-8222-222222222222';const observed=[];
  const {origin}=await setup(t,(req,res)=>{observed.push({path:req.url,key:req.headers['idempotency-key'],auth:req.headers.authorization});res.setHeader('content-type','application/json');res.end('{}');});
  for(const path of ['/api/v1/company-studies',`/api/v1/company-studies/${id}`]) assert.equal((await fetch(origin+path)).status,200);
  for(const suffix of ['',`/${id}/activities`,`/${id}/links`,`/${id}/monitor`,`/${id}/revisions`,`/${id}/activities/${id}/retry`]){
    const result=await fetch(origin+'/api/v1/company-studies'+suffix,{method:'POST',headers:{origin,'content-type':'application/json','idempotency-key':'company-key'},body:'{}'});
    assert.equal(result.status,200);assert.equal(observed.at(-1).key,'company-key');assert.equal(observed.at(-1).auth,'Bearer server-only-sentinel');
  }
  for(const path of [`/api/v1/company-studies/${id}/raw`,`/api/v1/company-studies/not-an-id`,`/api/v1/company-studies/${id}/activities/${id}/payload`])assert.equal((await fetch(origin+path)).status,404);
  assert.equal((await fetch(origin+'/api/v1/company-studies',{method:'DELETE',headers:{origin}})).status,404);
});

test('proxy authenticates only with runtime identity and preserves idempotency', async t => {
  let observed;
  const { origin } = await setup(t, (req, res) => {
    observed = req.headers;
    res.setHeader('content-type', 'application/json');
    res.end('{"accepted":true}');
  });
  const result = await fetch(origin + '/api/v1/research-conversations', {
    method: 'POST', headers: { authorization: 'Bearer attacker', 'idempotency-key': 'same-key', origin: origin, 'content-type': 'application/json' }, body: '{"initial_message":"test"}',
  });
  assert.equal(result.status, 200);
  assert.equal(observed.authorization, 'Bearer server-only-sentinel');
  assert.equal(observed['idempotency-key'], 'same-key');
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.equal((await result.text()).includes('server-only-sentinel'), false);
});

test('deny foreign origin, Host and fetch metadata without contacting upstream', async t => {
  const { origin } = await setup(t, () => assert.fail('forbidden request reached API'));
  for (const headers of [{ origin: 'https://evil.invalid' }, { host: 'evil.invalid' }, { 'sec-fetch-site': 'cross-site' }]) {
    const status = await new Promise((resolve, reject) => {
      const request = http.get(origin + '/api/v1/research-session', { headers }, response => {
        response.resume(); resolve(response.statusCode);
      });
      request.on('error', reject);
    });
    assert.equal(status, 403);
  }
});

test('deny legacy, encoded, unknown paths and unsupported methods', async t => {
  const { origin } = await setup(t, () => assert.fail('forbidden route reached API'));
  for (const path of ['/api/v1/documents', '/api/v1/research-session/extra', '/api/v1/%72esearch-session']) {
    assert.equal((await fetch(origin + path)).status, 404);
  }
  assert.equal((await fetch(origin + '/api/v1/research-conversations', { method: 'DELETE' })).status, 404);
});

test('missing runtime identity fails closed', async t => {
  const { origin } = await setup(t, () => assert.fail('missing identity reached API'), { token: '' });
  const response = await fetch(origin + '/api/v1/research-session');
  assert.equal(response.status, 503);
  assert.match(await response.text(), /local_identity_unavailable/);
});

test('SSE sends a live frame before upstream completes and preserves resume cursor', async t => {
  let seen;
  let resolveClosed;
  const closed = new Promise(resolve => { resolveClosed = resolve; });
  const { origin } = await setup(t, (req, res) => {
    seen = [req.url, req.headers['last-event-id']];
    res.on('close', resolveClosed);
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('id: 8\nevent: role_event\ndata: {}\n\n');
  });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2000);
  t.after(() => { clearTimeout(timeout); controller.abort(); });
  const id = '11111111-1111-1111-1111-111111111111';
  const path = `/api/v1/research-conversations/${id}/events?after_sequence=7`;
  const response = await fetch(origin + path, { signal: controller.signal, headers: { 'last-event-id': '7' } });
  const chunk = await response.body.getReader().read();
  assert.match(new TextDecoder().decode(chunk.value), /id: 8/);
  assert.deepEqual(seen, [path, '7']);
  controller.abort();
  await Promise.race([closed, new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error('upstream did not close')), 1000);
    timer.unref();
  })]);
});

test('upstream redirects and transport failures never disclose credentials', async t => {
  const { origin } = await setup(t, (req, res) => {
    if (req.url.endsWith('research-session')) { res.writeHead(302, { location: 'https://evil.invalid' }); res.end(); }
    else req.socket.destroy();
  });
  for (const path of ['/api/v1/research-session', '/api/v1/research-conversations']) {
    const response = await fetch(origin + path);
    assert.equal(response.status, 502);
    assert.equal(await response.text(), '{"detail":{"code":"gateway_upstream_unavailable"}}');
  }
});

test('reject unsafe server configuration before serving', () => {
  for (const target of ['https://evil.invalid', 'http://api:8000/path', 'http://user:pass@api:8000']) {
    assert.throws(() => createGatewayServer({ target, root: '.', token: 'test' }));
  }
  assert.throws(() => createGatewayServer({ target: 'http://api:8000', root: '.', token: 'bad\r\ntoken' }));
});

test('team reads and writes are restricted to the exact scoped contract', async t => {
  let calls = 0;
  const { origin } = await setup(t, (req, res) => { calls++; res.end('{}'); });
  const id = '11111111-1111-1111-1111-111111111111';
  const team = `/api/v1/research-conversations/${id}/runs/${id}/team`;
  assert.equal((await fetch(origin + team)).status, 200);
  for (const command of ['messages', 'commands', 'reviews']) {
    assert.equal((await fetch(origin + team + '/' + command, { method: 'POST', body: '{}' })).status, 200);
  }
  assert.equal((await fetch(origin + team + '/reviews')).status, 404);
  assert.equal((await fetch(origin + team + '/admin', { method: 'POST' })).status, 404);
  assert.equal(calls, 4);
});


test('oversized request is refused before reaching the provider', async t => {
  const { origin } = await setup(t, () => assert.fail('oversized request reached API'));
  const response = await fetch(origin + '/api/v1/research-conversations', {
    method: 'POST', body: 'x'.repeat(1_048_577),
  });
  assert.equal(response.status, 413);
});
