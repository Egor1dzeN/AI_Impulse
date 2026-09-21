import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import OrbCanvas from './components/OrbCanvas';
import Chat from './components/Chat';
import Composer from './components/Composer';
import MemoryPanel from './components/MemoryPanel';
import ChatList from './components/ChatList';
import SettingsPanel from './components/SettingsPanel';
import Menu from './components/Menu';
import Login from './components/Login';
import { createConversation, fetchConversations, fetchHistory, sendChat } from './lib/api';
import { getUsername, isAuthenticated, logout } from './lib/auth';
import { getReasoningEnabled, setReasoningEnabled } from './lib/settings';
import { useI18n } from './lib/i18n.jsx';
import './styles.css';

const uid = () => Math.random().toString(36).slice(2);

export default function App() {
  const { t } = useI18n();
  const CHIPS = t('chips');
  const [authed, setAuthed] = useState(isAuthenticated);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [isChat, setIsChat] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [chatListOpen, setChatListOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [sendTick, setSendTick] = useState(0);
  const [historyScrollTick, setHistoryScrollTick] = useState(0);
  const [reasoning, setReasoning] = useState(getReasoningEnabled);

  const orbRef = useRef(null);
  const mainRef = useRef(null);
  const heroRef = useRef(null);
  const abortedRef = useRef(false);
  const controllerRef = useRef(null);

  useEffect(() => {
    if (!authed) return;
    fetchConversations()
      .then((list) => {
        if (!list.length) return;
        const latest = list[0];
        setConversationId(latest.id);
        return fetchHistory(latest.id).then((rows) => {
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
            setHistoryScrollTick((t) => t + 1);
          }
        });
      })
      .catch(() => {})
      .finally(() => setHistoryLoaded(true));
  }, [authed]);

  useEffect(() => {
    lift(true);
    window.addEventListener('resize', () => lift(true));
  }, []);

  // Google's OAuth redirect lands back here with ?google_calendar=... (see
  // main.py's /api/google-calendar/callback) — reopen Settings so the
  // result (SettingsPanel reads/clears the same param) is actually visible
  // instead of landing silently on the chat screen.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has('google_calendar')) {
      setSettingsOpen(true);
    }
  }, []);

  // Must run before Chat's own effects (which force-scroll on send) so the
  // feed is already visible/laid out by the time they read scrollHeight —
  // layout effects across the tree flush before any regular effects do,
  // unlike a plain useEffect here which Chat's child effect would beat.
  useLayoutEffect(() => {
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
    setSendTick((t) => t + 1);
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
      let convId = conversationId;
      if (!convId) {
        const conv = await createConversation();
        convId = conv.id;
        setConversationId(convId);
      }
      await sendChat(convId, text, controller.signal, (event) => {
        if (abortedRef.current) return;
        if (event.type === 'token') {
          acc += event.text;
          updateMessage(aiId, { content: acc, status: 'revealing' });
        } else if (event.type === 'done') {
          action = event.action;
        }
      }, reasoning);
      if (abortedRef.current) {
        setMessages((prev) => prev.filter((m) => m.id !== aiId));
        return;
      }
      updateMessage(aiId, { content: acc, status: 'done', action });
    } catch (err) {
      if (!abortedRef.current) {
        if (String(err.message).startsWith('401')) {
          logout();
          setAuthed(false);
          setMessages((prev) => prev.filter((m) => m.id !== aiId));
          return;
        }
        updateMessage(aiId, {
          content: t('replyError'),
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

  function startNewChat() {
    // Already on a blank, unsaved chat — nothing to reset.
    if (!conversationId && messages.length === 0) return;

    abortedRef.current = true;
    controllerRef.current?.abort();
    setMessages([]);
    setIsChat(false);
    setBusy(false);
    // No DB row yet — created lazily by send() on the first real message,
    // so idle clicks on "Новый чат" don't litter the list with empties.
    setConversationId(null);
  }

  async function switchConversation(id) {
    if (id === conversationId) return;
    abortedRef.current = true;
    controllerRef.current?.abort();
    setBusy(false);
    setConversationId(id);
    try {
      const rows = await fetchHistory(id);
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
      setHistoryScrollTick((t) => t + 1);
    } catch {
      // leave whatever was on screen if the switch failed
    }
  }

  function handleConversationDeleted(id) {
    if (id === conversationId) startNewChat();
  }

  function toggleReasoning() {
    setReasoning((prev) => {
      const next = !prev;
      setReasoningEnabled(next);
      return next;
    });
  }

  function signOut() {
    logout();
    setAuthed(false);
    setMessages([]);
    setIsChat(false);
    setConversationId(null);
  }

  if (!authed) {
    return <Login onAuthed={() => setAuthed(true)} />;
  }

  return (
    <>
      <header className="bar">
        <div className="mark" id="mark">Impulse</div>
        <div className="bar__right">
          <button className="ghost" type="button" onClick={() => setChatListOpen(true)}>
            {t('common.chats')}
          </button>
          <button className="ghost" type="button" onClick={() => setMemoryOpen(true)}>
            {t('common.memory')}
          </button>
          <button className="ghost" id="reset" type="button" onClick={startNewChat}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
            {t('common.newChat')}
          </button>
          <Menu
            className="menu--account"
            button={(toggle) => (
              <button className="ghost" type="button" onClick={toggle}>
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <circle cx="8" cy="5.5" r="2.7" />
                  <path d="M2.8 13.5a5.2 5.2 0 0 1 10.4 0" />
                </svg>
                {getUsername() || t('common.account')}
              </button>
            )}
          >
            <button type="button" className="menu__item" onClick={() => setSettingsOpen(true)}>{t('common.settings')}</button>
            <button type="button" className="menu__item" onClick={signOut}>{t('common.signOut')}</button>
          </Menu>
        </div>
      </header>

      <OrbCanvas ref={orbRef} />

      <main className="main" id="main" ref={mainRef}>
        {!isChat && (
          <section className="hero" id="hero" ref={heroRef}>
            <h1 className="hero__title">{t('hero.title')}</h1>
            <p className="hero__sub">{t('hero.sub')}</p>
            <div className="chips" id="chips">
              {CHIPS.map((c) => (
                <button key={c} className="chip" type="button" onClick={() => send(c)}>
                  {c}
                </button>
              ))}
            </div>
          </section>
        )}

        <Chat messages={messages} scrollSignal={sendTick} smoothScrollSignal={historyScrollTick} />
      </main>

      <Composer
        value={input}
        onChange={setInput}
        onSend={send}
        onStop={stop}
        busy={busy}
        reasoning={reasoning}
        onToggleReasoning={toggleReasoning}
      />

      <MemoryPanel open={memoryOpen} onClose={() => setMemoryOpen(false)} />

      <ChatList
        open={chatListOpen}
        activeId={conversationId}
        onSelect={switchConversation}
        onDeleted={handleConversationDeleted}
        onClose={() => setChatListOpen(false)}
      />

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  );
}
