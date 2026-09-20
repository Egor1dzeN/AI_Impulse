import { useEffect, useState } from 'react';
import { fetchSettings, updateSettings } from '../lib/api';
import { applyTheme, getTheme, setTheme as persistTheme } from '../lib/settings';

const THEME_OPTIONS = [
  { value: 'system', label: 'Системная' },
  { value: 'light', label: 'Светлая' },
  { value: 'dark', label: 'Тёмная' },
];

export default function SettingsPanel({ open, onClose }) {
  const [theme, setThemeState] = useState(getTheme);
  const [connected, setConnected] = useState(null); // null = ещё не загружено
  const [loading, setLoading] = useState(false);
  const [showConnectForm, setShowConnectForm] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setError('');
    setKeyInput('');
    setShowConnectForm(false);
    setLoading(true);
    fetchSettings()
      .then((s) => setConnected(s.intervals_icu_connected))
      .catch(() => setConnected(null))
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  function chooseTheme(next) {
    setThemeState(next);
    persistTheme(next);
    applyTheme(next);
  }

  async function connectIntervals(e) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key || saving) return;
    setSaving(true);
    setError('');
    try {
      const s = await updateSettings({ intervals_icu_api_key: key });
      setConnected(s.intervals_icu_connected);
      setKeyInput('');
    } catch {
      setError('Не получилось подключить — проверьте ключ.');
    } finally {
      setSaving(false);
    }
  }

  async function disconnectIntervals() {
    if (disconnecting) return;
    setDisconnecting(true);
    try {
      const s = await updateSettings({ intervals_icu_api_key: '' });
      setConnected(s.intervals_icu_connected);
    } catch {
      // leave the current state on screen if the request failed
    } finally {
      setDisconnecting(false);
    }
  }

  return (
    <div className="settings-panel">
      <div className="settings-panel__backdrop" onClick={onClose} />
      <div className="settings-panel__body">
        <div className="settings-panel__head">
          <h2>Настройки</h2>
          <button type="button" className="ghost" onClick={onClose}>Закрыть</button>
        </div>

        <section>
          <h3>Тема</h3>
          <div className="theme-switch">
            {THEME_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`theme-switch__option${theme === opt.value ? ' theme-switch__option--active' : ''}`}
                onClick={() => chooseTheme(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </section>

        <section>
          <h3>Intervals.icu</h3>
          {loading && <p className="settings-panel__hint">Загрузка…</p>}

          {!loading && connected && (
            <div className="settings-connect">
              <div className="settings-connect__status">
                <span className="settings-connect__dot" />
                Подключено
              </div>
              <p className="settings-panel__hint">
                Ассистент может записывать тренировки и показывать маршруты из вашего аккаунта Intervals.icu.
              </p>
              <button type="button" className="ghost" onClick={disconnectIntervals} disabled={disconnecting}>
                {disconnecting ? 'Отключаю…' : 'Отключить'}
              </button>
            </div>
          )}

          {!loading && !connected && !showConnectForm && (
            <div className="settings-connect">
              <div className="settings-connect__status settings-connect__status--off">
                <span className="settings-connect__dot settings-connect__dot--off" />
                Не подключено
              </div>
              <button type="button" className="ghost" onClick={() => setShowConnectForm(true)}>
                Подключить
              </button>
            </div>
          )}

          {!loading && !connected && showConnectForm && (
            <form className="settings-connect" onSubmit={connectIntervals}>
              <p className="settings-panel__hint">
                Вставьте личный API-ключ из intervals.icu → Settings → Developer Settings, чтобы ассистент мог записывать тренировки и показывать их маршруты на карте.
              </p>
              <div className="memory-add">
                <input
                  type="password"
                  placeholder="API-ключ"
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  disabled={saving}
                  autoComplete="off"
                  autoFocus
                />
                <button type="submit" className="ghost" disabled={saving || !keyInput.trim()}>
                  {saving ? 'Подключаю…' : 'Подключить'}
                </button>
              </div>
              {error && <p className="settings-panel__hint settings-panel__hint--error">{error}</p>}
            </form>
          )}
        </section>
      </div>
    </div>
  );
}
