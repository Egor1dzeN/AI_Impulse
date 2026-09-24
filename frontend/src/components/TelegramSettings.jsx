import { useEffect, useState } from 'react';
import { createTelegramConnectCode, disconnectTelegram, fetchTelegramStatus } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

// Telegram bot linking: a one-time code turns into t.me/<bot>?start=CODE for
// the user's own chat, or "/connect CODE" to send inside a group the bot was
// added to. The server links the account when the bot receives it.
export default function TelegramSettings({ open }) {
  const { t } = useI18n();
  const [status, setStatus] = useState(null);
  const [code, setCode] = useState(null); // {kind, deep_link, group_command, expires_at}
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function refresh() {
    return fetchTelegramStatus()
      .then((s) => {
        setStatus(s);
        if (s.connected && code?.kind === 'private') setCode(null);
      })
      .catch(() => setStatus({ configured: false }));
  }

  useEffect(() => {
    if (open) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // While a link code is pending, check every few seconds whether the bot got it.
  useEffect(() => {
    if (!open || !code) return undefined;
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, code]);

  async function makeCode(kind) {
    setBusy(true);
    setError('');
    try {
      setCode(await createTelegramConnectCode(kind));
    } catch (err) {
      setError(err.message || t('settings.telegramError'));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      setStatus(await disconnectTelegram());
      setCode(null);
    } finally {
      setBusy(false);
    }
  }

  if (!status) return <p className="settings-panel__hint">{t('common.loading')}</p>;

  return (
    <>
      <div className="service-row">
        <div className="service-row__title">
          <span className={`service-row__dot${status.connected ? '' : ' service-row__dot--off'}`} />
          Telegram
        </div>
        {status.configured &&
          (status.connected ? (
            <button type="button" className="ghost" onClick={disconnect} disabled={busy}>
              {t('settings.disconnect')}
            </button>
          ) : (
            <button type="button" className="ghost" onClick={() => makeCode('private')} disabled={busy}>
              {t('settings.connect')}
            </button>
          ))}
      </div>

      {!status.configured && <p className="settings-panel__hint">{t('settings.telegramNotConfigured')}</p>}

      {status.configured && status.connected && (
        <>
          <p className="settings-panel__hint">
            {t('settings.telegramConnectedHint', { bot: status.bot_username || '' })}
          </p>
          {status.chats?.length > 0 && (
            <ul className="settings-panel__hint telegram-chats">
              {status.chats.map((c) => (
                <li key={c.chat_id}>
                  {c.type === 'private' ? t('settings.telegramPrivateChat') : c.title || c.username || c.chat_id}
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="ghost" onClick={() => makeCode('group')} disabled={busy}>
            {t('settings.telegramAddGroup')}
          </button>
        </>
      )}

      {status.configured && !status.connected && !code && (
        <p className="settings-panel__hint">{t('settings.telegramConnectHint')}</p>
      )}

      {code?.deep_link && !status.connected && (
        <p className="settings-panel__hint">
          {t('settings.telegramOpenLink')}{' '}
          <a href={code.deep_link} target="_blank" rel="noreferrer">
            {code.deep_link}
          </a>
          <br />
          {t('settings.telegramCodeTtl')}
        </p>
      )}

      {code?.group_command && (
        <p className="settings-panel__hint">
          {t('settings.telegramGroupSteps', { bot: code.bot_username || '' })} <code>{code.group_command}</code>
          <br />
          {t('settings.telegramCodeTtl')}
        </p>
      )}

      {status.configured && status.receiving === false && (
        <p className="settings-panel__hint settings-panel__hint--error">{t('settings.telegramNotReceiving')}</p>
      )}
      {error && <p className="settings-panel__hint settings-panel__hint--error">{error}</p>}
    </>
  );
}
