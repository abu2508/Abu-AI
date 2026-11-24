#!/usr/bin/env node
/**
 * Redirect canary (M8.2). TEMPORARY, LAN-ONLY, FAKE CREDENTIALS ONLY.
 *
 * Purpose: find out, on a physical iPhone running Expo Go, whether the app's network transport follows a cross-origin
 * redirect and re-sends the request (headers AND body) to the second origin. It never touches the Abu AI backend and
 * never carries a real credential: the app sends fake markers (CANARY-FAKE-BEARER..., CANARY-FAKE-ENROLL...,
 * CANARY-FAKE-REFRESH...).
 *
 * Two origins on ONE private LAN address (different ports are different origins):
 *   A  http://<lan-ip>:8801   answers /redir/301|302|307|308 with a redirect to B
 *   B  http://<lan-ip>:8802   records everything it receives; ANY request reaching B means a redirect was followed
 *
 * Safety rules enforced here (see docs/mobile/REDIRECT_CANARY.md):
 *   - binds ONLY to a private IPv4 address (10/8, 172.16/12, 192.168/16): never 0.0.0.0, never a public address
 *   - refuses connections from non-private peers
 *   - never exposed through a Codespaces or any public port (do not forward these ports)
 *
 * Usage:  node scripts/redirect-canary.js [--host <private-ip>] [--port-a 8801] [--port-b 8802]
 */
'use strict';
const http = require('node:http');
const os = require('node:os');

const STATUSES = [301, 302, 307, 308];
const MARKERS = { bearer: 'CANARY-FAKE-BEARER', enroll: 'CANARY-FAKE-ENROLL', refresh: 'CANARY-FAKE-REFRESH' };

function isPrivateIPv4(ip) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  if ([m[1], m[2], m[3], m[4]].some((x) => Number(x) > 255)) return false;
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function privateAddresses() {
  const out = [];
  for (const [name, list] of Object.entries(os.networkInterfaces())) {
    for (const i of list ?? [])
      if (i.family === 'IPv4' && !i.internal && isPrivateIPv4(i.address)) out.push({ name, address: i.address });
  }
  return out;
}

const peerAllowed = (addr, host, allowLoopback) => {
  const ip = String(addr ?? '').replace(/^::ffff:/, '');
  return isPrivateIPv4(ip) || (allowLoopback && (ip === '127.0.0.1' || ip === host));
};

/**
 * @param {{ host?: string, portA?: number, portB?: number, allowLoopback?: boolean, log?: (message: string) => void }} [options]
 */
function startCanary({ host = '', portA = 8801, portB = 8802, allowLoopback = false, log = () => undefined } = {}) {
  const loopbackOk = allowLoopback && host === '127.0.0.1';
  if (!loopbackOk && !isPrivateIPv4(host))
    throw new Error(`refusing to bind to "${host}": the canary binds only to a private LAN IPv4 address`);
  // real ports are known only after listening (tests pass port 0)
  let aOrigin = `http://${host}:${portA}`;
  let bOrigin = `http://${host}:${portB}`;
  const state = { a: {}, b: [], startedAt: new Date().toISOString() };

  const readBody = (req) =>
    new Promise((resolve) => {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      req.on('error', () => resolve(''));
    });

  const guard = (req, res) => {
    if (!peerAllowed(req.socket.remoteAddress, host, allowLoopback)) {
      res.writeHead(403).end();
      return false;
    }
    return true;
  };

  const a = http.createServer(async (req, res) => {
    if (!guard(req, res)) return;
    const url = new URL(req.url ?? '/', aOrigin);
    const body = await readBody(req);
    const redir = /^\/redir\/(\d{3})$/.exec(url.pathname);
    if (redir && STATUSES.includes(Number(redir[1]))) {
      const status = Number(redir[1]);
      state.a[status] = {
        method: req.method,
        sawAuthorization: Boolean(req.headers.authorization),
        sawBody: body.length > 0,
        at: new Date().toISOString(),
      };
      log(
        `A  ${req.method} /redir/${status} (authorization:${Boolean(req.headers.authorization)} body:${body.length}b) -> redirect to B`,
      );
      res.writeHead(status, { location: `${bOrigin}/collect/${status}` }).end();
      return;
    }
    if (url.pathname === '/report') {
      res
        .writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' })
        .end(JSON.stringify(report(state)));
      return;
    }
    if (url.pathname === '/reset' && req.method === 'POST') {
      state.a = {};
      state.b = [];
      res.writeHead(204).end();
      return;
    }
    res
      .writeHead(200, { 'content-type': 'text/plain' })
      .end('Abu AI redirect canary (fake credentials only). GET /report for results.\n');
  });

  const b = http.createServer(async (req, res) => {
    if (!guard(req, res)) return;
    const body = await readBody(req);
    const status = Number(/^\/collect\/(\d{3})$/.exec(req.url ?? '')?.[1] ?? 0);
    const entry = {
      status,
      method: req.method,
      authorization: req.headers.authorization ? 'present' : 'absent',
      leakedBearer: String(req.headers.authorization ?? '').includes(MARKERS.bearer),
      leakedEnroll: body.includes(MARKERS.enroll),
      leakedRefresh: body.includes(MARKERS.refresh),
      bodyBytes: body.length,
      at: new Date().toISOString(),
    };
    state.b.push(entry);
    log(
      `B  RECEIVED ${req.method} ${req.url}  authorization:${entry.authorization} bearer:${entry.leakedBearer} enroll:${entry.leakedEnroll} refresh:${entry.leakedRefresh}  <-- a redirect was FOLLOWED`,
    );
    res.writeHead(200, { 'content-type': 'application/json' }).end('{"leaked":true}');
  });

  return new Promise((resolve, reject) => {
    a.once('error', reject);
    b.once('error', reject);
    a.listen(portA, host, () =>
      b.listen(portB, host, () => {
        aOrigin = `http://${host}:${a.address().port}`;
        bOrigin = `http://${host}:${b.address().port}`;
        const close = () => Promise.all([a, b].map((s) => new Promise((r) => s.close(() => r()))));
        resolve({
          aOrigin,
          bOrigin,
          state,
          close,
          portA: a.address().port,
          portB: b.address().port,
          report: () => report(state),
        });
      }),
    );
  });
}

/** Per-status verdict. PASS: A was reached and B received nothing. FAIL: B received a request. INCONCLUSIVE: A never saw it. */
function report(state) {
  const results = {};
  for (const s of STATUSES) {
    const reachedA = Boolean(state.a[s]);
    const toB = state.b.filter((x) => x.status === s);
    results[s] = {
      reachedA,
      bReceived: toB.length > 0,
      leakedBearer: toB.some((x) => x.leakedBearer),
      leakedEnroll: toB.some((x) => x.leakedEnroll),
      leakedRefresh: toB.some((x) => x.leakedRefresh),
      verdict: toB.length > 0 ? 'FAIL' : reachedA ? 'PASS' : 'INCONCLUSIVE',
    };
  }
  const verdicts = Object.values(results).map((r) => r.verdict);
  const overall = verdicts.includes('FAIL') ? 'FAIL' : verdicts.every((v) => v === 'PASS') ? 'PASS' : 'INCONCLUSIVE';
  return { overall, results, bRequests: state.b.length };
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  return out;
}

if (require.main === module) {
  const args = parseArgs(process.argv.slice(2));
  const candidates = privateAddresses();
  const host = args.host ?? candidates[0]?.address;
  if (!host) {
    console.error(
      'No private LAN IPv4 address found. Connect to your home/office Wi-Fi (a PRIVATE network) and retry.',
    );
    process.exit(1);
  }
  if (args.host && !candidates.some((c) => c.address === host)) {
    console.error(
      `${host} is not a private LAN address of this machine (candidates: ${candidates.map((c) => c.address).join(', ') || 'none'}).`,
    );
    process.exit(1);
  }
  const portA = Number(args['port-a'] ?? 8801);
  const portB = Number(args['port-b'] ?? 8802);
  startCanary({ host, portA, portB, log: (m) => console.log(`[${new Date().toISOString()}] ${m}`) })
    .then((c) => {
      console.log('\nAbu AI redirect canary: LAN only, fake credentials only. Stop with Ctrl+C when finished.');
      console.log(`  Enter this in Expo Go (Settings > Transport canary):  ${c.aOrigin}`);
      console.log(`  Second origin (should receive NOTHING):                ${c.bOrigin}`);
      console.log('  Do NOT forward these ports or expose them publicly.\n');
      let last = '';
      setInterval(() => {
        const r = c.report();
        const done = STATUSES.every((s) => r.results[s].reachedA || r.results[s].bReceived);
        const line = JSON.stringify(r.results);
        if (done && line !== last) {
          last = line;
          console.log(`\n=== RESULT: ${r.overall} ===`);
          for (const s of STATUSES)
            console.log(
              `  ${s}: ${r.results[s].verdict}  (reachedA=${r.results[s].reachedA}, bReceived=${r.results[s].bReceived})`,
            );
          console.log('');
        }
      }, 1000).unref?.();
    })
    .catch((e) => {
      console.error(String(e.message ?? e));
      process.exit(1);
    });
}

module.exports = { startCanary, report, isPrivateIPv4, privateAddresses, peerAllowed, STATUSES, MARKERS };
