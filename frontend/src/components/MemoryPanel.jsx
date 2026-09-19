import { useEffect, useState } from 'react';
import { addMemory, deleteMemory, fetchMemories, fetchSandbox } from '../lib/api';

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

  function loadMemories() {
    return fetchMemories().then(setMemories);
  }

  useEffect(() => {
    if (!open) return;
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

  async function removeMemory(id) {
    setRemovingId(id);
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
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
          <h3>Что ассистент запомнил о вас</h3>

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

          {memories.length === 0 && !loading && (
            <p className="memory-panel__hint">Пока ничего — напишите что-нибудь о себе в чате или добавьте вручную.</p>
          )}
          <ul className="memory-list">
            {memories.map((m) => (
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
