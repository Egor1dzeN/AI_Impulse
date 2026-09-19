import { useEffect, useRef } from 'react';
import { md } from '../lib/markdown';

const ACTION_LABELS = {
  create_task: 'Задача создана',
  add_expense: 'Трата записана',
  log_meal: 'Приём пищи записан',
  log_weight: 'Вес записан',
  list_tasks: 'Список задач',
  get_expense_summary: 'Сводка трат',
  get_health_summary: 'Сводка по здоровью',
};

function ActionBadge({ action }) {
  if (!action) return null;
  const label = ACTION_LABELS[action.tool] || action.tool;
  return (
    <div className="action-badge">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M3 8.5 6.2 11.7 13 4.5" />
      </svg>
      {label}
    </div>
  );
}

function Copy({ text }) {
  const btnRef = useRef(null);
  const onClick = () => {
    navigator.clipboard.writeText(text).then(() => {
      const btn = btnRef.current;
      if (!btn) return;
      btn.textContent = 'Скопировано';
      setTimeout(() => {
        if (btn) btn.textContent = 'Скопировать';
      }, 1600);
    });
  };
  return (
    <button ref={btnRef} type="button" className="copy" onClick={onClick}>
      Скопировать
    </button>
  );
}

// Code blocks are injected as raw HTML (dangerouslySetInnerHTML), so their
// copy buttons can't take a React onClick — handle clicks via delegation
// on the feed container instead.
function onFeedClick(e) {
  const btn = e.target.closest('.codeblock__copy');
  if (!btn) return;
  const code = btn.closest('.codeblock')?.querySelector('code');
  if (!code) return;
  navigator.clipboard.writeText(code.textContent).then(() => {
    const prev = btn.textContent;
    btn.textContent = 'Скопировано';
    clearTimeout(btn._resetTimer);
    btn._resetTimer = setTimeout(() => { btn.textContent = prev; }, 1600);
  });
}

export default function Chat({ messages }) {
  const feedRef = useRef(null);

  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    const main = el.closest('.main');
    if (!main) return;
    const pinned = main.scrollHeight - main.scrollTop - main.clientHeight < 60;
    if (pinned) main.scrollTop = main.scrollHeight;
  }, [messages]);

  return (
    <div
      className="feed"
      id="feed"
      ref={feedRef}
      role="log"
      aria-live="polite"
      aria-label="Переписка"
      onClick={onFeedClick}
    >
      {messages.map((m) => (
        <div key={m.id} className={`msg msg--${m.role}`}>
          {m.role === 'ai' && <span className="msg__orb" />}
          <div className="msg__body">
            {m.role === 'ai' && m.status === 'thinking' ? (
              <div className="dots"><i /><i /><i /></div>
            ) : m.role === 'ai' ? (
              <div
                dangerouslySetInnerHTML={{
                  __html: md(m.content) + (m.status === 'revealing' ? '<span class="caret"></span>' : ''),
                }}
              />
            ) : (
              <p>{m.content}</p>
            )}
          </div>
          {m.role === 'ai' && m.status === 'done' && <ActionBadge action={m.action} />}
          {m.role === 'ai' && m.status === 'done' && <Copy text={m.content} />}
        </div>
      ))}
    </div>
  );
}
