import { useEffect, useState } from 'react';
import { addMcpServer, deleteMcpServer, fetchMcpServers } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

// The user's own MCP servers (remote https only, checked by the backend) plus
// read-only global ones from backend/mcp_servers.json. The token goes to the
// backend once and is never sent back.
export default function McpSettings({ open }) {
  const { t } = useI18n();
  const [servers, setServers] = useState({ global: [], mine: [] });
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', url: '', token: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function load(refresh = false) {
    setLoading(true);
    return fetchMcpServers(refresh)
      .then(setServers)
      .catch(() => setServers({ global: [], mine: [] }))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!open) return;
    setShowForm(false);
    setError('');
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onAdd(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await addMcpServer({ name: form.name.trim(), url: form.url.trim(), token: form.token.trim() || null });
      setForm({ name: '', url: '', token: '' });
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err.message || t('settings.mcpAddError'));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id) {
    setBusy(true);
    try {
      const res = await deleteMcpServer(id);
      setServers((s) => ({ ...s, mine: res.mine || [] }));
    } finally {
      setBusy(false);
    }
  }

  const row = (s, removable) => (
    <div key={`${s.scope}-${s.name}`}>
      <div className="service-row">
        <div className="service-row__title">
          <span className={`service-row__dot${s.ok ? '' : ' service-row__dot--off'}`} />
          {s.name}
          {!removable && <span className="settings-panel__hint"> · {t('settings.mcpGlobal')}</span>}
        </div>
        {removable && (
          <button type="button" className="ghost" onClick={() => onDelete(s.id)} disabled={busy}>
            {t('settings.mcpRemove')}
          </button>
        )}
      </div>
      {s.url && <p className="settings-panel__hint mcp-url">{s.url}</p>}
      <p className={`settings-panel__hint${s.ok ? '' : ' settings-panel__hint--error'}`}>
        {s.ok
          ? t('settings.mcpTools', { tools: s.tools.map((x) => x.name).join(', ') || '—' })
          : t('settings.mcpUnavailable', { error: s.error || '' })}
      </p>
    </div>
  );

  return (
    <>
      <div className="service-row">
        <div className="service-row__title">{t('settings.mcpTitle')}</div>
        <button type="button" className="ghost" onClick={() => setShowForm((v) => !v)} disabled={busy}>
          {t('settings.mcpAdd')}
        </button>
      </div>
      <p className="settings-panel__hint">{t('settings.mcpHint')}</p>

      {showForm && (
        <form className="settings-connect mcp-form" onSubmit={onAdd}>
          <input
            placeholder={t('settings.mcpNamePlaceholder')}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            disabled={busy}
            maxLength={20}
            autoComplete="off"
            autoFocus
          />
          <input
            placeholder="https://example.com/mcp"
            value={form.url}
            onChange={(e) => setForm({ ...form, url: e.target.value })}
            disabled={busy}
            autoComplete="off"
            inputMode="url"
          />
          <input
            type="password"
            placeholder={t('settings.mcpTokenPlaceholder')}
            value={form.token}
            onChange={(e) => setForm({ ...form, token: e.target.value })}
            disabled={busy}
            autoComplete="off"
          />
          <button type="submit" className="ghost" disabled={busy || !form.name.trim() || !form.url.trim()}>
            {busy ? t('settings.connecting') : t('settings.connect')}
          </button>
          {error && <p className="settings-panel__hint settings-panel__hint--error">{error}</p>}
        </form>
      )}

      {loading && <p className="settings-panel__hint">{t('common.loading')}</p>}
      {servers.mine.map((s) => row(s, true))}
      {servers.global.map((s) => row(s, false))}
      {servers.mine.length > 0 && (
        <button type="button" className="ghost" onClick={() => load(true)} disabled={loading || busy}>
          {t('settings.mcpRefresh')}
        </button>
      )}
    </>
  );
}
