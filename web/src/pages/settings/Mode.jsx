import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../../lib/api.js'
import SettingsCard from '../../components/SettingsCard.jsx'
import { Lock, FlaskConical, Zap, AlertTriangle } from 'lucide-react'
import { useAuth } from '../../lib/auth.jsx'

/**
 * /settings/mode — shadow ↔ live toggle.
 *
 * GET /api/user/mode returns:
 *   { mode, can_live, gate_reason, broker_scope, config_present }
 *
 * POST /api/user/mode {mode: "live"} requires can_live=true (server-side
 * gates on broker_keys.scope='trade'). POST {mode: "shadow"} is
 * unconditional.
 *
 * UX:
 *   - Big radio-style "Shadow" / "Live" cards
 *   - Live card disabled (greyed, lock icon) when can_live is false; tooltip
 *     shows the gate_reason
 *   - Confirmation modal before flipping live → shadow OR shadow → live
 */
const btnStyle = {
  background: 'linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)',
  color: '#1a1a1a',
  border: 'none',
  fontSize: '0.83rem',
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  padding: '0.6rem 1.2rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
  transition: 'transform 0.1s, opacity 0.15s',
}


function ModeCard({ value, current, label, blurb, icon: Icon, disabled, onSelect }) {
  const active = value === current
  const accent = value === 'live' ? '#22c55e' : '#3B82F6'
  return (
    <button
      type="button"
      onClick={disabled ? undefined : () => onSelect(value)}
      disabled={disabled}
      style={{
        flex: 1,
        background: active ? `${accent}14` : 'var(--bg-surface)',
        border: active ? `2px solid ${accent}` : '2px solid var(--border)',
        borderRadius: '0.5rem',
        padding: '1.1rem',
        textAlign: 'left',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background 0.15s, border-color 0.15s',
        color: 'inherit',
        fontFamily: 'inherit',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem', marginBottom: '0.5rem' }}>
        <Icon size={18} style={{ color: active ? accent : 'var(--text-muted)' }} />
        <span style={{
          fontSize: '0.92rem',
          fontWeight: 700,
          color: active ? accent : 'var(--text-primary)',
          letterSpacing: '0.04em',
        }}>
          {label}
        </span>
        {active && (
          <span style={{
            marginLeft: 'auto',
            fontSize: '0.65rem',
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: accent,
          }}>
            Current
          </span>
        )}
        {disabled && !active && <Lock size={14} style={{ marginLeft: 'auto', color: 'var(--text-muted)' }} />}
      </div>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: 0, lineHeight: 1.55 }}>
        {blurb}
      </p>
    </button>
  )
}


function ConfirmModal({
  targetMode, onConfirm, onCancel, busy,
  otpRequired, otpCode, setOtpCode, otpInfo, err,
}) {
  const isLive = targetMode === 'live'
  const inOtpStep = isLive && otpRequired
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100, padding: '1rem',
    }} onClick={busy ? undefined : onCancel}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 460,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderTop: `2px solid ${isLive ? '#22c55e' : '#3B82F6'}`,
        borderRadius: '0.5rem',
        padding: '1.5rem',
      }}>
        <h2 style={{
          fontSize: '1.1rem', fontWeight: 700,
          color: 'var(--text-primary)', margin: 0, marginBottom: '0.6rem',
        }}>
          {inOtpStep ? 'Enter the code from your email'
            : isLive ? 'Switch to LIVE trading?'
            : 'Switch back to SHADOW mode?'}
        </h2>

        {inOtpStep ? (
          <>
            <p style={{
              fontSize: '0.88rem', color: 'var(--text-muted)',
              margin: 0, marginBottom: '1rem', lineHeight: 1.55,
            }}>
              {otpInfo || 'We sent a 6-digit code to your email. Enter it below to complete the switch (valid for 15 minutes).'}
            </p>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              autoFocus
              autoComplete="one-time-code"
              value={otpCode}
              onChange={e => setOtpCode(e.target.value.replace(/\D/g, ''))}
              placeholder="123456"
              style={{
                width: '100%', fontSize: '1.5rem', textAlign: 'center',
                fontFamily: 'ui-monospace, Menlo, monospace',
                letterSpacing: '0.6rem', padding: '0.7rem',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-input)',
                borderRadius: '0.4rem',
                color: 'var(--text-primary)',
                marginBottom: '1rem',
              }}
            />
            {err && (
              <div style={{
                background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
                color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
                padding: '0.55rem 0.75rem', marginBottom: '1rem',
              }}>
                {err}
              </div>
            )}
          </>
        ) : (
          <p style={{
            fontSize: '0.88rem', color: 'var(--text-muted)',
            margin: 0, marginBottom: '1.2rem', lineHeight: 1.55,
          }}>
            {isLive
              ? 'Click below to send a confirmation code to your email. You\'ll enter the code on the next screen — that\'s when the switch actually happens.'
              : 'Your bot stops placing real orders immediately. Open positions continue managing themselves until they exit normally.'}
          </p>
        )}

        <div style={{ display: 'flex', gap: '0.6rem', justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={busy} style={{
            background: 'transparent', color: 'var(--text-muted)',
            border: '1px solid var(--border)', padding: '0.6rem 1.1rem',
            borderRadius: '0.35rem', fontSize: '0.83rem', fontWeight: 600,
            cursor: 'pointer', opacity: busy ? 0.5 : 1,
          }}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy || (inOtpStep && otpCode.length !== 6)} style={{
            ...btnStyle,
            background: isLive
              ? 'linear-gradient(180deg, #22c55e 0%, #15803d 100%)'
              : 'linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)',
            color: isLive ? '#fff' : '#1a1a1a',
            opacity: (busy || (inOtpStep && otpCode.length !== 6)) ? 0.5 : 1,
          }}>
            {busy ? 'Working…'
              : inOtpStep ? 'Confirm + Go Live'
              : isLive ? 'Email me a code'
              : 'Yes, back to shadow'}
          </button>
        </div>
      </div>
    </div>
  )
}


export default function SettingsMode() {
  const { user } = useAuth()
  const isDemo = !!user?.is_demo
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(null)  // 'shadow' | 'live' | null
  const [busy, setBusy]       = useState(false)
  const [err, setErr]         = useState('')
  const [tick, setTick]       = useState(0)
  // OTP confirmation state (shadow→live only)
  const [otpRequired, setOtpRequired] = useState(false)
  const [otpCode, setOtpCode]         = useState('')
  const [otpInfo, setOtpInfo]         = useState('')   // "we sent a code…" banner

  useEffect(() => {
    let cancelled = false
    setLoading(true); setErr('')
    api.get('/user/mode')
      .then(r => { if (!cancelled) { setStatus(r.data); setLoading(false) } })
      .catch(e => {
        if (cancelled) return
        const s = e?.response?.status
        if (s === 503) setErr('Your trading workspace is still being set up.')
        else            setErr('Could not load mode status.')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [tick])

  const confirmFlip = async () => {
    setBusy(true); setErr(''); setOtpInfo('')
    try {
      const body = { mode: pending }
      // If we're in the OTP step (live flip + code entered), include the code
      if (pending === 'live' && otpRequired && otpCode) {
        body.confirmation_code = otpCode.trim()
      }
      const { data } = await api.post('/user/mode', body)

      if (data.confirmation_required) {
        // Backend issued a code + emailed it. Switch UI to OTP-entry mode.
        setOtpRequired(true)
        setOtpInfo(data.detail || 'Check your email for a 6-digit code.')
        setOtpCode('')
        // Don't dismiss the modal — user enters code in next step
      } else {
        // Actually flipped — close + refresh
        setPending(null)
        setOtpRequired(false)
        setOtpCode('')
        setOtpInfo('')
        setTick(t => t + 1)
      }
    } catch (e) {
      const s = e?.response?.status
      if (s === 400)       setErr(e?.response?.data?.detail || 'Mode change rejected.')
      else if (s === 503)  setErr('Your trading workspace is still being set up.')
      else                 setErr(e?.response?.data?.detail || 'Mode change failed.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <SettingsCard title="Loading…">
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
          Reading your trading config…
        </p>
      </SettingsCard>
    )
  }

  const canLive = status?.can_live

  return (
    <div>
      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.6rem 0.8rem', marginBottom: '1rem',
        }}>
          {err}
        </div>
      )}

      <SettingsCard
        title="Trading mode"
        subtitle="Shadow runs the strategy against real market data but never places real orders. Live places real Oanda orders on every entry."
      >
        <div style={{ display: 'flex', gap: '0.85rem', marginBottom: '0.85rem' }}>
          <ModeCard
            value="shadow"
            current={status?.mode}
            label="Shadow (Paper)"
            blurb="Strategy + risk engine run end-to-end; orders are simulated. No real money at risk."
            icon={FlaskConical}
            disabled={isDemo}
            onSelect={(target) => target !== status?.mode && setPending(target)}
          />
          <ModeCard
            value="live"
            current={status?.mode}
            label="Live (Real money)"
            blurb={isDemo
              ? 'Sign up for a real account to enable live trading.'
              : canLive
                ? 'Real Oanda orders. Same consensus + AI + drawdown safeguards as shadow.'
                : 'Locked until you connect a trade-scope Oanda key.'}
            icon={Zap}
            disabled={isDemo || !canLive}
            onSelect={(target) => target !== status?.mode && setPending(target)}
          />
        </div>

        {!canLive && status?.gate_reason && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: '0.6rem',
            background: 'rgba(245,158,11,0.08)',
            border: '1px solid rgba(245,158,11,0.22)',
            borderRadius: '0.4rem',
            padding: '0.7rem 0.85rem',
            marginTop: '0.4rem',
          }}>
            <AlertTriangle size={16} style={{ color: '#f59e0b', flexShrink: 0, marginTop: 2 }} />
            <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)', lineHeight: 1.5 }}>
              {status.gate_reason}
              {!status.broker_scope && (
                <> <Link to="/settings/broker" style={{ color: '#3B82F6', fontWeight: 600 }}>
                  Connect your Oanda key →
                </Link></>
              )}
            </div>
          </div>
        )}
      </SettingsCard>

      <SettingsCard title="What happens when I switch?" subtitle="">
        <ul style={{
          margin: 0, paddingLeft: '1.2rem',
          fontSize: '0.84rem', color: 'var(--text-muted)', lineHeight: 1.7,
        }}>
          <li>Engine reloads your <code>Config.yaml</code> on the next cycle (within ~60s).</li>
          <li>Open shadow positions don&apos;t migrate to live — they continue tracking until they exit normally on shadow stops.</li>
          <li>When live, every entry places real Oanda orders. Exits + trailing stops continue to manage automatically.</li>
          <li>You can switch back to shadow at any time — no broker key required for the reverse direction.</li>
        </ul>
      </SettingsCard>

      {pending && (
        <ConfirmModal
          targetMode={pending}
          onConfirm={confirmFlip}
          onCancel={() => {
            if (busy) return
            setPending(null)
            setOtpRequired(false)
            setOtpCode('')
            setOtpInfo('')
            setErr('')
          }}
          busy={busy}
          otpRequired={otpRequired}
          otpCode={otpCode}
          setOtpCode={setOtpCode}
          otpInfo={otpInfo}
          err={err}
        />
      )}
    </div>
  )
}
