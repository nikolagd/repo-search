const CSRF_COOKIE_NAME = "repo_search_admin_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

function readCookie(name) {
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`));

  if (!cookie) {
    return null;
  }

  return decodeURIComponent(cookie.slice(name.length + 1));
}

function buildHeaders(options) {
  const method = (options?.method || "GET").toUpperCase();
  const headers = {
    "Content-Type": "application/json",
    ...(options?.headers || {}),
  };

  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);

    if (csrfToken) {
      headers[CSRF_HEADER_NAME] = csrfToken;
    }
  }

  return headers;
}

export async function fetchJson(url, options) {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: buildHeaders(options),
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }

  return payload;
}
