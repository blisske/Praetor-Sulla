import { useEffect, useMemo, useState } from 'react'
import api from '../../lib/api.js'
import SettingsCard from '../../components/SettingsCard.jsx'
import { Shield, AlertTriangle, RotateCcw } from 'lucide-react'
import { useAuth } from '../../lib/auth.jsx'

/**
 * /settings/trading — user-settable risk caps.
 *
 * Five knobs, all bounded by [floor, operator-ceiling] (server-validated).
 * User can only TIGHTEN below the operator's ceiling — never loosen.
 *
 * Backend:
 *   GET  /api/user/risk-caps  → { current, ceilings, floors, config_present }
 *   POST /api/user/risk-caps  → tighten one or more fields
 *
 * Per knob we render:
 *   - Label + 1-line description
 *   - Numeric input (validated against floor/ceiling inline)
 *   - Range slider (visual; same bound enforcement)
 *   - Footer note showing "max allowed: X • min: Y"
 *
 * Save button only enables when at least one field differs from current.
 * Reset-to-ceiling button per-row restores the operator's max for that knob.
 */

// Field metadata — drives the UI declaratively
const FIELDS = [
  {
    key:   'risk_per_trade_pct',
    label: 'Risk per trade',
    unit:  '%',
    step:  0.1,
    desc:  'Maximum capital risked on any single trade, as a percent of your equity. Lower = smaller positions = safer.',
    direction_blurb: 'LOWER is more conservative.',
  },
  {
    key:   'position_size_max_pct',
    label: 'Max position size',
    unit:  '%',
    step:  0.5,
    desc:  'Hard cap on any single position as a percent of your equity. Independent of risk-per-trade; this is the absolute size ceiling.',
    direction_blurb: 'LOWER is more conservative.',
  },
  {
    key:   'max_open_trades',
    label: 'Max open positions',
    unit:  '',
    step:  1,
    integer: true,
    desc:  'How many positions the bot may hold at one time. Lower = more concentrated, easier to monitor, less correlated exposure.',
    direction_blurb: 'LOWER is more conservative.',
  },
  {
    key:   'drawdown_halt_pct',
    label: 'Peak-drawdown halt',
    unit:  '%',
    step:  0.5,
    desc:  'If your equity falls this far from its all-time peak, the bot stops opening NEW trades until you intervene. Doesn\'t close existing positions.',
    direction_blurb: 'LOWER halts you sooner = more protective.',
  },
  {
    key:   'daily_halt_pct',
    label: 'Daily-loss halt',
    unit:  '%',
    step:  0.5,
    desc:  'If your equity drops this far in a single day (vs. midnight UTC), the bot halts for the rest of the day. Independent of the peak-drawdown halt.',
    direction_blurb: 'LOWER halts you sooner = more protective.',
  },
]


function RiskRow({ field, current, ceiling, floor, draft, setDraft, disabled }) {
  const display = draft ?? current
  const isInt = !!field.integer
  const min = floor
  const max = ceiling
  const val = parseFloat(display)
  const outOfBounds = !Number.isFinite(val) || val < min || val > max
  const changed = draft !== undefined && draft !== '' && parseFloat(draft) !== parseFloat(current)

  // Visual fill — where the current value sits on the [floor, ceiling] axis
  const pctOnAxis = max === min ? 0 : ((parseFloat(display) - min) / (max - min)) * 100

  return (
    <div style={{
      padding: '1rem 0',
      borderBottom: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 220px' }}>
          <div style={{
            fontSize: '0.92rem', fontWeight: 700,
            color: 'var(--text-primary)', marginBottom: '0.2rem',
          }}>
            {field.label}
          </div>
          <p style={{
            fontSize: '0.78rem', color: 'var(--text-muted)',
            margin: 0, lineHeight: 1.5,
          }}>
            {field.desc}
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
          <input
            type="number"
            inputMode={isInt ? 'numeric' : 'decimal'}
            step={field.step}
            min={floor}
            max={ceiling}
            disabled={disabled}
            value={display ?? ''}
            onChange={e => setDraft(field.key, e.target.value)}
            style={{
              width: 90,
              padding: '0.45rem 0.55rem',
              background: 'var(--bg-elevated)',
              border: `1px solid ${outOfBounds ? '#ef4444' : 'var(--border-input)'}`,
              borderRadius: '0.3rem',
              color: 'var(--text-primary)',
              fontSize: '0.92rem',
              textAlign: 'right',
              fontVariantNumeric: 'tabular-nums',
            }}
          />
          {field.unit && (
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              {field.unit}
            </span>
          )}
          <button
            type="button"
            onClick={() => setDraft(field.key, String(ceiling))}
            disabled={disabled}
            title="Reset to operator's maximum"
            style={{
              background: 'transparent', border: '1px solid var(--border)',
              color: 'var(--text-muted)', borderRadius: '0.3rem',
              padding: '0.35rem 0.5rem', cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.4 : 1, display: 'flex', alignItems: 'center',
            }}
          >
            <RotateCcw size={13} />
          </button>
        </div>
      </div>

      <div style={{ marginTop: '0.7rem', display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <input
          type="range"
          min={floor}
          max={ceiling}
          step={field.step}
          disabled={disabled}
          value={Number.isFinite(val) ? val : floor}
          onChange={e => setDraft(field.key, e.target.value)}
          style={{
            flex: 1,
            accentColor: '#c8922a',
          }}
        />
        <div style={{
          fontSize: '0.72rem', color: 'var(--text-muted)',
          width: 130, textAlign: 'right',
          fontVariantNumeric: 'tabular-nums',
        }}>
          floor {floor}{field.unit} · ceiling {ceiling}{field.unit}
        </div>
      </div>

      <div style={{
        marginTop: '0.4rem',
        fontSize: '0.7rem', color: changed ? '#c8922a' : 'var(--text-muted)',
        fontStyle: 'italic',
        opacity: 0.85,
      }}>
        {field.direction_blurb}
        {changed && ' · unsaved change'}
        {outOfBounds && ` · value must be between ${floor} and ${ceiling}`}
      </div>
    </div>
  )
}


export default function SettingsTrading() {
  const { user } = useAuth()
  const isDemo = !!user?.is_demo
  const [state,   setState]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [drafts,  setDrafts]  = useState({})  // {field: stringValue} — only fields the user touched
  const [busy, setBusy] = useState(false)
  const [err,  setErr]  = useState('')
  const [ok,   setOk]   = useState('')

  const load = () => {
    setLoading(true); setErr('')
    api.get('/user/risk-caps')
      .then(r => { setState(r.data); setLoading(false); setDrafts({}) })
      .catch(e => {
        const s = e?.response?.status
        if (s === 503) setErr('Your trading workspace is still being set up.')
        else           setErr('Could not load risk caps.')
        setLoading(false)
      })
  }

  useEffect(() => { load() }, [])

  const setDraft = (key, value) => {
    setOk(''); setErr('')
    setDrafts(d => ({ ...d, [key]: value }))
  }

  // Build the request payload — only fields the user changed AND that are
  // in-bounds. Empty if nothing changed.
  const { payload, anyInvalid, anyChanged } = useMemo(() => {
    if (!state) return { payload: {}, anyInvalid: false, anyChanged: false }
    const out = {}
    let invalid = false
    let changed = false
    for (const f of FIELDS) {
      const raw = drafts[f.key]
      if (raw === undefined || raw === '') continue
      const num = f.integer ? parseInt(raw, 10) : parseFloat(raw)
      if (!Number.isFinite(num)) { invalid = true; continue }
      if (num < state.floors[f.key] || num > state.ceilings[f.key]) {
        invalid = true; continue
      }
      if (num === parseFloat(state.current[f.key])) continue  // no-op
      out[f.key] = num
      changed = true
    }
    return { payload: out, anyInvalid: invalid, anyChanged: changed }
  }, [drafts, state])

  const save = async () => {
    setBusy(true); setErr(''); setOk('')
    try {
      const { data } = await api.post('/user/risk-caps', payload)
      setOk(data.detail || `Updated ${Object.keys(payload).length} risk cap(s).`)
      // Refresh current values from server
      const fresh = await api.get('/user/risk-caps')
      setState(fresh.data)
      setDrafts({})
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Save failed.')
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

  if (err && !state) {
    return (
      <SettingsCard title="Trading risk caps" tone="danger">
        <p style={{ fontSize: '0.88rem', color: '#fca5a5', margin: 0 }}>{err}</p>
      </SettingsCard>
    )
  }

  return (
    <div>
      <SettingsCard
        title="Trading risk caps"
        subtitle="Tighten the parameters your bot uses. You can lower these below the operator-set maximums, but never raise them above."
      >
        <div style={{
          background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.18)',
          borderRadius: '0.4rem', padding: '0.7rem 0.85rem', marginBottom: '1rem',
          display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
        }}>
          <Shield size={15} style={{ color: '#22c55e', flexShrink: 0, marginTop: 2 }} />
          <p style={{ fontSize: '0.8rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.55 }}>
            <strong>Defensive by design.</strong> You can only tighten — there's no UI path
            to a riskier setting than the operator's ceiling. Changes apply on the next
            engine cycle (a few seconds).
          </p>
        </div>

        {isDemo && (
          <div style={{
            background: 'rgba(247,147,26,0.08)', border: '1px solid rgba(247,147,26,0.22)',
            borderRadius: '0.4rem', padding: '0.6rem 0.85rem', marginBottom: '1rem',
            display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
          }}>
            <AlertTriangle size={15} style={{ color: '#F7931A', flexShrink: 0, marginTop: 2 }} />
            <p style={{ fontSize: '0.78rem', color: 'var(--text-primary)', margin: 0 }}>
              Read-only in demo mode. Sign up for a real account to tighten your own caps.
            </p>
          </div>
        )}

        {!state?.config_present && (
          <div style={{
            background: 'rgba(247,147,26,0.08)', border: '1px solid rgba(247,147,26,0.22)',
            borderRadius: '0.4rem', padding: '0.6rem 0.85rem', marginBottom: '1rem',
            fontSize: '0.78rem', color: 'var(--text-primary)',
          }}>
            Your trading workspace is still being set up. The values below are the
            operator's defaults — once provisioning finishes you'll be able to tighten them.
          </div>
        )}

        <div>
          {FIELDS.map(f => (
            <RiskRow
              key={f.key}
              field={f}
              current={state.current[f.key]}
              ceiling={state.ceilings[f.key]}
              floor={state.floors[f.key]}
              draft={drafts[f.key]}
              setDraft={setDraft}
              disabled={isDemo || !state.config_present || busy}
            />
          ))}
        </div>

        <div style={{
          marginTop: '1rem', display: 'flex',
          alignItems: 'center', justifyContent: 'space-between', gap: '1rem',
        }}>
          <div style={{ minHeight: 24 }}>
            {ok  && <span style={{ fontSize: '0.82rem', color: '#22c55e' }}>{ok}</span>}
            {err && <span style={{ fontSize: '0.82rem', color: '#fca5a5' }}>{err}</span>}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={() => { setDrafts({}); setErr(''); setOk('') }}
              disabled={!anyChanged && Object.keys(drafts).length === 0}
              style={{
                background: 'transparent', color: 'var(--text-muted)',
                border: '1px solid var(--border)', padding: '0.55rem 1rem',
                borderRadius: '0.35rem', fontSize: '0.82rem', fontWeight: 600,
                cursor: 'pointer',
                opacity: (!anyChanged && Object.keys(drafts).length === 0) ? 0.4 : 1,
              }}
            >
              Discard
            </button>
            <button
              onClick={save}
              disabled={!anyChanged || anyInvalid || busy || isDemo || !state?.config_present}
              style={{
                background: 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
                color: '#1a1a1a', border: 'none',
                padding: '0.6rem 1.3rem',
                fontSize: '0.83rem', fontWeight: 700,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                borderRadius: '0.35rem', cursor: 'pointer',
                opacity: (!anyChanged || anyInvalid || busy || isDemo || !state?.config_present) ? 0.5 : 1,
              }}
            >
              {busy ? 'Saving…' : `Save ${anyChanged ? Object.keys(payload).length : ''} change${Object.keys(payload).length === 1 ? '' : 's'}`.trim()}
            </button>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title="How the engine uses these">
        <ul style={{
          fontSize: '0.83rem', color: 'var(--text-muted)',
          margin: 0, paddingLeft: '1.2rem', lineHeight: 1.7,
        }}>
          <li><strong>Risk per trade</strong> drives position sizing — the engine sizes each new position so a stop-out loses no more than this percent of equity.</li>
          <li><strong>Max position size</strong> is a hard ceiling — even if risk-per-trade math would allow a bigger position, this caps it.</li>
          <li><strong>Max open positions</strong> stops new entries once this many are open. Existing trades keep managing themselves.</li>
          <li><strong>Peak-drawdown halt</strong> compares current equity to all-time peak — triggers a hard halt that requires manual <em>/resume</em> via Telegram.</li>
          <li><strong>Daily-loss halt</strong> compares current equity to UTC-midnight equity — auto-resets at the next midnight.</li>
        </ul>
      </SettingsCard>
    </div>
  )
}
