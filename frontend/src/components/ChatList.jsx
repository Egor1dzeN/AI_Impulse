import { useEffect, useState } from 'react';
import { deleteConversation, fetchConversations } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

export default function ChatList({ open, activeId, onSelect, onDeleted, onClose }) {
  const { t } = useI18n();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetchConversations()
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  async function remove(id, e) {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      onDeleted(id);
    } catch {
      // leave the item in place if the delete failed
    }
  }

  return (
    <div className="chat-list">
      <div className="chat-list__backdrop" onClick={onClose} />
      <div className="chat-list__body">
        <div className="chat-list__head">
          <h2>{t('common.chats')}</h2>
          <button type="button" className="ghost" onClick={onClose}>{t('common.close')}</button>
        </div>

        {loading && <p className="chat-list__hint">{t('common.loading')}</p>}
        {!loading && items.length === 0 && (
          <p className="chat-list__hint">{t('chatList.noChats')}</p>
        )}

        <ul className="chat-list__items">
          {items.map((c) => (
            <li
              key={c.id}
              className={`chat-list__item${c.id === activeId ? ' chat-list__item--active' : ''}`}
            >
              <button
                type="button"
                className="chat-list__item-title"
                onClick={() => {
                  onSelect(c.id);
                  onClose();
                }}
              >
                {c.title}
              </button>
              <button
                type="button"
                className="chat-list__item-remove"
                aria-label={t('chatList.deleteAriaLabel')}
                onClick={(e) => remove(c.id, e)}
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
