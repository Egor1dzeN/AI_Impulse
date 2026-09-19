import { useEffect, useState } from 'react';
import { addMemory, deleteMemory, dreamMemories, fetchMemories, fetchSandbox, searchMemories } from '../lib/api';

const TYPE_LABELS = {
  fact: 'Факт',
  preference: 'Предпочтение',
  episode: 'Событие',
  entity: 'Сущность',
  relationship: 'Отношение',
  goal: 'Цель',
  state: 'Состояние',
  procedure: 'Процедура',
};

export default function MemoryPanel({ open, onClose }) {
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
      setAddError('Не получилось добавить. Попробуйте ещё раз.');
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
      setSearchError('Не получилось найти. Попробуйте ещё раз.');
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
      setDreamSummary('Не получилось обработать память. Попробуйте ещё раз.');
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
          <h2>Память</h2>
          <button type="button" className="ghost" onClick={onClose}>Закрыть</button>
        </div>

        {loading && <p className="memory-panel__hint">Загрузка…</p>}

        <section>
          <div className="memory-panel__section-head">
            <h3>Что ассистент запомнил о вас</h3>
            <button
              type="button"
              className="ghost"
              onClick={runDream}
              disabled={dreaming}
              title="Найти и объединить дубликаты/противоречия среди всех фактов"
            >
              {dreaming ? 'Обрабатываю…' : 'Осмыслить'}
            </button>
          </div>
          {dreamSummary && <p className="memory-panel__hint">{dreamSummary}</p>}

          <form className="memory-add" onSubmit={submitNewFact}>
            <input
              type="text"
              placeholder="Добавить факт о себе…"
              value={newFact}
              onChange={(e) => setNewFact(e.target.value)}
              disabled={adding}
            />
            <button type="submit" className="ghost" disabled={adding || !newFact.trim()}>
              {adding ? 'Добавляю…' : 'Добавить'}
            </button>
          </form>
          {addError && <p className="memory-panel__hint memory-panel__hint--error">{addError}</p>}

          <form className="memory-add" onSubmit={submitSearch}>
            <input
              type="text"
              placeholder="Поиск по памяти…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              disabled={searching}
            />
            {searchResults ? (
              <button type="button" className="ghost" onClick={clearSearch}>Сбросить</button>
            ) : (
              <button type="submit" className="ghost" disabled={searching || !searchInput.trim()}>
                {searching ? 'Ищу…' : 'Найти'}
              </button>
            )}
          </form>
          {searchError && <p className="memory-panel__hint memory-panel__hint--error">{searchError}</p>}

          {searchResults && searchResults.length === 0 && !searching && (
            <p className="memory-panel__hint">Ничего не найдено по этому запросу.</p>
          )}
          {!searchResults && memories.length === 0 && !loading && (
            <p className="memory-panel__hint">Пока ничего — напишите что-нибудь о себе в чате или добавьте вручную.</p>
          )}
          <ul className="memory-list">
            {(searchResults ?? memories).map((m) => (
              <li key={m.id}>
                <span className="memory-list__type">{TYPE_LABELS[m.type] || m.type}</span>
                <span className="memory-list__content">{m.content}</span>
                <button
                  type="button"
                  className="memory-list__remove"
                  aria-label="Удалить факт"
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
            <h3>Последние действия</h3>
            <ul className="memory-list">
              {sandbox.tasks.slice(0, 5).map((t) => (
                <li key={t.id}>Задача: {t.title}{t.due_date ? ` — до ${t.due_date}` : ''}</li>
              ))}
              {sandbox.expenses.slice(0, 5).map((e) => (
                <li key={e.id}>Трата: {e.amount} ₽ ({e.category}), {e.date}</li>
              ))}
              {sandbox.meals.slice(0, 5).map((m) => (
                <li key={m.id}>Приём пищи: {m.name}, {m.kcal} ккал, {m.date}</li>
              ))}
              {sandbox.weights.slice(0, 5).map((w) => (
                <li key={w.id}>Вес: {w.value_kg} кг, {w.date}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
