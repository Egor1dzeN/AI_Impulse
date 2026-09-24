import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import OrbCanvas from './components/OrbCanvas';
import Chat from './components/Chat';
import Composer from './components/Composer';
import MemoryPanel from './components/MemoryPanel';
import FinancePanel from './components/FinancePanel';
import ChatList from './components/ChatList';
import SettingsPanel from './components/SettingsPanel';
import Menu from './components/Menu';
import Login from './components/Login';
import {
  createConversation,
  extractDocument,
  fetchConversations,
  fetchHistory,
  fetchImportProgress,
  fetchProactive,
  importData,
  sendChat,
  UNAUTHORIZED_EVENT,
} from './lib/api';
import { datasetNote } from './lib/attachments';
import { getUsername, isAuthenticated, logout } from './lib/auth';
import { getReasoningEnabled, setReasoningEnabled } from './lib/settings';
import { useI18n } from './lib/i18n.jsx';
import './styles.css';

const uid = () => Math.random().toString(36).slice(2);

// How long the memory card "thinks" over an attached dataset before
// announcing what it found — the import itself starts immediately.
const MEMORY_CARD_THINKING_MS = 10000;

export default function App() {
  const { t } = useI18n();
  const CHIPS = t('chips');
  const [authed, setAuthed] = useState(isAuthenticated);
  const [messages, setMessages] = useState([]);
  const [memoryProgress, setMemoryProgress] = useState(null); // /api/import/progress while memory is filling
  const progressPollRef = useRef(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [isChat, setIsChat] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [financeOpen, setFinanceOpen] = useState(false);
  const [chatListOpen, setChatListOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [sendTick, setSendTick] = useState(0);
  const [historyScrollTick, setHistoryScrollTick] = useState(0);
  const [reasoning, setReasoning] = useState(getReasoningEnabled);
  const [proactive, setProactive] = useState([]);

  const orbRef = useRef(null);
  const mainRef = useRef(null);

  // What the assistant noticed on its own (overdue replies, conflicts,
  // spending spikes...) — refreshed whenever the start screen is shown.
  useEffect(() => {
    if (!authed || isChat) return;
    let cancelled = false;
    fetchProactive()
      .then((res) => {
        if (!cancelled) setProactive(res.suggestions || []);
      })
      .catch(() => {
        if (!cancelled) setProactive([]);
      });
    return () => {
      cancelled = true;
    };
  }, [authed, isChat]);
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

  // Google's OAuth redirect lands back here with ?google_calendar=... or
  // ?gmail=... (see main.py's /api/google-calendar/callback and
  // /api/gmail/callback) — reopen Settings so the result (SettingsPanel
  // reads/clears the same params) is actually visible instead of landing
  // silently on the chat screen.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('google_calendar') || params.has('gmail')) {
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

  // The hero grows after first paint (the "Ассистент заметил" cards arrive
  // asynchronously, the language can change): re-place the composer whenever
  // its height changes, or the composer stays mid-page on top of the chips.
  useEffect(() => {
    const hero = heroRef.current;
    if (!hero || isChat || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => lift());
    ro.observe(hero);
    return () => ro.disconnect();
  }, [isChat]);

  // On the start screen the orb is part of the page: when the hero (with the
  // "Ассистент заметил" cards) is taller than the window, scrolling moves the
  // orb up together with it instead of leaving it fixed on top of the cards.
  useEffect(() => {
    const main = mainRef.current;
    const root = document.documentElement;
    if (!main || isChat) {
      root.style.setProperty('--hero-scroll', '0px');
      return undefined;
    }
    let idle;
    function onScroll() {
      root.style.setProperty('--hero-scroll', `${main.scrollTop}px`);
      // Fades out as it leaves, so no half-orb lingers behind the header and the text scrolled under it.
      root.style.setProperty('--hero-fade', String(Math.max(0, 1 - main.scrollTop / 220)));
      document.body.classList.add('is-hero-scrolling');
      clearTimeout(idle);
      idle = setTimeout(() => document.body.classList.remove('is-hero-scrolling'), 150);
    }
    onScroll();
    main.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      main.removeEventListener('scroll', onScroll);
      clearTimeout(idle);
      document.body.classList.remove('is-hero-scrolling');
      root.style.setProperty('--hero-scroll', '0px');
      root.style.setProperty('--hero-fade', '1');
    };
  }, [isChat]);

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

  // Polls the background memory filling (profile build + fact extraction)
  // until it finishes, then marks the chat's "saving to memory" cards done.
  // Also runs once on login, picking an unfinished import back up after a
  // page reload.
  function pollMemoryProgress() {
    clearTimeout(progressPollRef.current);
    fetchImportProgress()
      .then((p) => {
        if (p.active) {
          setMemoryProgress(p);
          progressPollRef.current = setTimeout(pollMemoryProgress, 1500);
        } else {
          setMemoryProgress(null);
          setMessages((prev) =>
            prev.map((m) => (m.kind === 'memory' && m.status === 'saving' ? { ...m, status: 'done' } : m))
          );
        }
      })
      .catch(() => {
        progressPollRef.current = setTimeout(pollMemoryProgress, 3000);
      });
  }

  useEffect(() => {
    if (!authed) return;
    pollMemoryProgress();
    return () => clearTimeout(progressPollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  // Dataset files (email / Telegram / calendar exports) always go through
  // the full import pipeline into long-term memory; the chat shows it as
  // the assistant noticing personal data in the file. Returns the text to
  // pass on to the model: a short note per loaded file, or — for a JSON
  // that turned out not to be a dataset (422) — its document block.
  async function loadDatasets(datasets, cardId) {
    let imported = false;
    const thinking = new Promise((resolve) => setTimeout(resolve, MEMORY_CARD_THINKING_MS)).then(() => {
      // A slow import outlasts the pause — keep showing it's being read.
      if (!imported) updateMessage(cardId, { status: 'importing' });
    });
    const notes = [];
    const failed = [];
    let records = 0;
    for (const a of datasets) {
      try {
        const res = await importData({ file: a.file });
        records += res.imported;
        notes.push(datasetNote(a.name, t(`composer.source_${res.source}`), res.imported));
      } catch (err) {
        if (err.status === 422) {
          try {
            notes.push((await extractDocument(a.file)).block);
          } catch {
            failed.push(a.name);
          }
        } else {
          failed.push(a.name);
        }
      }
    }
    imported = true;
    await thinking;
    updateMessage(cardId, records ? { status: 'saving', records, failed } : { status: 'error', failed });
    if (records) pollMemoryProgress();
    return notes;
  }

  function updateMessage(id, patch) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }

  async function send(rawText, attachments = []) {
    const text = (rawText || '').trim();
    if ((!text && !attachments.length) || busy) return;

    if (!isChat) setIsChat(true);

    const datasets = attachments.filter((a) => a.kind === 'dataset');
    const documentBlocks = attachments.filter((a) => a.kind !== 'dataset').map((a) => a.content);

    setMessages((prev) => [
      ...prev,
      { id: uid(), role: 'you', content: text, files: attachments.map((a) => a.name), status: 'done' },
    ]);
    setSendTick((t) => t + 1);
    setInput('');
    setBusy(true);
    orbRef.current?.setEnergy(1);
    abortedRef.current = false;

    let datasetNotes = [];
    if (datasets.length) {
      const cardId = uid();
      setMessages((prev) => [
        ...prev,
        { id: cardId, role: 'ai', kind: 'memory', files: datasets.map((a) => a.name), status: 'thinking' },
      ]);
      datasetNotes = await loadDatasets(datasets, cardId);
    }

    // A bare file upload needs no model reply — the memory card is the answer.
    if (!text && !documentBlocks.length) {
      setBusy(false);
      orbRef.current?.setEnergy(0);
      return;
    }
    const outgoing = [text, ...documentBlocks, ...datasetNotes].filter(Boolean).join('\n\n');

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
      await sendChat(convId, outgoing, controller.signal, (event) => {
        if (abortedRef.current) return;
        if (event.type === 'token') {
          acc += event.text;
          updateMessage(aiId, { content: acc, status: 'revealing' });
        } else if (event.type === 'reset') {
          // The backend rejected what streamed so far (it claimed an action
          // no tool performed) and is answering again — drop that text.
          acc = '';
          updateMessage(aiId, { content: '', status: 'thinking' });
        } else if (event.type === 'done') {
          action = event.action;
          // The backend may have rewritten the saved text (e.g. cleaning
          // up a raw "[sources=[N]]" citation tag the model streamed as
          // tokens) — prefer that final version over our own accumulation
          // so what's shown matches what's actually saved.
          if (typeof event.reply === 'string') acc = event.reply;
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

  // Any 401 from the API (expired token, deleted account) means this session
  // is gone: stop polling and show the login screen instead of a dead page.
  useEffect(() => {
    const onUnauthorized = () => {
      clearTimeout(progressPollRef.current);
      signOut();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          <button className="ghost" type="button" onClick={() => setFinanceOpen(true)}>
            {t('common.finance')}
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
            {proactive.length > 0 && (
              <div className="proactive">
                <p className="proactive__title">{t('proactive.title')}</p>
                {proactive.map((p) => (
                  <button
                    key={p.title}
                    type="button"
                    className={`proactive__item proactive__item--${p.priority}`}
                    onClick={() => send(p.prompt)}
                  >
                    <span className="proactive__head">{p.title}</span>
                    <span className="proactive__details">{p.details}</span>
                    <span className="proactive__action">{p.suggested_action}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="chips" id="chips">
              {CHIPS.map((c) => (
                <button key={c} className="chip" type="button" onClick={() => send(c)}>
                  {c}
                </button>
              ))}
            </div>
          </section>
        )}

        <Chat
          messages={messages}
          memoryProgress={memoryProgress}
          scrollSignal={sendTick}
          smoothScrollSignal={historyScrollTick}
        />
      </main>

      <Composer
        value={input}
        onChange={setInput}
        onSend={send}
        onStop={stop}
        busy={busy}
        reasoning={reasoning}
        onToggleReasoning={toggleReasoning}
        progress={messages.some((m) => m.kind === 'memory' && m.status !== 'done' && m.status !== 'error') ? null : memoryProgress}
      />

      <MemoryPanel open={memoryOpen} onClose={() => setMemoryOpen(false)} />

      <FinancePanel open={financeOpen} onClose={() => setFinanceOpen(false)} />

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
