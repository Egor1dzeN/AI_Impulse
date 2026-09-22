import { useEffect, useState } from 'react';
import { fetchSettings, importData } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

const SOURCES = ['email', 'telegram', 'calendar'];

// Best-effort count for the confirmation modal below — same JSON-array-or-
// JSONL leniency as main.py's _parse_import_file(), just to show the user
// roughly how many real calendar events they're about to create. A parse
// failure here isn't fatal (the actual upload still runs the real parse
// server-side and reports its own errors), so this only ever degrades to
// "count unknown", never blocks the import.
async function countRecords(file) {
  try {
    const text = await file.text();
    const trimmed = text.trim();
    if (!trimmed) return 0;
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.length;
      if (parsed && typeof parsed === 'object') return 1;
    } catch {
      // not a single JSON value — fall through to line counting below
    }
    return trimmed.split('\n').filter((line) => line.trim()).length;
  } catch {
    return null;
  }
}

// The competition's "обязательный сценарий загрузки данных": the jury
// swaps in a second, unseen dataset during judging and expects a quick,
// no-code way to load it and have the assistant re-derive its whole
// profile from just that — see main.py's POST /api/import and
// memory_bridge.import_records() for the actual ingestion.
export default function ImportPanel({ open, onClose }) {
  const { t } = useI18n();
  const [source, setSource] = useState('email');
  const [file, setFile] = useState(null);
  const [replace, setReplace] = useState(true);
  const [syncCalendar, setSyncCalendar] = useState(false);
  const [calendarConnected, setCalendarConnected] = useState(null);
  const [status, setStatus] = useState('idle'); // idle | uploading | done | error
  const [result, setResult] = useState(null);
  const [confirmingSync, setConfirmingSync] = useState(false);
  const [pendingCount, setPendingCount] = useState(null);

  useEffect(() => {
    if (!open) return;
    setStatus('idle');
    setResult(null);
    setFile(null);
    setConfirmingSync(false);
    fetchSettings()
      .then((s) => setCalendarConnected(s.google_calendar_connected))
      .catch(() => setCalendarConnected(null));
  }, [open]);

  if (!open) return null;

  async function doImport() {
    setStatus('uploading');
    try {
      const res = await importData({ file, source, replace, syncCalendar: source === 'calendar' && syncCalendar });
      setResult(res);
      setStatus('done');
    } catch {
      setStatus('error');
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (!file || status === 'uploading') return;
    // Actually creating real events on the user's connected Google
    // Calendar is exactly the kind of external, hard-to-fully-undo action
    // this app always gates behind an explicit confirmation — same idea as
    // the calendar draft cards in chat, just as a small modal here since
    // there's no per-event card to confirm (it's a whole file at once).
    if (source === 'calendar' && syncCalendar) {
      setPendingCount(await countRecords(file));
      setConfirmingSync(true);
      return;
    }
    doImport();
  }

  function confirmSync() {
    setConfirmingSync(false);
    doImport();
  }

  return (
    <div className="settings-panel">
      <div className="settings-panel__backdrop" onClick={onClose} />
      <div className="settings-panel__body">
        <div className="settings-panel__head">
          <h2>{t('importPanel.title')}</h2>
          <button type="button" className="ghost" onClick={onClose}>{t('common.close')}</button>
        </div>

        <p className="settings-panel__hint">{t('importPanel.hint')}</p>

        <form onSubmit={onSubmit}>
          <section>
            <h3>{t('importPanel.sourceLabel')}</h3>
            <div className="theme-switch">
              {SOURCES.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`theme-switch__option${source === s ? ' theme-switch__option--active' : ''}`}
                  onClick={() => setSource(s)}
                  disabled={status === 'uploading'}
                >
                  {t(`importPanel.source${s.charAt(0).toUpperCase()}${s.slice(1)}`)}
                </button>
              ))}
            </div>
          </section>

          <section>
            <label className="email-draft__field">
              <span>{t('importPanel.fileLabel')}</span>
              <input
                type="file"
                accept=".jsonl,.json,application/json"
                onChange={(e) => setFile(e.target.files[0] || null)}
                disabled={status === 'uploading'}
              />
            </label>

            <label className="import-panel__checkbox">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
                disabled={status === 'uploading'}
              />
              {t('importPanel.replaceLabel')}
            </label>

            {source === 'calendar' && (
              <>
                <label className="import-panel__checkbox">
                  <input
                    type="checkbox"
                    checked={syncCalendar}
                    onChange={(e) => setSyncCalendar(e.target.checked)}
                    disabled={status === 'uploading' || !calendarConnected}
                  />
                  {t('importPanel.syncCalendarLabel')}
                </label>
                {!calendarConnected && <p className="settings-panel__hint">{t('importPanel.syncCalendarHint')}</p>}
              </>
            )}
          </section>

          <button type="submit" className="ghost" disabled={!file || status === 'uploading'}>
            {status === 'uploading' ? t('importPanel.uploading') : t('importPanel.submit')}
          </button>
        </form>

        {status === 'done' && result && (
          <section>
            {replace && <p className="settings-panel__hint">{t('importPanel.resultReplaced')}</p>}
            <p className="settings-panel__hint">
              {t('importPanel.resultImported', { imported: result.imported, total: result.imported + result.failed })}
            </p>
            {result.transactions > 0 && (
              <p className="settings-panel__hint">{t('importPanel.resultTransactions', { count: result.transactions })}</p>
            )}
            {result.memory_extraction_queued > 0 && (
              <p className="settings-panel__hint">{t('importPanel.resultMemoryQueued', { count: result.memory_extraction_queued })}</p>
            )}
            {result.flagged?.length > 0 && (
              <p className="settings-panel__hint settings-panel__hint--error">
                {t('importPanel.resultFlagged', { count: result.flagged.length })}
              </p>
            )}
            {result.calendar_sync && !result.calendar_sync.error && (
              <p className="settings-panel__hint">
                {t('importPanel.resultCalendarSync', {
                  synced: result.calendar_sync.synced,
                  skipped: result.calendar_sync.skipped,
                  failed: result.calendar_sync.failed,
                })}
              </p>
            )}
            {result.calendar_sync?.error && (
              <p className="settings-panel__hint settings-panel__hint--error">{t('importPanel.resultCalendarNotConnected')}</p>
            )}
          </section>
        )}
        {status === 'error' && <p className="settings-panel__hint settings-panel__hint--error">{t('importPanel.error')}</p>}
      </div>

      {confirmingSync && (
        <div className="confirm-modal">
          <div className="confirm-modal__backdrop" onClick={() => setConfirmingSync(false)} />
          <div className="confirm-modal__box">
            <h3>{t('importPanel.syncConfirmTitle')}</h3>
            <p>
              {pendingCount === null
                ? t('importPanel.syncConfirmBodyUnknown')
                : t('importPanel.syncConfirmBody', { count: pendingCount })}
            </p>
            <div className="email-draft__actions">
              <button type="button" className="ghost ghost--danger" onClick={confirmSync}>
                {t('importPanel.syncConfirmProceed')}
              </button>
              <button type="button" className="ghost" onClick={() => setConfirmingSync(false)}>
                {t('importPanel.syncConfirmCancel')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
