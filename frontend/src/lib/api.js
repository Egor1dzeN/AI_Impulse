// Relative — Vite's dev server proxies /api to the backend (see
// vite.config.js), so this works both on localhost and through a tunnel.
import { getToken } from './auth';

const BASE = '';

function authHeaders(extra = {}) {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function json(res) {
  if (!res.ok) {
    // FastAPI's HTTPException bodies are {"detail": "..."} — surface that
    // real reason (e.g. "Google Calendar access was revoked — reconnect it
    // in Settings") instead of a bare status code wherever a caller shows
    // the error message to the user.
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON or empty error body — keep the status line
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function sendChat(conversationId, message, signal, onEvent, enableThinking = true) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ conversation_id: conversationId, message, enable_thinking: enableThinking }),
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

export function fetchHistory(conversationId) {
  return fetch(`${BASE}/api/history?conversation_id=${conversationId}`, { headers: authHeaders() }).then(json);
}

export function createConversation() {
  return fetch(`${BASE}/api/conversations`, { method: 'POST', headers: authHeaders() }).then(json);
}

export function fetchConversations() {
  return fetch(`${BASE}/api/conversations`, { headers: authHeaders() }).then(json);
}

export function deleteConversation(conversationId) {
  return fetch(`${BASE}/api/conversations/${conversationId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(json);
}

export function fetchMemories() {
  return fetch(`${BASE}/api/memories`, { headers: authHeaders() }).then(json);
}

export function addMemory(content) {
  return fetch(`${BASE}/api/memories`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ content }),
  }).then(json);
}

export function searchMemories(query) {
  return fetch(`${BASE}/api/memories/search?q=${encodeURIComponent(query)}`, {
    headers: authHeaders(),
  }).then(json);
}

export function dreamMemories() {
  return fetch(`${BASE}/api/memories/dream`, { method: 'POST', headers: authHeaders() }).then(json);
}

export function deleteMemory(memoryId) {
  return fetch(`${BASE}/api/memories/${memoryId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(json);
}

export function fetchSandbox() {
  return fetch(`${BASE}/api/sandbox`, { headers: authHeaders() }).then(json);
}

export function fetchFinanceTransactions() {
  return fetch(`${BASE}/api/finance/transactions`, { headers: authHeaders() }).then(json);
}

export function fetchFinanceSummary() {
  return fetch(`${BASE}/api/finance/summary`, { headers: authHeaders() }).then(json);
}

export function fetchSettings() {
  return fetch(`${BASE}/api/settings`, { headers: authHeaders() }).then(json);
}

export function updateSettings(payload) {
  return fetch(`${BASE}/api/settings`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  }).then(json);
}

// Returns the Google consent-screen URL; the caller does a full page
// navigation to it (window.location.href), not a fetch — the OAuth
// redirect dance needs the browser itself, not our SPA's XHR context.
export function getGoogleCalendarConnectUrl() {
  return fetch(`${BASE}/api/google-calendar/connect`, { headers: authHeaders() }).then(json);
}

export function disconnectGoogleCalendar() {
  return fetch(`${BASE}/api/google-calendar/disconnect`, {
    method: 'POST',
    headers: authHeaders(),
  }).then(json);
}

export function getGmailConnectUrl() {
  return fetch(`${BASE}/api/gmail/connect`, { headers: authHeaders() }).then(json);
}

export function disconnectGmail() {
  return fetch(`${BASE}/api/gmail/disconnect`, {
    method: 'POST',
    headers: authHeaders(),
  }).then(json);
}

export function importData({ file, source, replace, syncCalendar }) {
  const form = new FormData();
  form.append('file', file);
  // Omitted source = auto-detect on the backend (FormData would otherwise
  // send the literal string "undefined").
  if (source) form.append('source', source);
  form.append('replace', replace ? 'true' : 'false');
  form.append('sync_calendar', syncCalendar ? 'true' : 'false');
  // No Content-Type header here either — see transcribeAudio above.
  return fetch(`${BASE}/api/import`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(json);
}

export function fetchImportProgress() {
  return fetch(`${BASE}/api/import/progress`, { headers: authHeaders() }).then(json);
}

export function deleteAllAccountData() {
  return fetch(`${BASE}/api/account/data`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(json);
}

export function sendEmail({ draftId, to, subject, body }) {
  return fetch(`${BASE}/api/gmail/send`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ draft_id: draftId, to, subject, body }),
  }).then(json);
}

export function confirmCalendarAction({ draftId, kind, eventId, title, startTime, durationMinutes, notes, attendees }) {
  return fetch(`${BASE}/api/calendar/confirm`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      draft_id: draftId,
      kind,
      event_id: eventId,
      title,
      start_time: startTime,
      duration_minutes: durationMinutes,
      notes,
      attendees,
    }),
  }).then(json);
}

export function transcribeAudio(blob) {
  const form = new FormData();
  form.append('file', blob, 'voice.webm');
  // No Content-Type header here on purpose — the browser sets
  // multipart/form-data with the right boundary itself; setting it
  // manually (as authHeaders({'Content-Type': ...}) would) breaks the
  // boundary and the backend can't parse the upload.
  return fetch(`${BASE}/api/transcribe`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(json);
}

export function extractDocument(file) {
  const form = new FormData();
  form.append('file', file, file.name);
  return fetch(`${BASE}/api/documents/extract`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(json);
}

export function fetchProactive() {
  return fetch(`${BASE}/api/proactive`, { headers: authHeaders() }).then(json);
}

export function exportMemories() {
  return fetch(`${BASE}/api/memories/export`, { headers: authHeaders() }).then((res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  });
}

