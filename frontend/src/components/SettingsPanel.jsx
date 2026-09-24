import { useEffect, useState } from 'react';
import {
  deleteAllAccountData,
  disconnectGmail,
  disconnectGoogleCalendar,
  fetchSettings,
  getGmailConnectUrl,
  getGoogleCalendarConnectUrl,
  updateSettings,
} from '../lib/api';
import McpSettings from './McpSettings';
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

  const [gmailConnected, setGmailConnected] = useState(null);
  const [gmailConnecting, setGmailConnecting] = useState(false);
  const [gmailDisconnecting, setGmailDisconnecting] = useState(false);
  const [gmailError, setGmailError] = useState('');

  const [deleteState, setDeleteState] = useState('idle'); // idle | confirming | deleting | done | error

  useEffect(() => {
    if (!open) return;
    setError('');
    setKeyInput('');
    setShowConnectForm(false);
    setDeleteState('idle');
    setLoading(true);
    fetchSettings()
      .then((s) => {
        setConnected(s.intervals_icu_connected);
        setGcalConnected(s.google_calendar_connected);
        setGmailConnected(s.gmail_connected);
      })
      .catch(() => {
        setConnected(null);
        setGcalConnected(null);
        setGmailConnected(null);
      })
      .finally(() => setLoading(false));
  }, [open]);

  // The OAuth redirect lands back on the app (not inside this panel — the
  // browser navigated away and came back), so pick up its result from the
  // URL once on mount rather than from any in-panel state, and open the
  // panel + report the outcome instead of leaving it as a silent no-op.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let changed = false;

    const gcalResult = params.get('google_calendar');
    if (gcalResult) {
      params.delete('google_calendar');
      changed = true;
      if (gcalResult === 'connected') setGcalConnected(true);
      else if (gcalResult === 'error') setGcalError(t('settings.googleCalendarConnectError'));
    }

    const gmailResult = params.get('gmail');
    if (gmailResult) {
      params.delete('gmail');
      changed = true;
      if (gmailResult === 'connected') setGmailConnected(true);
      else if (gmailResult === 'error') setGmailError(t('settings.gmailConnectError'));
    }

    if (changed) {
      const rest = params.toString();
      window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : ''));
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

  async function connectGmail() {
    if (gmailConnecting) return;
    setGmailConnecting(true);
    setGmailError('');
    try {
      const { url } = await getGmailConnectUrl();
      window.location.href = url;
    } catch {
      setGmailError(t('settings.gmailConnectError'));
      setGmailConnecting(false);
    }
  }

  async function disconnectGmailClick() {
    if (gmailDisconnecting) return;
    setGmailDisconnecting(true);
    try {
      const s = await disconnectGmail();
      setGmailConnected(s.gmail_connected);
    } catch {
      // leave the current state on screen if the request failed
    } finally {
      setGmailDisconnecting(false);
    }
  }

  async function confirmDeleteAllData() {
    if (deleteState === 'deleting') return;
    setDeleteState('deleting');
    try {
      await deleteAllAccountData();
      setDeleteState('done');
    } catch {
      setDeleteState('error');
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

        <section>
          {loading && <p className="settings-panel__hint">{t('common.loading')}</p>}

          {!loading && (
            <>
              <div className="service-row">
                <div className="service-row__title">
                  <span className={`service-row__dot${gmailConnected ? '' : ' service-row__dot--off'}`} />
                  Gmail
                </div>
                {gmailConnected ? (
                  <button type="button" className="ghost" onClick={disconnectGmailClick} disabled={gmailDisconnecting}>
                    {gmailDisconnecting ? t('settings.disconnecting') : t('settings.disconnect')}
                  </button>
                ) : (
                  <button type="button" className="ghost" onClick={connectGmail} disabled={gmailConnecting}>
                    {gmailConnecting ? t('settings.connecting') : t('settings.connect')}
                  </button>
                )}
              </div>
              <p className="settings-panel__hint">
                {gmailConnected ? t('settings.gmailConnectedHint') : t('settings.gmailConnectHint')}
              </p>
              {gmailError && <p className="settings-panel__hint settings-panel__hint--error">{gmailError}</p>}
            </>
          )}
        </section>

        <section>
          <McpSettings open={open} />
        </section>

        <section className="settings-panel__danger-zone">
          <h3>{t('settings.dangerZoneTitle')}</h3>
          <p className="settings-panel__hint">{t('settings.deleteAllDataHint')}</p>
          {deleteState === 'done' ? (
            <p className="settings-panel__hint">{t('settings.deleteAllDataDone')}</p>
          ) : deleteState === 'confirming' ? (
            <>
              <p className="settings-panel__hint settings-panel__hint--error">{t('settings.deleteAllDataConfirmPrompt')}</p>
              <div className="email-draft__actions">
                <button type="button" className="ghost ghost--danger" onClick={confirmDeleteAllData}>
                  {t('settings.deleteAllDataConfirm')}
                </button>
                <button type="button" className="ghost" onClick={() => setDeleteState('idle')}>
                  {t('settings.deleteAllDataCancel')}
                </button>
              </div>
            </>
          ) : (
            <button
              type="button"
              className="ghost ghost--danger"
              onClick={() => setDeleteState('confirming')}
              disabled={deleteState === 'deleting'}
            >
              {deleteState === 'deleting' ? t('settings.deleteAllDataDeleting') : t('settings.deleteAllData')}
            </button>
          )}
          {deleteState === 'error' && <p className="settings-panel__hint settings-panel__hint--error">{t('settings.deleteAllDataError')}</p>}
        </section>
      </div>
    </div>
  );
}
