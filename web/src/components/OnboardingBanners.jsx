import { useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { Mail, Sparkles, CheckCircle2, ChevronRight, X } from 'lucide-react'
import api from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'

/**
 * Dashboard-top banners shown to new and unverified users.
 *
 * Two banners stack here (top-most renders first):
 *   1. EmailVerificationBanner — when the JWT's email_verified claim is
 *      false. "Resend verification email" button hits
 *      POST /api/auth/resend-verification.
 *   2. OnboardingChecklist — when the URL has ?onboarding=true (set by
 *      the signup flow). Brief welcome + 4-item checklist. Dismissible;
 *      dismissed state persists in localStorage so it doesn't reappear.
 *
 * The component is positioned to render at the top of the Dashboard page
 * but is self-contained — it makes its own /api calls (resend, byok status
 * for "Oanda connected" check, /user/mode for "shadow mode" check).
 */
const BLUE = '#3B82F6'


export default function OnboardingBanners() {
  return (
    <>
      <EmailVerificationBanner />
      <OnboardingChecklist />
    </>
  )
}


// ─── Email-verification banner ─────────────────────────────────────────────


function EmailVerificationBanner() {
  const { user } = useAuth()
  const [dismissed, setDismissed]   = useState(false)
  const [sending, setSending]       = useState(false)
  const [msg, setMsg]               = useState('')

  // Hide if no claims, already verified, or user explicitly dismissed this session
  if (!user || user.email_verified) return null
  if (dismissed) return null

  const resend = async () => {
    setMsg(''); setSending(true)
    try {
      await api.post('/auth/resend-verification')
      setMsg('✓ Verification email sent. Check your inbox.')
    } catch (e) {
      const s = e?.response?.status
      if (s === 429)      setMsg('Too many requests — try again in a few minutes.')
      else                 setMsg('Could not send verification email. Try again later.')
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: '0.9rem',
      background: 'rgba(245,158,11,0.08)',
      border: '1px solid rgba(245,158,11,0.28)',
      borderLeft: '3px solid #f59e0b',
      borderRadius: '0.4rem',
      padding: '0.85rem 1.1rem',
      marginBottom: '1rem',
    }}>
      <Mail size={18} style={{ color: '#f59e0b', flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-primary)' }}>
          Please verify your email
        </div>
        <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: '0.2rem', lineHeight: 1.55 }}>
          We sent a verification link to <strong style={{ color: 'var(--text-primary)' }}>{user.email}</strong>.
          Click it to confirm you own this address.{' '}
          {msg && <span style={{ color: msg.startsWith('✓') ? '#86efac' : '#fcd34d', fontWeight: 600 }}>{msg}</span>}
        </div>
        <div style={{ marginTop: '0.55rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button onClick={resend} disabled={sending} style={{
            background: 'transparent', color: '#f59e0b',
            border: '1px solid rgba(245,158,11,0.45)',
            padding: '0.32rem 0.85rem',
            fontSize: '0.78rem', fontWeight: 600, letterSpacing: '0.04em',
            borderRadius: '0.3rem', cursor: sending ? 'wait' : 'pointer',
            opacity: sending ? 0.6 : 1,
          }}>
            {sending ? 'Sending…' : 'Resend verification email'}
          </button>
        </div>
      </div>
      <button onClick={() => setDismissed(true)} aria-label="Dismiss" style={{
        background: 'transparent', border: 'none', color: 'var(--text-muted)',
        cursor: 'pointer', padding: 0, flexShrink: 0,
      }}>
        <X size={16} />
      </button>
    </div>
  )
}


// ─── Onboarding checklist ──────────────────────────────────────────────────


const DISMISS_KEY = 'foundation.onboarding.dismissed'


function OnboardingChecklist() {
  const [searchParams, setSearchParams] = useSearchParams()
  const onboardingFlag = searchParams.get('onboarding') === 'true'
  const [persistDismissed, setPersistDismissed] = useState(
    localStorage.getItem(DISMISS_KEY) === '1'
  )
  const { user } = useAuth()

  // Status pulled live so the checklist shows ✓ for items the user has
  // already completed (e.g., they finished signup, connected Oanda first
  // and THEN found their way here).
  const [broker, setBroker] = useState(null)

  useEffect(() => {
    if (!onboardingFlag || persistDismissed) return
    api.get('/byok/oanda').then(r => setBroker(r.data)).catch(() => setBroker({ connected: false }))
  }, [onboardingFlag, persistDismissed])

  if (!onboardingFlag || persistDismissed) return null

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, '1')
    setPersistDismissed(true)
    // Clean up the URL so onboarding=true doesn't linger in the address bar
    searchParams.delete('onboarding')
    setSearchParams(searchParams, { replace: true })
  }

  const steps = [
    {
      done:  true,
      label: 'Account created',
      detail: user?.email ? `Signed up as ${user.email}` : 'Welcome to Foundation',
    },
    {
      done:  !!user?.email_verified,
      label: 'Verify your email',
      detail: 'Click the link we sent. Useful for password recovery.',
      to:    null,
    },
    {
      // Paper-trading-first framing: this is the FREE, no-broker-needed default.
      // Connecting Oanda is presented as a "when you're ready" follow-up, not
      // a prerequisite. Resets expectations vs the old order which implied
      // users had to give us a broker key before they could do anything.
      done:  false,
      label: 'Watch your bot paper-trade',
      detail: 'No broker account needed. Your bot is already running in shadow mode — fetching public market data, evaluating signals, simulating trades against your starting capital.',
      to:    '/trades',
    },
    {
      done:  !!broker?.connected,
      label: 'Connect Oanda when ready to go live',
      detail: 'Only needed if you decide to switch from paper to real money. Read the security guidance there — Foundation should NEVER have withdraw permission.',
      to:    '/settings/broker',
    },
  ]

  const completed = steps.filter(s => s.done).length

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: '1rem',
      background: 'linear-gradient(135deg, rgba(59,130,246,0.10) 0%, rgba(59,130,246,0.04) 100%)',
      border: '1px solid rgba(59,130,246,0.28)',
      borderLeft: `3px solid ${BLUE}`,
      borderRadius: '0.5rem',
      padding: '1.1rem 1.25rem',
      marginBottom: '1rem',
    }}>
      <Sparkles size={20} style={{ color: BLUE, flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', marginBottom: '0.3rem' }}>
          <span style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' }}>
            Welcome to Foundation
          </span>
          <span style={{
            fontSize: '0.72rem', fontWeight: 600, color: BLUE,
            background: 'rgba(59,130,246,0.12)',
            padding: '0.1rem 0.5rem', borderRadius: 4, letterSpacing: '0.06em',
          }}>
            {completed} of {steps.length} complete
          </span>
        </div>
        <p style={{
          fontSize: '0.84rem', color: 'var(--text-muted)',
          margin: 0, marginBottom: '0.85rem', lineHeight: 1.55,
        }}>
          A few quick steps to get the most out of your new account.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {steps.map(s => (
            <ChecklistRow key={s.label} step={s} />
          ))}
        </div>
      </div>
      <button onClick={dismiss} aria-label="Dismiss" style={{
        background: 'transparent', border: 'none', color: 'var(--text-muted)',
        cursor: 'pointer', padding: 0, flexShrink: 0,
      }}>
        <X size={16} />
      </button>
    </div>
  )
}


function ChecklistRow({ step }) {
  const Icon = step.done ? CheckCircle2 : Sparkles
  const color = step.done ? '#22c55e' : 'var(--text-muted)'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.65rem',
      padding: '0.4rem 0.6rem',
      borderRadius: '0.3rem',
      background: step.done ? 'rgba(34,197,94,0.05)' : 'transparent',
    }}>
      <Icon size={15} style={{ color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: '0.85rem',
          fontWeight: 600,
          color: step.done ? 'var(--text-muted)' : 'var(--text-primary)',
          textDecoration: step.done ? 'line-through' : 'none',
        }}>
          {step.label}
        </div>
        <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', lineHeight: 1.45 }}>
          {step.detail}
        </div>
      </div>
      {!step.done && step.to && (
        <Link to={step.to} style={{
          color: BLUE,
          textDecoration: 'none',
          fontSize: '0.78rem',
          fontWeight: 600,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 2,
          flexShrink: 0,
        }}>
          Go <ChevronRight size={13} />
        </Link>
      )}
    </div>
  )
}
