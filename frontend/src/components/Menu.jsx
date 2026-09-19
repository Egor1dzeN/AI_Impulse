import { useEffect, useRef, useState } from 'react';

export default function Menu({ className = '', button, children, openOn = 'click' }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const closeTimer = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  const hoverProps =
    openOn === 'hover'
      ? {
          onMouseEnter: () => {
            clearTimeout(closeTimer.current);
            setOpen(true);
          },
          onMouseLeave: () => {
            // Small delay so moving the cursor from the button into the
            // list itself (a real gap in between) doesn't close it first.
            closeTimer.current = setTimeout(() => setOpen(false), 150);
          },
        }
      : {};

  return (
    <div className={`menu ${className}`} ref={rootRef} {...hoverProps}>
      {button(() => setOpen((v) => !v), open)}
      {open && (
        <div className="menu__list" onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}
