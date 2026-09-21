import { useEffect, useRef, useState } from 'react';
import { transcribeAudio } from '../lib/api';
import { useI18n } from '../lib/i18n.jsx';

export default function Composer({ value, onChange, onSend, onStop, busy, reasoning, onToggleReasoning }) {
  const { t } = useI18n();
  const inputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const heldRef = useRef(false); // true while the mic button is physically pressed
  const [voiceState, setVoiceState] = useState('idle'); // idle | recording | transcribing
  const [voiceError, setVoiceError] = useState('');

  const grow = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  };

  useEffect(() => {
    grow();
  }, [value]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = (e) => {
    e.preventDefault();
    onSend(value);
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend(value);
    }
  };

  const attach = () => {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.addEventListener('change', () => {
      if (!picker.files.length) return;
      onChange((value ? value.trim() + ' ' : '') + picker.files[0].name);
      inputRef.current?.focus();
    });
    picker.click();
  };

  // Stops every mic track too (not just the recorder) — otherwise the
  // browser's "recording" indicator (and the OS mic light on some
  // systems) stays on after the user finishes speaking.
  const stopTracks = () => {
    mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
  };

  useEffect(() => stopTracks, []);

  const startVoiceInput = async () => {
    if (voiceState !== 'idle') return;
    setVoiceError('');
    heldRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // The button may already have been released while the permission
      // prompt/getUserMedia was pending — don't start a recording nobody
      // is holding for anymore, just release the mic straight away.
      if (!heldRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stopTracks();
        setVoiceState('transcribing');
        try {
          const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
          const { text } = await transcribeAudio(blob);
          if (text) onChange((value ? value.trim() + ' ' : '') + text);
        } catch {
          setVoiceError(t('composer.voiceError'));
        } finally {
          setVoiceState('idle');
          inputRef.current?.focus();
        }
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setVoiceState('recording');
    } catch {
      setVoiceError(t('composer.voiceMicDenied'));
    }
  };

  const stopVoiceInput = () => {
    heldRef.current = false;
    if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop();
  };

  return (
    <div className="dock">
      <form className="composer" id="composer" autoComplete="off" onSubmit={submit}>
        <textarea
          id="input"
          ref={inputRef}
          className="composer__input"
          rows={1}
          placeholder={t('composer.placeholder')}
          aria-label={t('composer.messageAriaLabel')}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer__row">
          <button className="icon" type="button" id="attach" aria-label={t('composer.attachAriaLabel')} onClick={attach}>
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="M13.5 7.5 8.2 12.8a2 2 0 0 0 2.8 2.8l5.7-5.7a3.7 3.7 0 0 0-5.2-5.2L5.2 10.9a5.4 5.4 0 0 0 7.6 7.6" />
            </svg>
          </button>
          <button
            className={`icon composer__mic${voiceState === 'recording' ? ' composer__mic--recording' : ''}`}
            type="button"
            aria-label={t('composer.voiceHoldAriaLabel')}
            title={t('composer.voiceHoldAriaLabel')}
            disabled={voiceState === 'transcribing'}
            onPointerDown={(e) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
              startVoiceInput();
            }}
            onPointerUp={stopVoiceInput}
            onPointerCancel={stopVoiceInput}
          >
            {voiceState === 'transcribing' ? (
              <svg viewBox="0 0 20 20" aria-hidden="true" className="composer__mic-spinner">
                <circle cx="10" cy="10" r="7" strokeDasharray="30 15" />
              </svg>
            ) : (
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <rect x="7.5" y="2.5" width="5" height="9" rx="2.5" />
                <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" />
              </svg>
            )}
          </button>
          <button
            className={`ghost composer__reasoning${reasoning ? ' ghost--active' : ''}`}
            type="button"
            onClick={onToggleReasoning}
            title={reasoning ? t('composer.reasoningOnTitle') : t('composer.reasoningOffTitle')}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 2a4.5 4.5 0 0 0-2.5 8.24V12h5v-1.76A4.5 4.5 0 0 0 8 2Z" />
              <path d="M6.5 14h3" />
            </svg>
            {t('composer.reasoningLabel')}
          </button>
          <span className="hint">{t('composer.enterHint')}</span>
          {!busy ? (
            <button className="send" id="send" type="submit" aria-label={t('composer.sendAriaLabel')}>
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="M10 16V4M4.8 9.2 10 4l5.2 5.2" />
              </svg>
            </button>
          ) : (
            <button className="send send--stop" id="stop" type="button" aria-label={t('composer.stopAriaLabel')} onClick={onStop}>
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <rect x="6.5" y="6.5" width="7" height="7" rx="1.5" />
              </svg>
            </button>
          )}
        </div>
      </form>
      {voiceError ? <p className="disclaimer disclaimer--error">{voiceError}</p> : <p className="disclaimer">{t('composer.disclaimer')}</p>}
    </div>
  );
}
