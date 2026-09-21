import { useEffect, useState } from 'react';
import { fetchSettings, updateSettings } from '../lib/api';
import { applyTheme, getTheme, setTheme as persistTheme } from '../lib/settings';
import { useI18n } from '../lib/i18n.jsx';

export default function SettingsPanel({ open, onClose }) {
  const { t, lang, changeLanguage } = useI18n();
  const THEME_OPTIONS = [
    { value: 'system', label: t('settings.themeSystem') },
    { value: 'light', label: t('settings.themeLight') },
    { value: 'dark', label: t('settings.themeDark') },
  ];
  const LANG_OPTIONS = [
    { value: 'ru', label: t('settings.langRu') },
    { value: 'en', label: t('settings.langEn') },
  ];
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
      setError(t('settings.connectError'));
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
          <h2>{t('common.settings')}</h2>
          <button type="button" className="ghost" onClick={onClose}>{t('common.close')}</button>
        </div>

        <section>
          <h3>{t('settings.languageSectionTitle')}</h3>
          <div className="theme-switch">
            {LANG_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`theme-switch__option${lang === opt.value ? ' theme-switch__option--active' : ''}`}
                onClick={() => changeLanguage(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </section>

        <section>
          <h3>{t('settings.themeSectionTitle')}</h3>
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
          {loading && <p className="settings-panel__hint">{t('common.loading')}</p>}

          {!loading && connected && (
            <div className="settings-connect">
              <div className="settings-connect__status">
                <span className="settings-connect__dot" />
                {t('settings.intervalsConnected')}
              </div>
              <p className="settings-panel__hint">{t('settings.intervalsConnectedHint')}</p>
              <button type="button" className="ghost" onClick={disconnectIntervals} disabled={disconnecting}>
                {disconnecting ? t('settings.disconnecting') : t('settings.disconnect')}
              </button>
            </div>
          )}

          {!loading && !connected && !showConnectForm && (
            <div className="settings-connect">
              <div className="settings-connect__status settings-connect__status--off">
                <span className="settings-connect__dot settings-connect__dot--off" />
                {t('settings.intervalsNotConnected')}
              </div>
              <button type="button" className="ghost" onClick={() => setShowConnectForm(true)}>
                {t('settings.connect')}
              </button>
            </div>
          )}

          {!loading && !connected && showConnectForm && (
            <form className="settings-connect" onSubmit={connectIntervals}>
              <p className="settings-panel__hint">{t('settings.intervalsConnectHint')}</p>
              <div className="memory-add">
                <input
                  type="password"
                  placeholder={t('settings.apiKeyPlaceholder')}
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  disabled={saving}
                  autoComplete="off"
                  autoFocus
                />
                <button type="submit" className="ghost" disabled={saving || !keyInput.trim()}>
                  {saving ? t('settings.connecting') : t('settings.connect')}
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
