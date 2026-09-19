const BASE = '';
const TOKEN_KEY = 'impulse_token';

// Public identifier, not a secret — matches backend/app/auth.py's default.
export const GOOGLE_CLIENT_ID = '75107685144-51b4nd1b4stkl6p6en293sndm6d5jk5q.apps.googleusercontent.com';

async function json(res) {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function register(username, password) {
  const { access_token } = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }).then(json);
  localStorage.setItem(TOKEN_KEY, access_token);
}

export async function login(username, password) {
  const { access_token } = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }).then(json);
  localStorage.setItem(TOKEN_KEY, access_token);
}

export async function loginWithGoogle(idToken) {
  const { access_token } = await fetch(`${BASE}/api/auth/google`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: idToken }),
  }).then(json);
  localStorage.setItem(TOKEN_KEY, access_token);
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function isAuthenticated() {
  return !!getToken();
}

export function getUsername() {
  const token = getToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.username || payload.sub || null;
  } catch {
    return null;
  }
}
