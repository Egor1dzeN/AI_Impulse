import { useEffect, useState } from 'react';
import { fetchMemories, fetchSandbox } from '../lib/api';

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

export default function MemoryPanel({ open, userId, onClose }) {
  const [memories, setMemories] = useState([]);
  const [sandbox, setSandbox] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    Promise.all([fetchMemories(userId), fetchSandbox(userId)])
      .then(([mem, sb]) => {
        setMemories(mem);
        setSandbox(sb);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, userId]);

  if (!open) return null;

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
          {memories.length === 0 && !loading && (
            <p className="memory-panel__hint">Пока ничего — напишите что-нибудь о себе в чате.</p>
          )}
          <ul className="memory-list">
            {memories.map((m) => (
              <li key={m.id}>
                <span className="memory-list__type">{TYPE_LABELS[m.type] || m.type}</span>
                {m.content}
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
