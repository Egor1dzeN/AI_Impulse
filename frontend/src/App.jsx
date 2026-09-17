import { useEffect, useRef, useState } from 'react';
import OrbCanvas from './components/OrbCanvas';
import Chat from './components/Chat';
import Composer from './components/Composer';
import MemoryPanel from './components/MemoryPanel';
import { fetchHistory, getUserId, sendChat } from './lib/api';
import './styles.css';

const CHIPS = [
  'Хочу купить хлеб за 200 рублей',
  'Поставь задачу закончить отчёт до пятницы',
  'Я живу в Москве и учусь на программиста',
  'Сколько я потратил в этом месяце?',
];

const uid = () => Math.random().toString(36).slice(2);

export default function App() {
  const [userId] = useState(getUserId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [isChat, setIsChat] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const orbRef = useRef(null);
  const mainRef = useRef(null);
  const heroRef = useRef(null);
  const abortedRef = useRef(false);
  const controllerRef = useRef(null);

  useEffect(() => {
    fetchHistory(userId)
      .then((rows) => {
        if (rows.length) {
          setMessages(
            rows.map((r) => ({
              id: r.id,
              role: r.role === 'user' ? 'you' : 'ai',
              content: r.content,
              status: 'done',
              action: r.action,
            }))
          );
          setIsChat(true);
        }
      })
      .catch(() => {})
      .finally(() => setHistoryLoaded(true));
  }, [userId]);

  useEffect(() => {
    lift(true);
    window.addEventListener('resize', () => lift(true));
  }, []);

  useEffect(() => {
    document.body.classList.toggle('is-chat', isChat);
    if (!isChat) lift(true);
  }, [isChat]);

  // The composer's textarea grows as the user types (see Composer's own
  // grow()), which changes .dock's real height — recompute the lift so the
  // fixed translateY stays in sync with it instead of drifting off-screen.
  useEffect(() => {
    if (!isChat) lift();
  }, [input, isChat]);

  function lift() {
    const main = mainRef.current;
    const hero = heroRef.current;
    if (!main || !hero || document.body.classList.contains('is-chat')) return;
    const root = document.documentElement;
    root.style.setProperty('--idle-top', '0px');
    const dock = document.querySelector('.dock');
    if (!dock) return;
    dock.style.setProperty('--lift', '0px');
    const slack = main.clientHeight - hero.offsetHeight - 20;
    const shift = Math.max(0, slack) / 2;
    root.style.setProperty('--idle-top', Math.round(shift) + 'px');
    const gap = main.clientHeight - hero.offsetHeight - shift - 20;
    dock.style.setProperty('--lift', Math.max(0, Math.round(gap)) + 'px');
  }

  function updateMessage(id, patch) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;

    if (!isChat) setIsChat(true);

    setMessages((prev) => [...prev, { id: uid(), role: 'you', content: text, status: 'done' }]);
    setInput('');
    setBusy(true);
    orbRef.current?.setEnergy(1);
    abortedRef.current = false;

    const aiId = uid();
    setMessages((prev) => [...prev, { id: aiId, role: 'ai', content: '', status: 'thinking' }]);

    const controller = new AbortController();
    controllerRef.current = controller;

    let acc = '';
    let action = null;

    try {
      await sendChat(userId, text, controller.signal, (event) => {
        if (abortedRef.current) return;
        if (event.type === 'token') {
          acc += event.text;
          updateMessage(aiId, { content: acc, status: 'revealing' });
        } else if (event.type === 'done') {
          action = event.action;
        }
      });
      if (abortedRef.current) {
        setMessages((prev) => prev.filter((m) => m.id !== aiId));
        return;
      }
      updateMessage(aiId, { content: acc, status: 'done', action });
    } catch (err) {
      if (!abortedRef.current) {
        updateMessage(aiId, {
          content: 'Не получилось получить ответ. Проверьте, что бэкенд запущен.',
          status: 'done',
        });
      }
    } finally {
      setBusy(false);
      orbRef.current?.setEnergy(0);
    }
  }

  function stop() {
    abortedRef.current = true;
    controllerRef.current?.abort();
    setBusy(false);
    orbRef.current?.setEnergy(0);
  }

  function reset() {
    abortedRef.current = true;
    setMessages([]);
    setIsChat(false);
    setBusy(false);
  }

  return (
    <>
      <header className="bar">
        <div className="mark" id="mark">Impulse</div>
        <div className="bar__right">
          <button className="ghost" type="button" onClick={() => setMemoryOpen(true)}>
            Память
          </button>
          {isChat && (
            <button className="ghost" id="reset" type="button" onClick={reset}>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
              Новый чат
            </button>
          )}
        </div>
      </header>

      <OrbCanvas ref={orbRef} />

      <main className="main" id="main" ref={mainRef}>
        {!isChat && (
          <section className="hero" id="hero" ref={heroRef}>
            <h1 className="hero__title">О чём подумаем?</h1>
            <p className="hero__sub">
              Impulse запоминает то, что вы рассказываете, и умеет действовать: ставить задачи, записывать траты и показатели здоровья.
            </p>
            <div className="chips" id="chips">
              {CHIPS.map((c) => (
                <button key={c} className="chip" type="button" onClick={() => send(c)}>
                  {c}
                </button>
              ))}
            </div>
          </section>
        )}

        <Chat messages={messages} />
      </main>

      <Composer value={input} onChange={setInput} onSend={send} onStop={stop} busy={busy} />

      <MemoryPanel open={memoryOpen} userId={userId} onClose={() => setMemoryOpen(false)} />
    </>
  );
}
