const RING_RADIUS = 15;
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;

// A filling ring with the percentage inside, plus a caption next to it —
// used by the chat's "saving to memory" card and, after a page reload
// mid-import, under the composer.
export default function MemoryProgress({ percent, caption }) {
  return (
    <div className="memory-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={caption}>
      <span className="memory-progress__ring">
        <svg viewBox="0 0 36 36" aria-hidden="true">
          <circle className="memory-progress__track" cx="18" cy="18" r={RING_RADIUS} />
          <circle
            className="memory-progress__fill"
            cx="18"
            cy="18"
            r={RING_RADIUS}
            strokeDasharray={RING_LENGTH}
            strokeDashoffset={RING_LENGTH * (1 - percent / 100)}
          />
        </svg>
        <span className="memory-progress__percent">{percent}%</span>
      </span>
      {caption ? <span className="memory-progress__caption">{caption}</span> : null}
    </div>
  );
}
