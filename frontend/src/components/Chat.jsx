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
  get_weather: 'Погода',
  get_exchange_rate: 'Курс валюты',
};

function ActionBadge({ action }) {
  if (!action || action.tool === 'show_map') return null;
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

function MapEmbed({ action }) {
  if (!action || action.tool !== 'show_map') return null;
  const result = action.result;
  if (!result) return null;

  if (!result.found) {
    return <p className="map-embed__miss">Не нашёл «{result.query}» на карте.</p>;
  }

  const { bbox, lat, lon, display_name } = result;
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&marker=${lat}%2C${lon}&layer=mapnik`;
  const bigMapHref = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=12/${lat}/${lon}`;

  return (
    <div className="map-embed">
      <iframe
        src={src}
        title={display_name}
        referrerPolicy="no-referrer-when-downgrade"
      />
      <a className="map-embed__link" href={bigMapHref} target="_blank" rel="noreferrer">
        {display_name}
      </a>
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

export default function Chat({ messages, scrollSignal, smoothScrollSignal }) {
  const feedRef = useRef(null);

  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    const main = el.closest('.main');
    if (!main) return;
    const pinned = main.scrollHeight - main.scrollTop - main.clientHeight < 60;
    if (pinned) main.scrollTop = main.scrollHeight;
  }, [messages]);

  // Sending a message should always jump to the bottom, even if the user
  // had scrolled up to read earlier history — unlike incoming AI tokens
  // above, this is a deliberate action and the "pinned" check shouldn't
  // gate it.
  useEffect(() => {
    if (!scrollSignal) return;
    const el = feedRef.current;
    if (!el) return;
    const main = el.closest('.main');
    if (!main) return;
    main.scrollTop = main.scrollHeight;
  }, [scrollSignal]);

  // Loaded chat history on page load — ease down to the latest message
  // instead of snapping, so it reads as a deliberate "catching you up".
  useEffect(() => {
    if (!smoothScrollSignal) return;
    const el = feedRef.current;
    if (!el) return;
    const main = el.closest('.main');
    if (!main) return;
    main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
  }, [smoothScrollSignal]);

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
          {m.role === 'you' ? (
            <div className="msg__body">
              <p>{m.content}</p>
            </div>
          ) : (
            <div className="msg__content">
              <div className="msg__body">
                {m.status === 'thinking' ? (
                  <div className="dots"><i /><i /><i /></div>
                ) : (
                  <div
                    dangerouslySetInnerHTML={{
                      __html: md(m.content) + (m.status === 'revealing' ? '<span class="caret"></span>' : ''),
                    }}
                  />
                )}
              </div>
              {m.status === 'done' && <MapEmbed action={m.action} />}
              {m.status === 'done' && <ActionBadge action={m.action} />}
              {m.status === 'done' && <Copy text={m.content} />}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
