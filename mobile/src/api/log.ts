/** Redacting request log. Only method, a route TEMPLATE (never ids), status and timing are ever recorded. */
export interface LogEntry {
  method: string;
  route: string;
  status: number | 'error';
  ms: number;
  code?: string;
}
export type Logger = (entry: LogEntry) => void;

const SENSITIVE =
  /(authorization|refresh[_-]?token|access[_-]?token|enrollment[_-]?code|idempotency[_-]?key|bearer\s+\S+)/i;

/** Defence in depth: refuse to log anything that looks like it carries a credential. */
export function safeLog(sink: (line: string) => void): Logger {
  return (e) => {
    const line = `${e.method} ${e.route} -> ${e.status} (${e.ms}ms)${e.code ? ` ${e.code}` : ''}`;
    sink(SENSITIVE.test(line) ? '[redacted]' : line);
  };
}
