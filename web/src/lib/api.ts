// Client for the local Puffin API (puffin/web/server.py). The token arrives in the URL fragment
// (#token=...), which browsers never send to a server; it is sent back as a bearer header.
import type { Issue } from "./types";

const TOKEN_KEY = "puffin-token";

function readToken(): string {
  const match = /(?:^#|&)token=([^&]+)/.exec(location.hash);
  if (match) {
    const token = decodeURIComponent(match[1]);
    try {
      sessionStorage.setItem(TOKEN_KEY, token); // survives a reload of this tab only
    } catch {
      /* storage may be unavailable; the token still works for this page */
    }
    history.replaceState(null, "", location.pathname + location.search); // drop it from the bar
    return token;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

const token = readToken();

export class ApiError extends Error {
  constructor(
    public status: number,
    public issues: Issue[],
  ) {
    super(issues[0]?.message ?? `request failed (${status})`);
  }
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const issues: Issue[] = data.errors ?? [
      { severity: "error", code: "http", where: path, message: data.error ?? response.statusText, hint: "" },
    ];
    throw new ApiError(response.status, issues);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
};

export type ChangeEvent = { type: "project" | "schematic" | "state" | "checkpoint" };

export const hasToken = token !== "";

/** Server-sent events over fetch (EventSource cannot send the bearer header). Reconnects,
 *  except after a 401: a wrong or missing token does not fix itself. */
export function listen(onChange: (event: ChangeEvent) => void, onStatus: (live: boolean) => void): () => void {
  let stopped = false;
  let controller: AbortController | null = null;

  async function connect() {
    while (!stopped) {
      controller = new AbortController();
      try {
        const response = await fetch("/api/events", {
          headers: { authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        if (response.status === 401) {
          stopped = true; // a wrong or missing token will not fix itself by retrying
          break;
        }
        if (!response.ok || !response.body) throw new Error(String(response.status));
        onStatus(true);
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          let end: number;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            const event = /^event: (\w+)/m.exec(block)?.[1];
            const data = /^data: (.*)$/m.exec(block)?.[1];
            if (event === "change" && data) onChange(JSON.parse(data) as ChangeEvent);
          }
        }
      } catch {
        /* fall through to reconnect */
      }
      onStatus(false);
      if (!stopped) await new Promise((r) => setTimeout(r, 1500));
    }
  }

  void connect();
  return () => {
    stopped = true;
    controller?.abort();
  };
}
