// All backend calls go through here so the API surface is in one place.
// Paths start with /api — Vite proxies them to the Python backend in dev.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`${options.method || "GET"} ${path} failed: ${res.status}`);
  }
  return res.json();
}

export function getHealth() {
  return request("/api/health");
}

export function sendChat(message) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}
