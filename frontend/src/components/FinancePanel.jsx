import { useEffect, useMemo, useState } from 'react';
import { fetchFinanceSummary, fetchFinanceTransactions } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

const MONTH_FORMATTERS = {};

function formatMonth(month, locale) {
  // month is "YYYY-MM" — turn it into a locale month/year label without
  // pulling in a date-formatting library for one string.
  const key = locale;
  if (!MONTH_FORMATTERS[key]) {
    MONTH_FORMATTERS[key] = new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' });
  }
  const [year, m] = month.split('-').map(Number);
  return MONTH_FORMATTERS[key].format(new Date(year, m - 1, 1));
}

const LOCALE_BY_LANG = { ru: 'ru-RU', en: 'en-US' };

export default function FinancePanel({ open, onClose }) {
  const { t, lang } = useI18n();
  const locale = LOCALE_BY_LANG[lang] || 'ru-RU';
  const [transactions, setTransactions] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError('');
    Promise.all([fetchFinanceTransactions().then(setTransactions), fetchFinanceSummary().then(setSummary)])
      .catch(() => setError(t('financePanel.loadError')))
      .finally(() => setLoading(false));
  }, [open]);

  const groups = useMemo(() => {
    const byMonth = new Map();
    for (const tx of transactions) {
      const month = tx.date.slice(0, 7);
      if (!byMonth.has(month)) byMonth.set(month, []);
      byMonth.get(month).push(tx);
    }
    return [...byMonth.entries()];
  }, [transactions]);

  if (!open) return null;

  return (
    <div className="finance-panel">
      <div className="finance-panel__backdrop" onClick={onClose} />
      <div className="finance-panel__body">
        <div className="finance-panel__head">
          <h2>{t('common.finance')}</h2>
          <button type="button" className="ghost" onClick={onClose}>{t('common.close')}</button>
        </div>

        {loading && <p className="finance-panel__hint">{t('common.loading')}</p>}
        {error && <p className="finance-panel__hint finance-panel__hint--error">{error}</p>}

        {summary && (
          <section className="finance-summary">
            <div className="finance-summary__row">
              <span className="finance-summary__label">{t('financePanel.balance')}</span>
              <span className={`finance-summary__value ${summary.balance >= 0 ? 'finance-summary__value--positive' : 'finance-summary__value--negative'}`}>
                {summary.balance >= 0 ? '+' : ''}{summary.balance.toLocaleString(locale)} ₽
              </span>
            </div>
            <div className="finance-summary__row finance-summary__row--muted">
              <span>{t('financePanel.totalIncome')}: {summary.total_income.toLocaleString(locale)} ₽</span>
              <span>{t('financePanel.totalExpense')}: {summary.total_expense.toLocaleString(locale)} ₽</span>
            </div>
          </section>
        )}

        {!loading && transactions.length === 0 && !error && (
          <p className="finance-panel__hint">{t('financePanel.noTransactions')}</p>
        )}

        {groups.map(([month, txs]) => (
          <section key={month}>
            <h3>{formatMonth(month, locale)}</h3>
            <ul className="finance-list">
              {txs.map((tx) => (
                <li key={tx.id} className="finance-list__item">
                  <div className="finance-list__main">
                    <span className="finance-list__category">{tx.category}</span>
                    <span className="finance-list__date">{tx.date}</span>
                  </div>
                  {tx.note && <span className="finance-list__note">{tx.note}</span>}
                  <span className={`finance-list__amount ${tx.type === 'income' ? 'finance-list__amount--income' : 'finance-list__amount--expense'}`}>
                    {tx.type === 'income' ? '+' : '−'}{tx.amount.toLocaleString(locale)} ₽
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
