import { useEffect, useState } from 'react'
import api from '../../lib/api.js'
import SettingsCard from '../../components/SettingsCard.jsx'
import { ShieldCheck, ShieldAlert, FlaskConical, Zap, Unplug } from 'lucide-react'
import { useAuth } from '../../lib/auth.jsx'

/**
 * /settings/broker — Bring-Your-Own-Key Oanda connection management.
 *
 * Oanda's auth model:
 *   - Single API TOKEN (not key+secret split)
 *   - Plus an account_id selecting which account under the token
 *   - Bound to ONE environment: practice (free, simulated) or live
 *
 * UI states:
 *   1. No token connected → big paste form with practice/live picker
 *   2. Connected, scope=trade, env=live → "Live trading" green
 *   3. Connected, scope=trade, env=practice → "Practice trading" blue
 *   4. Connected, scope=read → amber "Trading blocked at Oanda" warning
 */

const BLUE = '#3B82F6'

const inputStyle = {
  width: '100%',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-input)',
  borderRadius: '0.4rem',
  padding: '0.6rem 0.8rem',
  color: 'var(--text-primary)',
  fontSize: '0.86rem',
  outline: 'none',
  fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
}

const labelStyle = {
  display: 'block',
  fontSize: '0.7rem',
  fontWeight: 600,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--text-sub)',
  marginBottom: '0.35rem',
}

const btnStyle = {
  background: `linear-gradient(180deg, ${BLUE} 0%, #1D4ED8 100%)`,
  color: '#fff',
  border: 'none',
  fontSize: '0.83rem',
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  padding: '0.6rem 1.2rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
  transition: 'transform 0.1s, box-shadow 0.15s, opacity 0.15s',
}

const ghostBtnStyle = {
  background: 'transparent',
  color: 'var(--text-muted)',
  border: '1px solid var(--border)',
  fontSize: '0.83rem',
  fontWeight: 600,
  letterSpacing: '0.06em',
  padding: '0.55rem 1.1rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
}


function ConnectedBadge({ scope, environment }) {
  const isTrade = scope === 'trade'
  const isLive  = environment === 'live'

  let color, bg, Icon, label, detail
  if (!isTrade) {
    color = '#f59e0b'
    bg    = 'rgba(245,158,11,0.10)'
    Icon  = ShieldAlert
    label = `Read-only · ${environment || 'practice'} account`
    detail = (
      "Your token authenticated, but the account is in a non-trading state. " +
      "Shadow trading still works on Ionic's own ledger; live Oanda " +
      "trading is locked until you resolve it with Oanda."
    )
  } else if (isLive) {
    color = '#22c55e'
    bg    = 'rgba(34,197,94,0.10)'
    Icon  = Zap
    label = 'Live trading · Oanda funded account'
    detail = (
      "Connected to a funded Oanda live account, trading enabled. " +
      "You can switch to live mode any time from the Mode page."
    )
  } else {
    color = BLUE
    bg    = 'rgba(59,130,246,0.10)'
    Icon  = FlaskConical
    label = 'Practice trading · Oanda practice account'
    detail = (
      "Connected to an Oanda practice account. Ionic will paper-trade " +
      "with the simulated funds in this account. To go live, generate " +
      "a token against your funded Oanda live account."
    )
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: '0.85rem',
      padding: '0.9rem 1.1rem',
      background: bg,
      border: `1px solid ${color}33`,
      borderRadius: '0.5rem',
      marginBottom: '1rem',
    }}>
      <Icon size={20} style={{ color, flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontSize: '0.85rem', fontWeight: 700, color, letterSpacing: '0.04em' }}>
          {label}
        </div>
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem', lineHeight: 1.5 }}>
          {detail}
        </div>
      </div>
    </div>
  )
}


function PasteForm({ existing, onConnected }) {
  const { user } = useAuth()
  const isDemo = !!user?.is_demo
  const [apiToken,  setApiToken]  = useState('')
  const [accountId, setAccountId] = useState('')
  const [isPractice, setIsPractice] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [ok, setOk]     = useState(null)
  const [securityAcked, setSecurityAcked] = useState(false)

  const handleAckChange = checked => {
    setSecurityAcked(checked)
    if (!checked) { setApiToken(''); setAccountId(''); setErr('') }
  }

  const submit = async e => {
    e.preventDefault()
    setErr(''); setOk(null)
    if (apiToken.trim().length < 32) {
      setErr('Oanda tokens are ~65 chars. Make sure you pasted the entire value.')
      return
    }
    if (!accountId.trim().includes('-') || accountId.trim().length < 10) {
      setErr("Account ID format looks wrong. Should look like '101-001-12345678-001'.")
      return
    }
    setBusy(true)
    try {
      const { data } = await api.post('/byok/oanda/validate', {
        api_token:   apiToken.trim(),
        account_id:  accountId.trim(),
        environment: isPractice ? 'practice' : 'live',
      })
      setOk(data)
      setApiToken(''); setAccountId('')
      if (onConnected) onConnected()
    } catch (e) {
      const status = e?.response?.status
      const detail = e?.response?.data?.detail
      if (status === 400)       setErr(detail || 'Oanda rejected this token.')
      else if (status === 422)  setErr('Token or account ID looks malformed.')
      else if (status === 429)  setErr('Too many validation attempts. Please wait an hour or so.')
      else if (status === 502)  setErr('Oanda accepted the token but we couldn\'t determine scope. Contact support.')
      else if (status === 503)  setErr('Oanda API is unavailable right now. Please try again in a few minutes.')
      else                       setErr(detail || 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title={existing ? 'Rotate Oanda token' : 'Connect Oanda'}
      subtitle={
        existing
          ? 'Paste a fresh API token + account_id. The old encrypted blob is overwritten.'
          : 'Foundation uses your own Oanda API token to place trades on your behalf. Your token is encrypted at rest (AES-256-GCM) and never written to disk in plaintext.'
      }
    >
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        {ok && (
          <div style={{ background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
                        color: '#86efac', fontSize: '0.82rem', borderRadius: '0.3rem',
                        padding: '0.6rem 0.8rem' }}>
            ✓ {ok.detail}
          </div>
        )}
        {err && (
          <div style={{ background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
                        color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
                        padding: '0.55rem 0.75rem' }}>
            {err}
          </div>
        )}

        {/* STEP 1: SECURITY GUIDANCE + MANDATORY ACK */}
        <div style={{
          background: 'rgba(220,38,38,0.07)',
          border: '1px solid rgba(220,38,38,0.30)',
          borderRadius: '0.4rem',
          padding: '0.85rem 1rem',
          fontSize: '0.78rem',
          lineHeight: 1.6,
        }}>
          <div style={{
            fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.12em',
            textTransform: 'uppercase', color: '#fca5a5', marginBottom: '0.5rem',
          }}>
            Step 1: Generate your token on Oanda — read before generating
          </div>
          <ul style={{ margin: 0, paddingLeft: '1.1rem', color: 'var(--text-primary)' }}>
            <li>
              <strong style={{ color: '#22c55e' }}>✓ Start with a Practice token:</strong>{' '}
              Sign in to Oanda → switch to <strong>fxTrade Practice</strong> → Manage API Access →
              Generate. Ionic will paper-trade against the simulated funds Oanda gives every
              practice account ($100K). Live tokens come later once you've watched the bot trade.
            </li>
            <li style={{ marginTop: '0.45rem' }}>
              <strong style={{ color: '#fbbf24' }}>⚠ Oanda tokens are environment-bound.</strong>{' '}
              A Practice token only works against api-fxpractice.oanda.com;
              a Live token only against api-fxtrade.oanda.com. The radio buttons
              below MUST match the environment, or Oanda will reject with 401.
            </li>
            <li style={{ marginTop: '0.45rem' }}>
              <strong style={{ color: '#fbbf24' }}>💡 Find your account ID</strong>{' '}
              in Oanda's web dashboard → Manage Funds → it looks like
              <code style={{ background: 'var(--bg-elevated)', padding: '0.05rem 0.3rem',
                             borderRadius: 3, marginLeft: '0.3rem' }}>101-001-12345678-001</code>.
              You'll have one per sub-account.
            </li>
            <li style={{ marginTop: '0.45rem' }}>
              <strong style={{ color: '#fca5a5' }}>⛔ Tokens can place trades but cannot transfer funds.</strong>{' '}
              Bank transfers in/out of your Oanda account require ACH credentials you
              entered directly at Oanda. Foundation cannot drain your bank via the API.
            </li>
          </ul>
          <p style={{
            fontSize: '0.72rem', color: 'var(--text-muted)', margin: 0, marginTop: '0.7rem',
            paddingTop: '0.55rem', borderTop: '1px solid rgba(220,38,38,0.15)',
          }}>
            <strong>Transparency:</strong> Your token + account_id are encrypted at rest
            with AES-256-GCM. They're decrypted only in your bot's engine memory at trade
            time. The Foundation operator has technical ability to decrypt stored keys
            (self-hosted single-operator system). Bring only what you'd trust at a Vegas table.
          </p>

          <label style={{
            display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
            marginTop: '0.85rem', paddingTop: '0.7rem',
            borderTop: '1px solid rgba(220,38,38,0.15)',
            cursor: 'pointer', color: 'var(--text-primary)',
          }}>
            <input
              type="checkbox"
              checked={securityAcked}
              onChange={e => handleAckChange(e.target.checked)}
              style={{ marginTop: 3, accentColor: BLUE, flexShrink: 0 }}
            />
            <span style={{ fontSize: '0.82rem', lineHeight: 1.5 }}>
              <strong>I confirm I generated my Oanda token per the guidance above:</strong>{' '}
              I know which environment (practice vs live) the token is bound to,
              I have the correct account ID for that environment,
              and I understand the token alone cannot move money in or out of my Oanda account.
            </span>
          </label>
        </div>

        {/* STEP 2: ENVIRONMENT PICKER + PASTE FORM */}
        <div style={{
          opacity: securityAcked ? 1 : 0.35,
          pointerEvents: securityAcked ? 'auto' : 'none',
          transition: 'opacity 0.2s',
          display: 'flex', flexDirection: 'column', gap: '0.85rem',
          paddingTop: '0.3rem',
        }}>
          {!securityAcked && (
            <p style={{ fontSize: '0.72rem', color: '#fbbf24', margin: 0, fontStyle: 'italic' }}>
              ⬆ Check the box above to enable the paste form.
            </p>
          )}

          <div>
            <label style={labelStyle}>Environment</label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="button"
                onClick={() => setIsPractice(true)}
                disabled={!securityAcked}
                style={{
                  flex: 1,
                  background: isPractice ? `${BLUE}1f` : 'var(--bg-elevated)',
                  border: `1.5px solid ${isPractice ? BLUE : 'var(--border-input)'}`,
                  color: isPractice ? BLUE : 'var(--text-muted)',
                  fontSize: '0.85rem',
                  fontWeight: isPractice ? 700 : 500,
                  padding: '0.65rem 0.8rem',
                  borderRadius: '0.4rem',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                  <FlaskConical size={14} />
                  <span>Practice</span>
                </div>
                <div style={{ fontSize: '0.7rem', fontWeight: 500, color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                  Simulated funds · api-fxpractice.oanda.com
                </div>
              </button>
              <button
                type="button"
                onClick={() => setIsPractice(false)}
                disabled={!securityAcked}
                style={{
                  flex: 1,
                  background: !isPractice ? 'rgba(34,197,94,0.12)' : 'var(--bg-elevated)',
                  border: `1.5px solid ${!isPractice ? '#22c55e' : 'var(--border-input)'}`,
                  color: !isPractice ? '#22c55e' : 'var(--text-muted)',
                  fontSize: '0.85rem',
                  fontWeight: !isPractice ? 700 : 500,
                  padding: '0.65rem 0.8rem',
                  borderRadius: '0.4rem',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                  <Zap size={14} />
                  <span>Live</span>
                </div>
                <div style={{ fontSize: '0.7rem', fontWeight: 500, color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                  Real money · api-fxtrade.oanda.com
                </div>
              </button>
            </div>
          </div>

          <div>
            <label style={labelStyle}>
              {isPractice ? 'Practice API token' : 'Live API token'}
            </label>
            <input style={inputStyle} type="text" autoComplete="off" spellCheck={false}
                   value={apiToken} onChange={e => setApiToken(e.target.value)}
                   required={securityAcked} tabIndex={securityAcked ? 0 : -1}
                   placeholder="65-character hex token" />
          </div>
          <div>
            <label style={labelStyle}>Account ID</label>
            <input style={inputStyle} type="text" autoComplete="off" spellCheck={false}
                   value={accountId} onChange={e => setAccountId(e.target.value)}
                   required={securityAcked} tabIndex={securityAcked ? 0 : -1}
                   placeholder="101-001-12345678-001" />
          </div>
        </div>
        <div>
          <button
            type="submit"
            disabled={busy || isDemo || !securityAcked}
            title={isDemo ? 'Disabled in demo mode'
                   : !securityAcked ? 'Check the security ack above first'
                   : undefined}
            style={{
              ...btnStyle,
              opacity: (busy || isDemo || !securityAcked) ? 0.4 : 1,
              cursor: (isDemo || !securityAcked) ? 'not-allowed' : 'pointer',
            }}
          >
            {busy
              ? `Validating against Oanda ${isPractice ? 'practice' : 'live'}…`
              : existing ? 'Replace Token' : 'Validate & Save'}
          </button>
        </div>
      </form>
    </SettingsCard>
  )
}


function DisconnectCard({ onDisconnected }) {
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState('')
  const [confirm, setConfirm] = useState(false)

  const submit = async () => {
    setErr(''); setBusy(true)
    try {
      await api.delete('/byok/oanda')
      if (onDisconnected) onDisconnected()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not disconnect. Try again.')
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  return (
    <SettingsCard
      title="Disconnect Oanda"
      subtitle="Removes the encrypted token + account ID from Foundation. Your bot reverts to shadow (paper) mode on Ionic's own ledger. You can reconnect any time."
    >
      {err && (
        <div style={{ background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
                      color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
                      padding: '0.55rem 0.75rem', marginBottom: '0.8rem' }}>{err}</div>
      )}
      {!confirm ? (
        <button onClick={() => setConfirm(true)} style={ghostBtnStyle}>
          <Unplug size={14} style={{ display: 'inline', marginRight: '0.4rem', marginBottom: 2 }} />
          Disconnect Token
        </button>
      ) : (
        <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
          <span style={{ fontSize: '0.84rem', color: '#fca5a5' }}>Are you sure?</span>
          <button onClick={submit} disabled={busy} style={{
            ...btnStyle,
            background: 'linear-gradient(180deg, #dc2626 0%, #991b1b 100%)',
            color: '#fff', opacity: busy ? 0.5 : 1,
          }}>
            {busy ? 'Disconnecting…' : 'Yes, disconnect'}
          </button>
          <button onClick={() => setConfirm(false)} style={ghostBtnStyle} disabled={busy}>
            Cancel
          </button>
        </div>
      )}
    </SettingsCard>
  )
}


export default function SettingsBroker() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get('/byok/oanda')
      .then(r => { if (!cancelled) { setStatus(r.data); setLoading(false) } })
      .catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refreshTick])

  const refresh = () => setRefreshTick(t => t + 1)

  if (loading) {
    return (
      <SettingsCard title="Loading…">
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
          Checking your broker connection…
        </p>
      </SettingsCard>
    )
  }

  const connected = status?.connected

  return (
    <div>
      {connected && status?.last_error && (
        <SettingsCard title="Connection error" tone="danger">
          <p style={{ fontSize: '0.85rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.55 }}>
            Oanda reported: <code style={{
              fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
              background: 'var(--bg-elevated)',
              padding: '0.1rem 0.4rem',
              borderRadius: '0.2rem',
              fontSize: '0.82rem',
            }}>{status.last_error}</code>
          </p>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: '0.6rem' }}>
            Your bot was automatically reverted to shadow mode. Rotate your token below to fix.
          </p>
        </SettingsCard>
      )}

      {connected && <ConnectedBadge scope={status.scope} environment={status.environment} />}

      {connected && (
        <div style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border)',
          borderRadius: '0.4rem',
          padding: '0.85rem 1.1rem',
          marginBottom: '1rem',
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          gap: '0.4rem 1rem',
          fontSize: '0.8rem',
        }}>
          <span style={{ color: 'var(--text-muted)' }}>Environment:</span>
          <span style={{ color: 'var(--text-primary)', textTransform: 'capitalize' }}>
            {status.environment || '—'}
          </span>
          <span style={{ color: 'var(--text-muted)' }}>Validated:</span>
          <span style={{ color: 'var(--text-primary)' }}>
            {status.validated_at ? new Date(status.validated_at + 'Z').toLocaleString() : '—'}
          </span>
          <span style={{ color: 'var(--text-muted)' }}>Last used:</span>
          <span style={{ color: 'var(--text-primary)' }}>
            {status.last_used_at ? new Date(status.last_used_at + 'Z').toLocaleString() : 'Not yet'}
          </span>
        </div>
      )}

      <PasteForm existing={connected} onConnected={refresh} />
      {connected && <DisconnectCard onDisconnected={refresh} />}
    </div>
  )
}
