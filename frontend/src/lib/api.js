// Relative — Vite's dev server proxies /api to the backend (see
// vite.config.js), so this works both on localhost and through a tunnel.
const BASE = '';

export function getUserId() {
  let id = localStorage.getItem('impulse_user_id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('impulse_user_id', id);
  }
  return id;
}

async function json(res) {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function sendChat(userId, message, signal, onEvent) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, message }),
    signal,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = raw.startsWith('data: ') ? raw.slice(6) : raw;
      if (!line) continue;
      try {
        onEvent(JSON.parse(line));
      } catch {
        // ignore malformed/partial event
      }
    }
  }
}

export function fetchHistory(userId) {
  return fetch(`${BASE}/api/history/${userId}`).then(json);
}

export function fetchMemories(userId) {
  return fetch(`${BASE}/api/memories/${userId}`).then(json);
}

export function fetchSandbox(userId) {
  return fetch(`${BASE}/api/sandbox/${userId}`).then(json);
}
