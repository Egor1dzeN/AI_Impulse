const REASONING_KEY = 'impulse_reasoning';

export function getReasoningEnabled() {
  const stored = localStorage.getItem(REASONING_KEY);
  return stored === null ? true : stored === '1';
}

export function setReasoningEnabled(enabled) {
  localStorage.setItem(REASONING_KEY, enabled ? '1' : '0');
}
