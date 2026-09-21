import { useEffect, useRef, useState } from 'react';
import { GOOGLE_CLIENT_ID, login, loginWithGoogle, register } from '../lib/auth';
import { useI18n } from '../lib/i18n.jsx';

export default function Login({ onAuthed }) {
  const { t, lang, changeLanguage } = useI18n();
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showOther, setShowOther] = useState(false);

  const googleRef = useRef(null);

  useEffect(() => {
    if (!showOther) return;
    let cancelled = false;

    async function handleGoogleCredential(response) {
      setError('');
      try {
        await loginWithGoogle(response.credential);
        onAuthed();
      } catch (err) {
        setError(err.message || t('login.googleError'));
      }
    }

    function tryInit() {
      if (cancelled || !googleRef.current) return;
      if (!window.google?.accounts?.id) {
        setTimeout(tryInit, 150);
        return;
      }
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleGoogleCredential,
      });
      window.google.accounts.id.renderButton(googleRef.current, {
        theme: 'outline',
        size: 'large',
        width: 304,
        locale: lang,
      });
    }

    tryInit();
    return () => {
      cancelled = true;
    };
  }, [showOther, lang]);

  async function submit(e) {
    e.preventDefault();
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError('');
    try {
      if (mode === 'login') {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password);
      }
      onAuthed();
    } catch (err) {
      setError(err.message || t('login.loginError'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="theme-switch auth__lang">
          <button
            type="button"
            className={`theme-switch__option${lang === 'ru' ? ' theme-switch__option--active' : ''}`}
            onClick={() => changeLanguage('ru')}
          >
            Русский
          </button>
          <button
            type="button"
            className={`theme-switch__option${lang === 'en' ? ' theme-switch__option--active' : ''}`}
            onClick={() => changeLanguage('en')}
          >
            English
          </button>
        </div>

        <div className="mark">Impulse</div>
        <h1 className="auth__title">{mode === 'login' ? t('login.loginTitle') : t('login.registerTitle')}</h1>

        <form onSubmit={submit}>
          <label className="auth__field">
            <span>{t('login.username')}</span>
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>

          <label className="auth__field">
            <span>{t('login.password')}</span>
            <input
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {error && <p className="auth__error">{error}</p>}

          <button className="auth__submit" type="submit" disabled={busy}>
            {mode === 'login' ? t('login.loginSubmit') : t('login.registerSubmit')}
          </button>

          <button
            type="button"
            className="ghost auth__switch"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login');
              setError('');
            }}
          >
            {mode === 'login' ? t('login.toRegister') : t('login.toLogin')}
          </button>
        </form>

        {!showOther ? (
          <button
            type="button"
            className="ghost auth__other"
            onClick={() => setShowOther(true)}
          >
            {t('login.otherMethods')}
          </button>
        ) : (
          <>
            <div className="auth__divider">{t('login.or')}</div>
            <div className="auth__google" ref={googleRef} />
          </>
        )}
      </div>
    </div>
  );
}
