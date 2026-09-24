import { Component, useEffect, useRef, useState } from 'react';
import { md } from '../lib/markdown';
import { getLanguage, translate, useI18n } from '../lib/i18n.jsx';
import { confirmCalendarAction, sendEmail } from '../lib/api';
import MapboxMap from './MapboxMap';
import MemoryProgress from './MemoryProgress';
import { splitAttachments } from '../lib/attachments';
import { progressCaption } from '../lib/memoryProgress';

// Shown instead of the plain dots while waiting for the first token —
// same idea as Claude's playful status words ("Pondering…", "Noodling…").
// Cycles through a random pick every couple of seconds so a longer wait
// (e.g. the agent working through a few tool calls before any text
// streams) doesn't just sit on one word the whole time.
const THINKING_INTERVAL_MS = 5200;

function pickThinkingWord(words, exclude) {
  if (words.length === 1) return words[0];
  let word;
  do {
    word = words[Math.floor(Math.random() * words.length)];
  } while (word === exclude);
  return word;
}

function ThinkingIndicator() {
  const { t } = useI18n();
  const words = t('chat.thinkingWords');
  const [word, setWord] = useState(() => pickThinkingWord(words));

  useEffect(() => {
    const id = setInterval(() => {
      setWord((prev) => pickThinkingWord(words, prev));
    }, THINKING_INTERVAL_MS);
    return () => clearInterval(id);
  }, [words]);

  return (
    <div className="thinking">
      <span key={word} className="thinking__word">{word}…</span>
      <div className="dots"><i /><i /><i /></div>
    </div>
  );
}

function ActionBadge({ action }) {
  const { t } = useI18n();
  const HIDDEN_TOOLS = [
    'show_map',
    'build_route',
    'get_workout_route',
    'draft_email',
    'create_calendar_event',
    'update_calendar_event',
    'delete_calendar_event',
  ];
  if (!action || HIDDEN_TOOLS.includes(action.tool)) return null;
  const label = t('chat.actions')[action.tool] || action.tool;
  return (
    <div className="action-badge">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M3 8.5 6.2 11.7 13 4.5" />
      </svg>
      {label}
    </div>
  );
}

const PLACE_MARKER_COLOR = '#FF5A00'; // var(--flare)
const ORIGIN_MARKER_COLOR = '#2ecc71';
const DEST_MARKER_COLOR = '#e74c3c';

function ExternalLinkIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M6.5 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V9.5" />
      <path d="M9.5 2H14v4.5" />
      <path d="M14 2 7.5 8.5" />
    </svg>
  );
}

function MapEmbed({ action }) {
  const { t } = useI18n();
  if (!action || action.tool !== 'show_map') return null;
  const result = action.result;
  if (!result) return null;

  if (!result.found) {
    return <p className="map-embed__miss">{t('chat.mapMiss', { query: result.query })}</p>;
  }

  const { lat, lon, display_name } = result;
  const bigMapHref = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=14/${lat}/${lon}`;

  return (
    <div className="map-embed">
      <MapboxMap markers={[{ lonlat: [lon, lat], color: PLACE_MARKER_COLOR }]} title={display_name} />
      <a className="map-embed__link" href={bigMapHref} target="_blank" rel="noreferrer">
        <ExternalLinkIcon />
        {display_name}
      </a>
    </div>
  );
}

const ROUTE_TRAVELMODE = { driving: 'driving', walking: 'walking', cycling: 'bicycling' };

function formatDuration(minutes, t) {
  if (minutes < 60) return `${minutes} ${t('chat.minutesShort')}`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} ${t('chat.hoursShort')} ${m} ${t('chat.minutesShort')}` : `${h} ${t('chat.hoursShort')}`;
}

// Built via Mapbox (Directions + an interactive GL JS map), unlike
// MapEmbed above which stays on plain OpenStreetMap for "just show me
// this place".
function RouteEmbed({ action }) {
  const { t } = useI18n();
  if (!action || action.tool !== 'build_route') return null;
  const result = action.result;
  if (!result) return null;

  if (!result.found) {
    return <p className="map-embed__miss">{t('chat.routeMiss', { query: result.query })}</p>;
  }

  const { origin, destination, distance_km, duration_min, origin_coords, destination_coords, route_geometry, profile } = result;
  const directionsHref =
    `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(origin)}` +
    `&destination=${encodeURIComponent(destination)}&travelmode=${ROUTE_TRAVELMODE[profile] || 'driving'}`;

  const markers = [
    { lonlat: origin_coords, color: ORIGIN_MARKER_COLOR },
    { lonlat: destination_coords, color: DEST_MARKER_COLOR },
  ];

  return (
    <div className="map-embed">
      <MapboxMap markers={markers} route={route_geometry} title={t('chat.routeTitle', { from: origin, to: destination })} />
      <a className="map-embed__link" href={directionsHref} target="_blank" rel="noreferrer">
        <ExternalLinkIcon />
        {origin} → {destination} · {distance_km} {t('chat.kmShort')} · {formatDuration(duration_min, t)}
      </a>
    </div>
  );
}

// GPS track of one Intervals.icu workout. Deliberately renders nothing
// when the workout has no track (result.has_route === false, e.g. it was
// logged manually via log_workout) — the tool's own instructions have the
// model describe the workout in plain text for that case instead, so there
// is nothing here for this component to add.
// One malformed tool result used to crash the whole app (black screen on
// every load of that conversation): an embed that throws now just vanishes.
class EmbedBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    console.error('embed failed to render', error);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function WorkoutRouteEmbed({ action }) {
  const { t } = useI18n();
  if (!action || action.tool !== 'get_workout_route') return null;
  const result = action.result;
  if (!result || !result.has_route || !Array.isArray(result.points)) return null;

  const { name, date, distance_km, duration_min, points, workout_id } = result;
  // Intervals.icu returns [lat, lon] pairs (null for GPS gaps — history saved
  // before the backend filtered them still has them); Mapbox wants [lon, lat].
  const routeGeometry = points
    .filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]))
    .map(([lat, lon]) => [lon, lat]);
  if (routeGeometry.length < 2) return null;
  const markers = [
    { lonlat: routeGeometry[0], color: ORIGIN_MARKER_COLOR },
    { lonlat: routeGeometry[routeGeometry.length - 1], color: DEST_MARKER_COLOR },
  ];
  const activityHref = `https://intervals.icu/activities/${workout_id}`;
  const dateLabel = date ? date.slice(0, 10) : '';

  return (
    <div className="map-embed">
      <MapboxMap markers={markers} route={routeGeometry} title={t('chat.workoutRouteTitle', { name })} />
      <a className="map-embed__link" href={activityHref} target="_blank" rel="noreferrer">
        <ExternalLinkIcon />
        {name} · {dateLabel} · {distance_km} {t('chat.kmShort')} · {formatDuration(duration_min, t)}
      </a>
    </div>
  );
}

// Only surface here — never sent by the agent itself (see tools.py's
// draft_email / TOPIC 1b comment and main.py's POST /api/gmail/send). This
// button click is the one and only place an email actually goes out.
function EmailDraftEmbed({ action }) {
  const { t } = useI18n();
  const [status, setStatus] = useState('idle'); // idle | sending | sent | error
  const draft = action?.tool === 'draft_email' ? action.result : null;
  // Seeded once from the tool's draft, then freely editable — onSend always
  // uses this local copy, never the original draft.* values, so edits
  // actually take effect.
  const [to, setTo] = useState(draft?.to || '');
  const [subject, setSubject] = useState(draft?.subject || '');
  const [body, setBody] = useState(draft?.body || '');
  if (!draft) return null;

  const locked = status === 'sending' || status === 'sent';

  const onSend = async () => {
    setStatus('sending');
    try {
      await sendEmail({ draftId: draft.draft_id, to, subject, body });
      setStatus('sent');
    } catch {
      setStatus('error');
    }
  };

  return (
    <div className="email-draft">
      <div className="email-draft__title">{t('chat.emailDraftTitle')}</div>
      <label className="email-draft__field">
        <span>{t('chat.emailDraftTo')}</span>
        <input type="email" value={to} onChange={(e) => setTo(e.target.value)} disabled={locked} />
      </label>
      <label className="email-draft__field">
        <span>{t('chat.emailDraftSubject')}</span>
        <input type="text" value={subject} onChange={(e) => setSubject(e.target.value)} disabled={locked} />
      </label>
      <textarea
        className="email-draft__body"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={locked}
        rows={Math.min(10, Math.max(3, body.split('\n').length))}
      />
      {status === 'sent' ? (
        <div className="email-draft__sent">{t('chat.emailDraftSent')}</div>
      ) : (
        <button type="button" className="ghost" onClick={onSend} disabled={status === 'sending' || !to.trim() || !subject.trim()}>
          {status === 'sending' ? t('chat.emailDraftSending') : t('chat.emailDraftSend')}
        </button>
      )}
      {status === 'error' && <p className="settings-panel__hint settings-panel__hint--error">{t('chat.emailDraftError')}</p>}
    </div>
  );
}

// datetime-local inputs want "YYYY-MM-DDTHH:MM" (no seconds); the backend's
// start_time is "YYYY-MM-DDTHH:MM:SS" — trim/pad at the edges instead of
// changing the wire format both sides agree on.
const toInputDatetime = (isoLocal) => (isoLocal ? isoLocal.slice(0, 16) : '');
const fromInputDatetime = (value) => (value ? `${value}:00` : '');

const CALENDAR_DRAFT_TOOLS = ['create_calendar_event', 'update_calendar_event', 'delete_calendar_event'];

// One row per event in a delete_calendar_event draft (there's always at
// least one, even for a single deletion — see draft_delete_calendar_events_impl).
// Status is owned by the parent (CalendarDeleteEmbed), not this row, so a
// bulk "confirm all" button there can drive every row's request without
// each row managing its own hidden state.
function CalendarDeleteRow({ event, status, errorMessage, onConfirm, onRemove }) {
  const { t } = useI18n();

  return (
    <li className="calendar-delete-row">
      <div className="calendar-delete-row__info">
        <span className="calendar-delete-row__summary">{event.summary}</span>
        <span className="calendar-delete-row__start">{event.start_time}</span>
      </div>
      {status === 'done' ? (
        <span className="calendar-delete-row__status">{t('chat.calendarDraftDoneDelete')}</span>
      ) : status === 'removed' ? (
        <span className="calendar-delete-row__status">{t('chat.calendarDraftCancelled')}</span>
      ) : status === 'error' ? (
        <span className="calendar-delete-row__status calendar-delete-row__status--error" title={errorMessage || ''}>
          {t('chat.calendarDraftError')}{errorMessage ? `: ${errorMessage}` : ''}
        </span>
      ) : (
        <div className="calendar-delete-row__actions">
          <button type="button" className="ghost" onClick={onConfirm} disabled={status === 'applying'}>
            {status === 'applying' ? t('chat.calendarDraftApplying') : t('chat.calendarDraftConfirmDelete')}
          </button>
          <button
            type="button"
            className="composer__attachment-remove"
            aria-label={t('chat.calendarDraftRemoveFromListAriaLabel')}
            onClick={onRemove}
            disabled={status === 'applying'}
          >
            ×
          </button>
        </div>
      )}
    </li>
  );
}

// Chat history saved before delete_calendar_event supported batches has the
// old single-event shape (draft_id/event_id/summary/start_time, no `events`
// array) — normalize it into a one-item list rather than crashing the whole
// page when an old conversation is reloaded.
function calendarDeleteEvents(draft) {
  if (Array.isArray(draft.events)) return draft.events;
  return [{ draft_id: draft.draft_id, event_id: draft.event_id, summary: draft.summary, start_time: draft.start_time }];
}

function CalendarDeleteEmbed({ draft }) {
  const { t } = useI18n();
  const events = calendarDeleteEvents(draft);
  const [rowState, setRowState] = useState({}); // draft_id -> { status, error }

  const statusOf = (id) => rowState[id]?.status || 'idle';

  const confirmOne = async (event) => {
    setRowState((prev) => ({ ...prev, [event.draft_id]: { status: 'applying' } }));
    try {
      await confirmCalendarAction({ draftId: event.draft_id, kind: 'delete', eventId: event.event_id });
      setRowState((prev) => ({ ...prev, [event.draft_id]: { status: 'done' } }));
    } catch (err) {
      setRowState((prev) => ({ ...prev, [event.draft_id]: { status: 'error', error: err?.message } }));
    }
  };

  const removeOne = (id) => setRowState((prev) => ({ ...prev, [id]: { status: 'removed' } }));

  const pending = events.filter((e) => statusOf(e.draft_id) === 'idle');
  const anyApplying = events.some((e) => statusOf(e.draft_id) === 'applying');

  const confirmAll = async () => {
    // Sequential, not Promise.all — a burst of N simultaneous deletes is
    // both unkind to the Calendar API and, if one token error repeats
    // across all of them, harder to read than one row failing at a time.
    for (const event of pending) {
      await confirmOne(event);
    }
  };

  return (
    <div className="email-draft">
      <div className="email-draft__title">{t('chat.calendarDraftTitleDelete')}</div>
      {events.length > 1 && pending.length > 0 && (
        <button type="button" className="ghost ghost--danger" onClick={confirmAll} disabled={anyApplying}>
          {anyApplying ? t('chat.calendarDraftApplying') : t('chat.calendarDraftConfirmAll', { count: pending.length })}
        </button>
      )}
      <ul className="calendar-delete-list">
        {events.map((event) => (
          <CalendarDeleteRow
            key={event.draft_id}
            event={event}
            status={statusOf(event.draft_id)}
            errorMessage={rowState[event.draft_id]?.error}
            onConfirm={() => confirmOne(event)}
            onRemove={() => removeOne(event.draft_id)}
          />
        ))}
      </ul>
    </div>
  );
}

// Same pattern as EmailDraftEmbed above (see tools.py's TOPIC 1 comment):
// the agent only ever proposes a create/update/delete via one of the three
// draft_* tools below — the actual Google Calendar API call happens only
// when the user presses Confirm here, via POST /api/calendar/confirm.
function CalendarActionEmbed({ action }) {
  const { t } = useI18n();
  const draft = CALENDAR_DRAFT_TOOLS.includes(action?.tool) ? action.result : null;
  const [status, setStatus] = useState('idle'); // idle | applying | done | cancelled | error
  const [title, setTitle] = useState(draft?.title || '');
  const [startTime, setStartTime] = useState(toInputDatetime(draft?.start_time));
  const [durationMinutes, setDurationMinutes] = useState(draft?.duration_minutes ?? 30);
  const [notes, setNotes] = useState(draft?.notes || '');
  const [attendees, setAttendees] = useState((draft?.attendees || []).join(', '));
  // The tool found the same event already on the calendar and made no draft.
  if (!draft || draft.duplicate) return null;

  if (draft.kind === 'delete') return <CalendarDeleteEmbed draft={draft} />;

  const kind = draft.kind; // 'create' | 'update'
  const locked = status === 'applying' || status === 'done' || status === 'duplicate' || status === 'cancelled';

  const onConfirm = async () => {
    setStatus('applying');
    try {
      const res = await confirmCalendarAction({
        draftId: draft.draft_id,
        kind,
        eventId: draft.event_id,
        title,
        startTime: fromInputDatetime(startTime),
        durationMinutes,
        notes,
        attendees: kind === 'create' ? attendees.split(/[,;\s]+/).filter(Boolean) : undefined,
      });
      setStatus(res?.duplicate ? 'duplicate' : 'done');
    } catch {
      setStatus('error');
    }
  };

  const doneLabel = { create: t('chat.calendarDraftDoneCreate'), update: t('chat.calendarDraftDoneUpdate') }[kind];
  const titleLabel = { create: t('chat.calendarDraftTitleCreate'), update: t('chat.calendarDraftTitleUpdate') }[kind];

  return (
    <div className="email-draft">
      <div className="email-draft__title">{titleLabel}</div>
      <label className="email-draft__field">
        <span>{t('chat.calendarDraftName')}</span>
        <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} disabled={locked} />
      </label>
      <label className="email-draft__field">
        <span>{t('chat.calendarDraftStart')}</span>
        <input type="datetime-local" value={startTime} onChange={(e) => setStartTime(e.target.value)} disabled={locked} />
      </label>
      <label className="email-draft__field">
        <span>{t('chat.calendarDraftDuration')}</span>
        <input
          type="number"
          min="1"
          value={durationMinutes}
          onChange={(e) => setDurationMinutes(Number(e.target.value))}
          disabled={locked}
        />
      </label>
      {kind === 'create' && (
        <label className="email-draft__field">
          <span>{t('chat.calendarDraftAttendees')}</span>
          <input
            type="text"
            value={attendees}
            placeholder="name@example.com"
            onChange={(e) => setAttendees(e.target.value)}
            disabled={locked}
          />
        </label>
      )}
      <textarea
        className="email-draft__body"
        placeholder={t('chat.calendarDraftNotes')}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        disabled={locked}
        rows={Math.min(6, Math.max(2, (notes || '').split('\n').length))}
      />
      {status === 'done' ? (
        <div className="email-draft__sent">{doneLabel}</div>
      ) : status === 'duplicate' ? (
        <div className="email-draft__sent">{t('chat.calendarDraftDuplicate')}</div>
      ) : status === 'cancelled' ? (
        <div className="email-draft__sent">{t('chat.calendarDraftCancelled')}</div>
      ) : (
        <div className="email-draft__actions">
          <button type="button" className="ghost" onClick={onConfirm} disabled={status === 'applying' || !title.trim()}>
            {status === 'applying' ? t('chat.calendarDraftApplying') : t('chat.calendarDraftConfirm')}
          </button>
          <button type="button" className="ghost" onClick={() => setStatus('cancelled')} disabled={status === 'applying'}>
            {t('chat.calendarDraftCancel')}
          </button>
        </div>
      )}
      {status === 'error' && <p className="settings-panel__hint settings-panel__hint--error">{t('chat.calendarDraftError')}</p>}
    </div>
  );
}

function Copy({ text }) {
  const { t } = useI18n();
  const btnRef = useRef(null);
  const onClick = () => {
    navigator.clipboard.writeText(text).then(() => {
      const btn = btnRef.current;
      if (!btn) return;
      btn.textContent = t('chat.copied');
      setTimeout(() => {
        if (btn) btn.textContent = t('chat.copy');
      }, 1600);
    });
  };
  return (
    <button ref={btnRef} type="button" className="copy" onClick={onClick}>
      {t('chat.copy')}
    </button>
  );
}

// Code blocks are injected as raw HTML (dangerouslySetInnerHTML), so their
// copy buttons can't take a React onClick — handle clicks via delegation
// on the feed container instead. Not a component, so it reads the current
// language directly rather than through the i18n context.
function FileChips({ files }) {
  if (!files.length) return null;
  return (
    <ul className="msg__files">
      {files.map((name, i) => (
        <li key={`${name}-${i}`} className="msg__file">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M9.5 1.5H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5Z" />
            <path d="M9.5 1.5V5H13" />
          </svg>
          <span>{name}</span>
        </li>
      ))}
    </ul>
  );
}

// Attachments render as file chips, never as their raw text — both for a
// fresh message (files passed explicitly) and for history from the server
// (split back out of the stored message).
function YouMessage({ message }) {
  const { text, files } = message.files
    ? { text: message.content, files: message.files }
    : splitAttachments(message.content);
  return (
    <div className="msg__body">
      <FileChips files={files} />
      {text ? <p>{text}</p> : null}
    </div>
  );
}

// The assistant "noticing" personal data in an attached dataset: the file
// goes through the import pipeline and this card tracks the memory filling
// up (profile build + fact extraction) until it's done.
function MemoryCard({ card, progress }) {
  const { t } = useI18n();
  const files = card.files.join('», «');
  if (card.status === 'thinking') return <ThinkingIndicator />;
  if (card.status === 'error') {
    return <p className="memory-card__title memory-card__title--error">{t('chat.memoryCardError', { files })}</p>;
  }
  const title = {
    importing: t('chat.memoryCardImporting', { files }),
    saving: t('chat.memoryCardSaving', { files }),
    done: t('chat.memoryCardDone', { files, count: card.records }),
  }[card.status];
  let ring;
  if (card.status === 'done') ring = <MemoryProgress percent={100} caption={t('chat.memoryCardReady')} />;
  else if (card.status === 'saving' && progress) ring = <MemoryProgress percent={progress.percent} caption={progressCaption(progress, t)} />;
  else ring = <MemoryProgress percent={0} caption={t('chat.memoryCardReading')} />;
  return (
    <div className="memory-card">
      <p className="memory-card__title">{title}</p>
      {ring}
      {card.failed?.length ? (
        <p className="memory-card__title memory-card__title--error">
          {t('chat.memoryCardError', { files: card.failed.join('», «') })}
        </p>
      ) : null}
    </div>
  );
}

function onFeedClick(e) {
  const btn = e.target.closest('.codeblock__copy');
  if (!btn) return;
  const code = btn.closest('.codeblock')?.querySelector('code');
  if (!code) return;
  const copiedLabel = translate(getLanguage(), 'chat.copied');
  navigator.clipboard.writeText(code.textContent).then(() => {
    const prev = btn.textContent;
    btn.textContent = copiedLabel;
    clearTimeout(btn._resetTimer);
    btn._resetTimer = setTimeout(() => { btn.textContent = prev; }, 1600);
  });
}

export default function Chat({ messages, memoryProgress, scrollSignal, smoothScrollSignal }) {
  const { t, lang } = useI18n();
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
      aria-label={t('chat.ariaLabel')}
      onClick={onFeedClick}
    >
      {messages.map((m) => (
        <div key={m.id} className={`msg msg--${m.role}`}>
          {m.role === 'ai' && <span className="msg__orb" />}
          {m.role === 'you' ? (
            <YouMessage message={m} />
          ) : m.kind === 'memory' ? (
            <div className="msg__content">
              <div className="msg__body">
                <MemoryCard card={m} progress={memoryProgress} />
              </div>
            </div>
          ) : (
            <div className="msg__content">
              <div className="msg__body">
                {m.status === 'thinking' ? (
                  <ThinkingIndicator />
                ) : (
                  <div
                    dangerouslySetInnerHTML={{
                      __html: md(m.content, lang) + (m.status === 'revealing' ? '<span class="caret"></span>' : ''),
                    }}
                  />
                )}
              </div>
              {m.status === 'done' && (
                <EmbedBoundary>
                  <MapEmbed action={m.action} />
                  <RouteEmbed action={m.action} />
                  <WorkoutRouteEmbed action={m.action} />
                  <EmailDraftEmbed action={m.action} />
                  <CalendarActionEmbed action={m.action} />
                  <ActionBadge action={m.action} />
                </EmbedBoundary>
              )}
              {m.status === 'done' && <Copy text={m.content} />}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
