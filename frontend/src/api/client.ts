const CSRF_COOKIE_NAME = "repo_search_admin_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

function readCookie(name: string): string | null {
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`));

  if (!cookie) {
    return null;
  }

  return decodeURIComponent(cookie.slice(name.length + 1));
}

function buildHeaders(options?: RequestInit): Headers {
  const method = (options?.method || "GET").toUpperCase();
  const headers = new Headers(options?.headers);

  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);

    if (csrfToken) {
      headers.set(CSRF_HEADER_NAME, csrfToken);
    }
  }

  return headers;
}

export function getErrorMessage(error: unknown, fallback = "Request failed"): string {
  return error instanceof Error ? error.message : fallback;
}

export async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: buildHeaders(options),
  });

  const payload: unknown = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String(payload.detail)
      : "Request failed";

    throw new Error(detail);
  }

  return payload as T;
}
