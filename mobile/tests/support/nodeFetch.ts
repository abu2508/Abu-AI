import http from 'node:http';

/** Minimal real-HTTP transport for the local contract tests (Jest's own fetch is a React Native polyfill). Never follows redirects. */
export function nodeFetch(url: string, init: RequestInit = {}): Promise<Response> {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const body = typeof init.body === 'string' ? init.body : undefined;
    const req = http.request(
      {
        host: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        method: init.method ?? 'GET',
        headers: {
          ...(init.headers as Record<string, string>),
          ...(body ? { 'Content-Length': String(Buffer.byteLength(body)) } : {}),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          const status = res.statusCode ?? 0;
          resolve({
            ok: status >= 200 && status < 300,
            status,
            url,
            type: 'basic',
            headers: { get: (k: string) => (res.headers[k.toLowerCase()] as string | undefined) ?? null },
            json: () => (text ? Promise.resolve(JSON.parse(text) as unknown) : Promise.reject(new Error('empty body'))),
          } as unknown as Response);
        });
      },
    );
    req.on('error', reject);
    init.signal?.addEventListener('abort', () =>
      req.destroy(Object.assign(new Error('aborted'), { name: 'AbortError' })),
    );
    if (body) req.write(body);
    req.end();
  });
}
