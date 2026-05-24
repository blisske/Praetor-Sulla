import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import SettingsCard from './SettingsCard.jsx'
import { Shield, ShieldCheck, ShieldOff, AlertTriangle, Copy, CheckCircle2 } from 'lucide-react'

/**
 * TwoFactorCard — Settings → Account 2FA section.
 *
 * Three states it renders:
 *   - DISABLED      → "Enable 2FA" button → starts enrollment flow
 *   - ENROLLING     → modal: QR + secret + verify code → on success show codes
 *   - SHOWING_CODES → 10 recovery codes, "I've saved these" to dismiss
 *   - ENABLED       → status + Disable + Regenerate buttons
 *
 * Self-fetches /api/auth/totp/status on mount + after every state change.
 * The actual enrollment dialog + recovery-codes display use the same
 * modal shell.
 */
export default function TwoFactorCard({ isDemo }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [phase, setPhase] = useState('idle')   // 'idle'|'enrolling'|'showing_codes'|'disabling'|'regen'
  const [enroll, setEnroll] = useState(null)   // {provisioning_uri, secret_base32, qr_png_data_url}
  const [codes, setCodes] = useState(null)     // array of plaintext recovery codes (shown once)

  const refresh = () => {
    setLoading(true); setErr('')
    api.get('/auth/totp/status')
      .then(r => setStatus(r.data))
      .catch(() => setErr('Could not load 2FA status.'))
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  // ── ENABLE — start enrollment ─────────────────────────────────────────
  const startEnrollment = async (password) => {
    setErr('')
    try {
      const { data } = await api.post('/auth/totp/enroll/start', { current_password: password })
      setEnroll(data)
      setPhase('enrolling')
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not start enrollment.')
    }
  }

  // ── ENABLE — confirm with code, get recovery codes ───────────────────
  const confirmEnrollment = async (code) => {
    setErr('')
    try {
      const { data } = await api.post('/auth/totp/enroll/confirm', { code })
      setCodes(data.recovery_codes)
      setEnroll(null)
      setPhase('showing_codes')
      refresh()
    } catch (e) {
      throw new Error(e?.response?.data?.detail || 'Code did not verify.')
    }
  }

  // ── DISABLE — needs password + (TOTP code OR recovery code) ──────────
  const disable = async (password, code) => {
    try {
      await api.post('/auth/totp/disable', { current_password: password, code })
      setPhase('idle')
      refresh()
    } catch (e) {
      throw new Error(e?.response?.data?.detail || 'Could not disable 2FA.')
    }
  }

  // ── REGENERATE — needs password + TOTP code (not recovery) ───────────
  const regenerate = async (password, code) => {
    try {
      const { data } = await api.post('/auth/totp/regenerate-codes', {
        current_password: password, code,
      })
      setCodes(data.recovery_codes)
      setPhase('showing_codes')
      refresh()
    } catch (e) {
      throw new Error(e?.response?.data?.detail || 'Could not regenerate codes.')
    }
  }

  if (loading) {
    return (
      <SettingsCard title="Two-factor authentication">
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
          Loading…
        </p>
      </SettingsCard>
    )
  }

  const enabled = !!status?.enabled
  const remaining = status?.recovery_codes_remaining ?? 0

  return (
    <>
      <SettingsCard
        title="Two-factor authentication"
        subtitle={enabled
          ? "2FA is on. You'll be asked for a code each time you sign in."
          : "Add a second factor to your sign-in. Strongly recommended once your account is connected to a real broker key."}
      >
        {err && (
          <div style={{
            background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
            color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
            padding: '0.55rem 0.75rem', marginBottom: '0.85rem',
          }}>{err}</div>
        )}

        {enabled ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.55rem',
              padding: '0.5rem 0.8rem',
              background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
              borderRadius: '0.35rem',
              fontSize: '0.82rem', color: '#86efac',
            }}>
              <ShieldCheck size={15} />
              Enabled · {remaining} recovery code{remaining === 1 ? '' : 's'} remaining
              {remaining <= 2 && (
                <span style={{ color: '#fbbf24', marginLeft: '0.25rem' }}>
                  · running low — regenerate soon
                </span>
              )}
            </div>
            <div style={{ flex: 1 }} />
            <button
              onClick={() => setPhase('regen')}
              disabled={isDemo}
              style={{
                background: 'transparent', color: 'var(--text-muted)',
                border: '1px solid var(--border)', padding: '0.5rem 1rem',
                borderRadius: '0.35rem', fontSize: '0.8rem', fontWeight: 600,
                cursor: isDemo ? 'not-allowed' : 'pointer', opacity: isDemo ? 0.5 : 1,
              }}
            >
              Regenerate codes
            </button>
            <button
              onClick={() => setPhase('disabling')}
              disabled={isDemo}
              style={{
                background: 'transparent', color: '#fca5a5',
                border: '1px solid rgba(239,68,68,0.3)', padding: '0.5rem 1rem',
                borderRadius: '0.35rem', fontSize: '0.8rem', fontWeight: 600,
                cursor: isDemo ? 'not-allowed' : 'pointer', opacity: isDemo ? 0.5 : 1,
              }}
            >
              Disable 2FA
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.55rem',
              padding: '0.5rem 0.8rem',
              background: 'rgba(148,163,184,0.06)', border: '1px solid var(--border)',
              borderRadius: '0.35rem',
              fontSize: '0.82rem', color: 'var(--text-muted)',
            }}>
              <ShieldOff size={15} />
              Not enabled
            </div>
            <div style={{ flex: 1 }} />
            <button
              onClick={() => setPhase('start_password')}
              disabled={isDemo}
              style={{
                background: 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
                color: '#1a1a1a', border: 'none',
                padding: '0.6rem 1.3rem',
                fontSize: '0.83rem', fontWeight: 700,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                borderRadius: '0.35rem',
                cursor: isDemo ? 'not-allowed' : 'pointer',
                opacity: isDemo ? 0.5 : 1,
              }}
            >
              Enable 2FA
            </button>
          </div>
        )}
      </SettingsCard>

      {/* Password prompt before starting enrollment */}
      {phase === 'start_password' && (
        <PasswordPromptModal
          title="Re-enter your password to enable 2FA"
          subtitle="Sensitive action — we re-prompt so a stolen session can't enroll 2FA."
          confirmLabel="Continue"
          onCancel={() => setPhase('idle')}
          onSubmit={async (password) => { await startEnrollment(password) }}
        />
      )}

      {/* QR + verify code */}
      {phase === 'enrolling' && enroll && (
        <EnrollmentModal
          enroll={enroll}
          onCancel={() => { setPhase('idle'); setEnroll(null) }}
          onConfirm={confirmEnrollment}
        />
      )}

      {/* Show 10 recovery codes ONCE */}
      {phase === 'showing_codes' && codes && (
        <RecoveryCodesModal
          codes={codes}
          onDismiss={() => { setCodes(null); setPhase('idle') }}
        />
      )}

      {/* Disable confirmation */}
      {phase === 'disabling' && (
        <PasswordAndCodeModal
          title="Disable two-factor authentication"
          subtitle="Enter your password plus a 6-digit code from your authenticator app (or a recovery code) to confirm."
          confirmLabel="Disable 2FA"
          confirmTone="danger"
          onCancel={() => setPhase('idle')}
          onSubmit={disable}
        />
      )}

      {/* Regenerate confirmation */}
      {phase === 'regen' && (
        <PasswordAndCodeModal
          title="Regenerate recovery codes"
          subtitle="Your existing recovery codes will be wiped and replaced with 10 fresh ones. Save the new codes — the old ones stop working immediately."
          confirmLabel="Regenerate codes"
          requireTotpOnly
          onCancel={() => setPhase('idle')}
          onSubmit={regenerate}
        />
      )}
    </>
  )
}


// ─── Modals ──────────────────────────────────────────────────────────────


function modalShell(children, accentColor = '#c8922a') {
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100, padding: '1rem',
    }}>
      <div style={{
        width: '100%', maxWidth: 520,
        maxHeight: '90vh', overflow: 'auto',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderTop: `2px solid ${accentColor}`,
        borderRadius: '0.5rem',
        padding: '1.5rem 1.75rem',
      }} onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}


function PasswordPromptModal({ title, subtitle, confirmLabel, onCancel, onSubmit }) {
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await onSubmit(pw) }
    catch (e) { setErr(e?.message || 'Failed.'); setBusy(false) }
  }

  return modalShell(
    <form onSubmit={submit}>
      <h2 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, marginBottom: '0.4rem', color: 'var(--text-primary)' }}>{title}</h2>
      <p style={{ fontSize: '0.84rem', color: 'var(--text-muted)', margin: 0, marginBottom: '1rem', lineHeight: 1.55 }}>{subtitle}</p>
      {err && <ErrBox>{err}</ErrBox>}
      <input
        type="password" autoComplete="current-password" autoFocus
        value={pw} onChange={e => setPw(e.target.value)} required
        placeholder="Current password"
        style={modalInputStyle}
      />
      <ModalButtons busy={busy} onCancel={onCancel} confirmLabel={confirmLabel} disabled={!pw || busy} />
    </form>
  )
}


function EnrollmentModal({ enroll, onCancel, onConfirm }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await onConfirm(code) }
    catch (e) { setErr(e?.message || 'Code did not verify.'); setBusy(false) }
  }

  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(enroll.secret_base32)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {/* ignore */}
  }

  return modalShell(
    <form onSubmit={submit}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.5rem' }}>
        <Shield size={18} style={{ color: '#c8922a' }} />
        <h2 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
          Set up two-factor authentication
        </h2>
      </div>
      <p style={{ fontSize: '0.83rem', color: 'var(--text-muted)', margin: 0, marginBottom: '1rem', lineHeight: 1.55 }}>
        Open your authenticator app (Google Authenticator, Authy, 1Password, Bitwarden — any TOTP app)
        and scan this QR code. Then enter the 6-digit code your app shows to finish enrolling.
      </p>

      <div style={{ display: 'flex', gap: '1.25rem', alignItems: 'flex-start', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <div style={{
          background: '#fff', padding: '0.6rem', borderRadius: '0.4rem',
          flexShrink: 0,
        }}>
          <img src={enroll.qr_png_data_url} alt="2FA QR code" style={{ display: 'block', width: 168, height: 168 }} />
        </div>
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '0.35rem',
                        letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Can't scan? Enter this manually:
          </div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.4rem',
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: '0.35rem', padding: '0.5rem 0.65rem', marginBottom: '0.5rem',
          }}>
            <code style={{
              fontFamily: 'ui-monospace, Menlo, monospace', fontSize: '0.78rem',
              color: 'var(--text-primary)', letterSpacing: '0.05em',
              wordBreak: 'break-all', flex: 1,
            }}>{enroll.secret_base32}</code>
            <button type="button" onClick={copySecret} style={{
              background: 'transparent', border: 'none', color: copied ? '#22c55e' : 'var(--text-muted)',
              cursor: 'pointer', padding: 0,
            }}>
              {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
            </button>
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Foundation · {enroll.provisioning_uri.match(/totp\/[^?]*:([^?]+)/)?.[1] || 'your account'}
          </div>
        </div>
      </div>

      {err && <ErrBox>{err}</ErrBox>}

      <div>
        <label style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '0.3rem', display: 'block',
                        letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          Verification code
        </label>
        <input
          type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6}
          autoComplete="one-time-code" autoFocus
          value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
          placeholder="123456"
          style={{
            ...modalInputStyle,
            fontSize: '1.4rem', textAlign: 'center',
            fontFamily: 'ui-monospace, Menlo, monospace',
            letterSpacing: '0.5rem',
          }}
        />
      </div>

      <ModalButtons busy={busy} onCancel={onCancel} confirmLabel="Verify + Enable"
                    disabled={code.length !== 6 || busy} />
    </form>
  )
}


function RecoveryCodesModal({ codes, onDismiss }) {
  const [acked, setAcked] = useState(false)
  const [copied, setCopied] = useState(false)

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(codes.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {/* ignore */}
  }

  return modalShell(
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.5rem' }}>
        <ShieldCheck size={18} style={{ color: '#22c55e' }} />
        <h2 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
          Save your recovery codes
        </h2>
      </div>
      <p style={{ fontSize: '0.83rem', color: 'var(--text-muted)', margin: 0, marginBottom: '0.85rem', lineHeight: 1.55 }}>
        2FA is now enabled. <strong style={{ color: '#fbbf24' }}>Save these 10 recovery codes
        somewhere safe</strong> — they're the only way back into your account if you lose your phone.
        Each code works once.
      </p>

      <div style={{
        background: '#f59e0b14', border: '1px solid rgba(245,158,11,0.3)',
        borderRadius: '0.35rem', padding: '0.55rem 0.75rem', marginBottom: '0.85rem',
        display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
      }}>
        <AlertTriangle size={14} style={{ color: '#fbbf24', flexShrink: 0, marginTop: 2 }} />
        <p style={{ fontSize: '0.76rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.5 }}>
          We won't show these again. Treat them like passwords.
        </p>
      </div>

      <div style={{
        background: 'var(--bg-elevated)', border: '1px solid var(--border)',
        borderRadius: '0.4rem', padding: '0.85rem 1rem', marginBottom: '0.85rem',
        display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.35rem 1rem',
      }}>
        {codes.map((c, i) => (
          <code key={i} style={{
            fontFamily: 'ui-monospace, Menlo, monospace',
            fontSize: '0.92rem',
            color: 'var(--text-primary)',
            letterSpacing: '0.04em',
          }}>{c}</code>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
        <button type="button" onClick={copyAll} style={{
          background: 'transparent', border: '1px solid var(--border)',
          color: copied ? '#22c55e' : 'var(--text-primary)',
          padding: '0.5rem 0.9rem', borderRadius: '0.35rem',
          fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: '0.4rem',
        }}>
          {copied ? <><CheckCircle2 size={14} /> Copied!</> : <><Copy size={14} /> Copy all</>}
        </button>
      </div>

      <label style={{
        display: 'flex', alignItems: 'center', gap: '0.55rem',
        fontSize: '0.83rem', color: 'var(--text-primary)',
        marginBottom: '1rem', cursor: 'pointer',
      }}>
        <input type="checkbox" checked={acked} onChange={e => setAcked(e.target.checked)}
               style={{ accentColor: '#c8922a' }} />
        I've saved my recovery codes somewhere safe.
      </label>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          onClick={onDismiss}
          disabled={!acked}
          style={{
            background: 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
            color: '#1a1a1a', border: 'none',
            padding: '0.6rem 1.3rem',
            fontSize: '0.83rem', fontWeight: 700,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            borderRadius: '0.35rem',
            cursor: acked ? 'pointer' : 'not-allowed',
            opacity: acked ? 1 : 0.5,
          }}
        >
          Done
        </button>
      </div>
    </div>,
    '#22c55e'
  )
}


function PasswordAndCodeModal({ title, subtitle, confirmLabel, confirmTone, requireTotpOnly, onCancel, onSubmit }) {
  const [pw, setPw] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // For disable, accept either TOTP (6 digits) OR recovery (xxxx-xxxx, 9 chars)
  // For regen, require TOTP only
  const cleanedCode = code.trim()
  const isTotp = /^\d{6}$/.test(cleanedCode)
  const isRecovery = /^[a-z0-9]{4}-?[a-z0-9]{4}$/i.test(cleanedCode)
  const codeValid = requireTotpOnly ? isTotp : (isTotp || isRecovery)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await onSubmit(pw, cleanedCode) }
    catch (e) { setErr(e?.message || 'Failed.'); setBusy(false) }
  }

  return modalShell(
    <form onSubmit={submit}>
      <h2 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, marginBottom: '0.4rem', color: 'var(--text-primary)' }}>{title}</h2>
      <p style={{ fontSize: '0.83rem', color: 'var(--text-muted)', margin: 0, marginBottom: '1rem', lineHeight: 1.55 }}>{subtitle}</p>
      {err && <ErrBox>{err}</ErrBox>}
      <input
        type="password" autoComplete="current-password"
        value={pw} onChange={e => setPw(e.target.value)} required
        placeholder="Current password"
        style={modalInputStyle}
      />
      <input
        type="text" inputMode="numeric" autoComplete="one-time-code"
        value={code} onChange={e => setCode(e.target.value)}
        placeholder={requireTotpOnly ? '6-digit code from your app' : 'TOTP code or recovery code'}
        style={modalInputStyle}
      />
      <ModalButtons busy={busy} onCancel={onCancel} confirmLabel={confirmLabel} confirmTone={confirmTone}
                    disabled={!pw || !codeValid || busy} />
    </form>,
    confirmTone === 'danger' ? '#dc2626' : '#c8922a'
  )
}


// ─── Shared bits ─────────────────────────────────────────────────────────


const modalInputStyle = {
  width: '100%',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-input)',
  borderRadius: '0.4rem',
  padding: '0.6rem 0.8rem',
  color: 'var(--text-primary)',
  fontSize: '0.9rem',
  outline: 'none',
  fontFamily: 'inherit',
  marginBottom: '0.7rem',
}


function ModalButtons({ busy, onCancel, confirmLabel, disabled, confirmTone }) {
  const danger = confirmTone === 'danger'
  return (
    <div style={{ display: 'flex', gap: '0.6rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
      <button type="button" onClick={onCancel} disabled={busy} style={{
        background: 'transparent', color: 'var(--text-muted)',
        border: '1px solid var(--border)', padding: '0.55rem 1.1rem',
        borderRadius: '0.35rem', fontSize: '0.82rem', fontWeight: 600,
        cursor: 'pointer', opacity: busy ? 0.5 : 1,
      }}>
        Cancel
      </button>
      <button type="submit" disabled={disabled} style={{
        background: danger
          ? 'linear-gradient(180deg, #dc2626 0%, #991b1b 100%)'
          : 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
        color: danger ? '#fff' : '#1a1a1a',
        border: 'none', padding: '0.6rem 1.3rem',
        fontSize: '0.83rem', fontWeight: 700,
        letterSpacing: '0.08em', textTransform: 'uppercase',
        borderRadius: '0.35rem', cursor: 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}>
        {busy ? 'Working…' : confirmLabel}
      </button>
    </div>
  )
}


function ErrBox({ children }) {
  return (
    <div style={{
      background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
      color: '#fca5a5', fontSize: '0.8rem', borderRadius: '0.3rem',
      padding: '0.5rem 0.7rem', marginBottom: '0.7rem',
    }}>
      {children}
    </div>
  )
}
