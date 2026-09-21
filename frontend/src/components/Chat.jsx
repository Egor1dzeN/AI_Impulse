import { useEffect, useRef, useState } from 'react';
import { md } from '../lib/markdown';
import { getLanguage, translate, useI18n } from '../lib/i18n.jsx';
import MapboxMap from './MapboxMap';

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
  if (!action || action.tool === 'show_map' || action.tool === 'build_route' || action.tool === 'get_workout_route') return null;
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
function WorkoutRouteEmbed({ action }) {
  const { t } = useI18n();
  if (!action || action.tool !== 'get_workout_route') return null;
  const result = action.result;
  if (!result || !result.has_route || !result.points || result.points.length < 2) return null;

  const { name, date, distance_km, duration_min, points, workout_id } = result;
  // Intervals.icu returns [lat, lon] pairs; Mapbox wants [lon, lat].
  const routeGeometry = points.map(([lat, lon]) => [lon, lat]);
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

export default function Chat({ messages, scrollSignal, smoothScrollSignal }) {
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
            <div className="msg__body">
              <p>{m.content}</p>
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
              {m.status === 'done' && <MapEmbed action={m.action} />}
              {m.status === 'done' && <RouteEmbed action={m.action} />}
              {m.status === 'done' && <WorkoutRouteEmbed action={m.action} />}
              {m.status === 'done' && <ActionBadge action={m.action} />}
              {m.status === 'done' && <Copy text={m.content} />}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
