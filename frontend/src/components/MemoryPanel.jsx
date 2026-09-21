import { useEffect, useState } from 'react';
import { addMemory, deleteMemory, dreamMemories, fetchMemories, fetchSandbox, searchMemories } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

export default function MemoryPanel({ open, onClose }) {
  const { t } = useI18n();
  const TYPE_LABELS = t('memoryPanel.types');
  const [memories, setMemories] = useState([]);
  const [sandbox, setSandbox] = useState(null);
  const [loading, setLoading] = useState(false);
  const [newFact, setNewFact] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');
  const [removingId, setRemovingId] = useState(null);
  const [searchInput, setSearchInput] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [dreaming, setDreaming] = useState(false);
  const [dreamSummary, setDreamSummary] = useState('');

  function loadMemories() {
    return fetchMemories().then(setMemories);
  }

  useEffect(() => {
    if (!open) return;
    setSearchInput('');
    setSearchResults(null);
    setSearchError('');
    setLoading(true);
    Promise.all([loadMemories(), fetchSandbox().then(setSandbox)])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  async function submitNewFact(e) {
    e.preventDefault();
    const text = newFact.trim();
    if (!text || adding) return;
    setAdding(true);
    setAddError('');
    try {
      await addMemory(text);
      setNewFact('');
      await loadMemories();
    } catch {
      setAddError(t('memoryPanel.addError'));
    } finally {
      setAdding(false);
    }
  }

  async function submitSearch(e) {
    e.preventDefault();
    const q = searchInput.trim();
    if (!q || searching) return;
    setSearching(true);
    setSearchError('');
    try {
      const hits = await searchMemories(q);
      setSearchResults(hits);
    } catch {
      setSearchError(t('memoryPanel.searchError'));
    } finally {
      setSearching(false);
    }
  }

  function clearSearch() {
    setSearchInput('');
    setSearchResults(null);
    setSearchError('');
  }

  async function runDream() {
    if (dreaming) return;
    setDreaming(true);
    setDreamSummary('');
    try {
      const result = await dreamMemories();
      setDreamSummary(result.summary);
      if (result.merged.length) await loadMemories();
    } catch {
      setDreamSummary(t('memoryPanel.dreamError'));
    } finally {
      setDreaming(false);
    }
  }

  async function removeMemory(id) {
    setRemovingId(id);
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      setSearchResults((prev) => (prev ? prev.filter((m) => m.id !== id) : prev));
    } catch {
      // leave it in place if the delete failed
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div className="memory-panel">
      <div className="memory-panel__backdrop" onClick={onClose} />
      <div className="memory-panel__body">
        <div className="memory-panel__head">
          <h2>{t('common.memory')}</h2>
          <button type="button" className="ghost" onClick={onClose}>{t('common.close')}</button>
        </div>

        {loading && <p className="memory-panel__hint">{t('common.loading')}</p>}

        <section>
          <div className="memory-panel__section-head">
            <h3>{t('memoryPanel.aboutYou')}</h3>
            <span className="tooltip">
              <button
                type="button"
                className="ghost"
                onClick={runDream}
                disabled={dreaming}
              >
                {dreaming ? t('memoryPanel.dreamButtonBusy') : t('memoryPanel.dreamButton')}
              </button>
              <span className="tooltip__badge" aria-hidden="true">!</span>
              <span className="tooltip__bubble" role="tooltip">{t('memoryPanel.dreamTooltip')}</span>
            </span>
          </div>
          {dreamSummary && <p className="memory-panel__hint">{dreamSummary}</p>}

          <form className="memory-add" onSubmit={submitNewFact}>
            <input
              type="text"
              placeholder={t('memoryPanel.addFactPlaceholder')}
              value={newFact}
              onChange={(e) => setNewFact(e.target.value)}
              disabled={adding}
            />
            <button type="submit" className="ghost" disabled={adding || !newFact.trim()}>
              {adding ? t('memoryPanel.addButtonBusy') : t('memoryPanel.addButton')}
            </button>
          </form>
          {addError && <p className="memory-panel__hint memory-panel__hint--error">{addError}</p>}

          <form className="memory-add" onSubmit={submitSearch}>
            <input
              type="text"
              placeholder={t('memoryPanel.searchPlaceholder')}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              disabled={searching}
            />
            {searchResults ? (
              <button type="button" className="ghost" onClick={clearSearch}>{t('common.reset')}</button>
            ) : (
              <button type="submit" className="ghost" disabled={searching || !searchInput.trim()}>
                {searching ? t('memoryPanel.searchButtonBusy') : t('memoryPanel.searchButton')}
              </button>
            )}
          </form>
          {searchError && <p className="memory-panel__hint memory-panel__hint--error">{searchError}</p>}

          {searchResults && searchResults.length === 0 && !searching && (
            <p className="memory-panel__hint">{t('memoryPanel.noSearchResults')}</p>
          )}
          {!searchResults && memories.length === 0 && !loading && (
            <p className="memory-panel__hint">{t('memoryPanel.noMemories')}</p>
          )}
          <ul className="memory-list">
            {(searchResults ?? memories).map((m) => (
              <li key={m.id}>
                <span className="memory-list__type">{TYPE_LABELS[m.type] || m.type}</span>
                <span className="memory-list__content">{m.content}</span>
                <button
                  type="button"
                  className="memory-list__remove"
                  aria-label={t('memoryPanel.deleteFactAriaLabel')}
                  disabled={removingId === m.id}
                  onClick={() => removeMemory(m.id)}
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M4 4l8 8M12 4l-8 8" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {sandbox && (
          <section>
            <h3>{t('memoryPanel.recentActions')}</h3>
            <ul className="memory-list">
              {sandbox.tasks.slice(0, 5).map((task) => (
                <li key={task.id}>
                  {t('memoryPanel.taskLine', { title: task.title })}
                  {task.due_date ? t('memoryPanel.taskDueSuffix', { date: task.due_date }) : ''}
                </li>
              ))}
              {sandbox.expenses.slice(0, 5).map((e) => (
                <li key={e.id}>{t('memoryPanel.expenseLine', { amount: e.amount, category: e.category, date: e.date })}</li>
              ))}
              {sandbox.meals.slice(0, 5).map((m) => (
                <li key={m.id}>{t('memoryPanel.mealLine', { name: m.name, kcal: m.kcal, date: m.date })}</li>
              ))}
              {sandbox.weights.slice(0, 5).map((w) => (
                <li key={w.id}>{t('memoryPanel.weightLine', { value: w.value_kg, date: w.date })}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
