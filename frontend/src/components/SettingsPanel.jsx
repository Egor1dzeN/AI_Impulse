import { useEffect, useState } from 'react';
import { disconnectGoogleCalendar, fetchSettings, getGoogleCalendarConnectUrl, updateSettings } from '../lib/api';
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

  const [gcalConnected, setGcalConnected] = useState(null);
  const [gcalConnecting, setGcalConnecting] = useState(false);
  const [gcalDisconnecting, setGcalDisconnecting] = useState(false);
  const [gcalError, setGcalError] = useState('');

  useEffect(() => {
    if (!open) return;
    setError('');
    setKeyInput('');
    setShowConnectForm(false);
    setLoading(true);
    fetchSettings()
      .then((s) => {
        setConnected(s.intervals_icu_connected);
        setGcalConnected(s.google_calendar_connected);
      })
      .catch(() => {
        setConnected(null);
        setGcalConnected(null);
      })
      .finally(() => setLoading(false));
  }, [open]);

  // The OAuth redirect lands back on the app (not inside this panel — the
  // browser navigated away and came back), so pick up its result from the
  // URL once on mount rather than from any in-panel state, and open the
  // panel + report the outcome instead of leaving it as a silent no-op.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get('google_calendar');
    if (!result) return;
    params.delete('google_calendar');
    const rest = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : ''));
    if (result === 'connected') {
      setGcalConnected(true);
    } else if (result === 'error') {
      setGcalError(t('settings.googleCalendarConnectError'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  async function connectGoogleCalendar() {
    if (gcalConnecting) return;
    setGcalConnecting(true);
    setGcalError('');
    try {
      const { url } = await getGoogleCalendarConnectUrl();
      // Full navigation, not a fetch — Google's consent screen and the
      // redirect back need the actual browser location, not our SPA state.
      window.location.href = url;
    } catch {
      setGcalError(t('settings.googleCalendarConnectError'));
      setGcalConnecting(false);
    }
  }

  async function disconnectGoogleCalendarClick() {
    if (gcalDisconnecting) return;
    setGcalDisconnecting(true);
    try {
      const s = await disconnectGoogleCalendar();
      setGcalConnected(s.google_calendar_connected);
    } catch {
      // leave the current state on screen if the request failed
    } finally {
      setGcalDisconnecting(false);
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
          {loading && <p className="settings-panel__hint">{t('common.loading')}</p>}

          {!loading && (
            <>
              <div className="service-row">
                <div className="service-row__title">
                  <span className={`service-row__dot${connected ? '' : ' service-row__dot--off'}`} />
                  Intervals.icu
                </div>
                {connected ? (
                  <button type="button" className="ghost" onClick={disconnectIntervals} disabled={disconnecting}>
                    {disconnecting ? t('settings.disconnecting') : t('settings.disconnect')}
                  </button>
                ) : (
                  <button type="button" className="ghost" onClick={() => setShowConnectForm((v) => !v)}>
                    {t('settings.connect')}
                  </button>
                )}
              </div>
              <p className="settings-panel__hint">
                {connected ? t('settings.intervalsConnectedHint') : t('settings.intervalsConnectHint')}
              </p>
            </>
          )}

          {!loading && !connected && showConnectForm && (
            <form className="settings-connect" onSubmit={connectIntervals}>
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

        <section>
          {loading && <p className="settings-panel__hint">{t('common.loading')}</p>}

          {!loading && (
            <>
              <div className="service-row">
                <div className="service-row__title">
                  <span className={`service-row__dot${gcalConnected ? '' : ' service-row__dot--off'}`} />
                  Google Calendar
                </div>
                {gcalConnected ? (
                  <button type="button" className="ghost" onClick={disconnectGoogleCalendarClick} disabled={gcalDisconnecting}>
                    {gcalDisconnecting ? t('settings.disconnecting') : t('settings.disconnect')}
                  </button>
                ) : (
                  <button type="button" className="ghost" onClick={connectGoogleCalendar} disabled={gcalConnecting}>
                    {gcalConnecting ? t('settings.connecting') : t('settings.connect')}
                  </button>
                )}
              </div>
              <p className="settings-panel__hint">
                {gcalConnected ? t('settings.googleCalendarConnectedHint') : t('settings.googleCalendarConnectHint')}
              </p>
              {gcalError && <p className="settings-panel__hint settings-panel__hint--error">{gcalError}</p>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
