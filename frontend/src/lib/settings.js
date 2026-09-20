const REASONING_KEY = 'impulse_reasoning';

export function getReasoningEnabled() {
  const stored = localStorage.getItem(REASONING_KEY);
  return stored === null ? true : stored === '1';
}

export function setReasoningEnabled(enabled) {
  localStorage.setItem(REASONING_KEY, enabled ? '1' : '0');
}

// Theme: 'system' | 'light' | 'dark'. 'system' is the default and is
// stored as the absence of a key (not the literal string) so index.html's
// early inline script and this file agree on what "no preference set"
// means without duplicating the default value in two places.
const THEME_KEY = 'impulse_theme';

export function getTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  return stored === 'light' || stored === 'dark' ? stored : 'system';
}

export function setTheme(theme) {
  if (theme === 'light' || theme === 'dark') {
    localStorage.setItem(THEME_KEY, theme);
  } else {
    localStorage.removeItem(THEME_KEY);
  }
}

// Applies the given/stored theme to the document — same logic index.html's
// inline script runs before React loads (to avoid a flash of the wrong
// theme); call this again whenever the user changes the setting.
export function applyTheme(theme = getTheme()) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
  else root.removeAttribute('data-theme');
}

// What's actually in effect right now, resolving 'system' against the OS
// preference — used where a component needs one concrete answer (e.g.
// picking a light/dark Mapbox map style) rather than the raw setting.
export function resolveTheme() {
  const stored = getTheme();
  if (stored !== 'system') return stored;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
