import { useEffect, useMemo, useState } from 'react'
import api from '../../lib/api.js'
import { useAuth } from '../../lib/auth.jsx'
import { AlertTriangle, Download, Info, FileText, Calculator } from 'lucide-react'

/**
 * /reports/tax — year-end disposal report.
 *
 * Backend:
 *   GET /api/user/tax/years
 *     → { years_live, years_shadow, has_live }
 *   GET /api/user/tax/method
 *     → { method }              (saved preference; loaded once on mount)
 *   GET /api/user/tax/disposals?year=YYYY&method=FIFO&include_shadow=false
 *     → { year, method, is_demo, has_live, disposals[], summary, disclaimer }
 *   GET /api/user/tax/disposals.csv?year=YYYY&method=FIFO&include_shadow=false
 *     → text/csv attachment with disclaimer + 8949-shaped columns
 *
 * UX notes
 *   - Year tabs: live years first, shadow years appear with "(demo)" badge
 *   - Method picker: defaults to saved preference, overridable per-view
 *   - is_demo=true → bright DEMO watermark banner across the page
 *   - "Not tax advice" red banner on every render (legal requirement)
 *   - Empty year shows a friendly empty state instead of an empty table
 */

const ORANGE = '#F7931A'
const GOLD   = '#c8922a'

const METHODS = ['FIFO', 'LIFO', 'HIFO']

// ─── tiny presentational helpers ──────────────────────────────────────────

const fmtUsd = n => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
const fmtQty = n => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  // Doric trades in whole shares — no need for crypto-style fractional precision
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 4 })
}
const fmtDate = s => (s ? String(s).slice(0, 10) : '—')   // YYYY-MM-DD


function SummaryCard({ label, value, sub, tone = 'neutral' }) {
  const toneColor = tone === 'pos' ? '#4ade80'
                  : tone === 'neg' ? '#f87171'
                  : 'var(--text-primary)'
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: '0.6rem',
      padding: '0.9rem 1rem',
      minWidth: 140, flex: '1 1 160px',
    }}>
      <div style={{
        fontSize: '0.66rem', fontWeight: 700, letterSpacing: '0.12em',
        textTransform: 'uppercase', color: 'var(--text-muted)',
        marginBottom: '0.35rem',
      }}>{label}</div>
      <div style={{
        fontSize: '1.2rem', fontWeight: 700, color: toneColor,
        fontVariantNumeric: 'tabular-nums',
      }}>{value}</div>
      {sub && (
        <div style={{
          fontSize: '0.7rem', color: 'var(--text-muted)',
          marginTop: '0.2rem',
        }}>{sub}</div>
      )}
    </div>
  )
}


export default function Tax() {
  const { user } = useAuth()
  const isDemoAcct = !!user?.is_demo

  // Year picker state ---------------------------------------------------
  const [years,    setYears]    = useState({ years_live: [], years_shadow: [], has_live: false })
  const [year,     setYear]     = useState(null)
  const [yearsErr, setYearsErr] = useState('')

  // Method picker state -------------------------------------------------
  const [savedMethod, setSavedMethod] = useState(null)   // server preference, never mutated locally
  const [method,      setMethod]      = useState(null)   // view-level override

  // Report state --------------------------------------------------------
  const [report,    setReport]    = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [err,       setErr]       = useState('')
  const [csvBusy,   setCsvBusy]   = useState(false)
  const [csvErr,    setCsvErr]    = useState('')

  // ── Load years + saved method on mount ──
  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.get('/user/tax/years'),
      api.get('/user/tax/method'),
    ])
      .then(([yrRes, mRes]) => {
        if (cancelled) return
        const yrs = yrRes.data || { years_live: [], years_shadow: [], has_live: false }
        const m   = (mRes.data?.method || 'FIFO').toUpperCase()
        setYears(yrs)
        setSavedMethod(m); setMethod(m)
        // Default-select the most recent live year; fall back to shadow.
        const pick = (yrs.years_live?.length ? yrs.years_live[yrs.years_live.length - 1]
                    : yrs.years_shadow?.length ? yrs.years_shadow[yrs.years_shadow.length - 1]
                    : new Date().getUTCFullYear())
        setYear(pick)
      })
      .catch(e => {
        if (cancelled) return
        const s = e?.response?.status
        if (s === 503) setYearsErr('Your trading workspace is still being set up.')
        else           setYearsErr('Could not load tax data.')
      })
    return () => { cancelled = true }
  }, [])

  // ── Fetch report when year or method changes ──
  useEffect(() => {
    if (!year || !method) return
    let cancelled = false
    setLoading(true); setErr('')
    api.get('/user/tax/disposals', { params: { year, method } })
      .then(r => { if (!cancelled) { setReport(r.data); setLoading(false) } })
      .catch(e => {
        if (cancelled) return
        setErr(e?.response?.data?.detail || 'Could not load disposals.')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [year, method])

  // ── CSV download — must include Authorization header, so axios+blob ──
  const downloadCsv = async () => {
    if (!year || !method) return
    setCsvBusy(true); setCsvErr('')
    try {
      const r = await api.get('/user/tax/disposals.csv', {
        params: { year, method },
        responseType: 'blob',
      })
      // Pull filename out of Content-Disposition if present
      const cd = r.headers?.['content-disposition'] || ''
      const m  = cd.match(/filename="?([^"]+)"?/i)
      const fallback = `foundation-ionic-tax-${year}-${method}${report?.is_demo ? '-DEMO' : ''}.csv`
      const filename = m ? m[1] : fallback

      const url  = URL.createObjectURL(r.data)
      const a    = document.createElement('a')
      a.href     = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      // Give the browser a tick to grab the blob before revoking
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setCsvErr('CSV download failed.')
    } finally {
      setCsvBusy(false)
    }
  }

  // ── Year list — live first, shadow appended with badge ──
  const yearTabs = useMemo(() => {
    const live   = (years.years_live   || []).map(y => ({ year: y, kind: 'live'   }))
    const shadow = (years.years_shadow || [])
      .filter(y => !(years.years_live || []).includes(y))
      .map(y => ({ year: y, kind: 'shadow' }))
    return [...live, ...shadow].sort((a, b) => b.year - a.year)
  }, [years])

  if (yearsErr) {
    return (
      <div className="p-6">
        <div style={{
          background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.3)',
          borderRadius: '0.5rem', padding: '1rem', color: 'var(--text-primary)',
        }}>
          {yearsErr}
        </div>
      </div>
    )
  }

  const summary = report?.summary
  const isDemo  = !!report?.is_demo
  const hasLive = !!report?.has_live

  return (
    <div className="p-6 space-y-5">
      {/* ── Header ────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: '1.4rem', fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <FileText size={20} style={{ color: GOLD }} /> Tax disposals
          </h1>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            Mechanical year-end FX report under IRC §988 (ordinary income — Sched 1 line 8z).
            Lot matching: <strong>{method || '…'}</strong>
            {savedMethod && method && savedMethod !== method && (
              <span style={{ color: '#fbbf24', fontStyle: 'italic', marginLeft: '0.4rem' }}>
                (override · saved default is {savedMethod})
              </span>
            )}
          </p>
        </div>

        <button
          onClick={downloadCsv}
          disabled={!report || csvBusy || (report?.disposals?.length ?? 0) === 0}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.45rem',
            background: 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
            color: '#1a1a1a', border: 'none',
            padding: '0.55rem 1rem', borderRadius: '0.4rem',
            fontSize: '0.82rem', fontWeight: 700,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            cursor: (!report || csvBusy) ? 'not-allowed' : 'pointer',
            opacity: (!report || csvBusy || (report?.disposals?.length ?? 0) === 0) ? 0.5 : 1,
          }}
        >
          <Download size={14} />
          {csvBusy ? 'Building…' : 'Download CSV'}
        </button>
      </div>

      {/* ── Not-tax-advice disclaimer — required on every render ──────── */}
      <div style={{
        background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.18)',
        borderRadius: '0.4rem', padding: '0.75rem 0.95rem',
        display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
      }}>
        <AlertTriangle size={15} style={{ color: '#f87171', flexShrink: 0, marginTop: 2 }} />
        <p style={{ fontSize: '0.8rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.55 }}>
          <strong>Not tax advice.</strong> Foundation generates this report mechanically from
          your trade history. Lot-matching rules, holding-period boundaries, and applicable
          tax rates can vary by jurisdiction. Verify with a CPA before filing.
        </p>
      </div>

      {/* ── DEMO watermark — only when the report is shadow-only ──────── */}
      {isDemo && (
        <div style={{
          background: 'repeating-linear-gradient(45deg, rgba(247,147,26,0.07) 0 14px, rgba(247,147,26,0.02) 14px 28px)',
          border: '1px solid rgba(247,147,26,0.45)',
          borderRadius: '0.4rem', padding: '0.85rem 1rem',
          display: 'flex', alignItems: 'flex-start', gap: '0.6rem',
        }}>
          <AlertTriangle size={16} style={{ color: ORANGE, flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)', lineHeight: 1.55 }}>
            <strong style={{ color: ORANGE, letterSpacing: '0.08em' }}>DEMO MODE</strong> —
            this report is derived from your <em>shadow</em> (paper) trades, NOT real money.
            Numbers will move once you go live — <strong>do not file this.</strong>
          </div>
        </div>
      )}

      {/* ── Controls row: year tabs + method picker ───────────────────── */}
      <div style={{
        background: 'var(--bg-surface)', border: '1px solid var(--border)',
        borderRadius: '0.6rem', padding: '0.85rem 1rem',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: '1rem', flexWrap: 'wrap',
      }}>
        {/* Year tabs */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginRight: '0.3rem' }}>
            Year
          </span>
          {yearTabs.length === 0 ? (
            <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
              No closed lots yet.
            </span>
          ) : yearTabs.map(({ year: y, kind }) => {
            const active = y === year
            return (
              <button
                key={`${kind}-${y}`}
                onClick={() => setYear(y)}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                  padding: '0.4rem 0.75rem',
                  background: active ? GOLD : 'var(--bg-elevated)',
                  color: active ? '#1a1a1a' : 'var(--text-sub)',
                  border: active ? `1px solid ${GOLD}` : '1px solid var(--border)',
                  borderRadius: '0.35rem',
                  fontSize: '0.82rem', fontWeight: 600,
                  cursor: 'pointer', transition: 'background 0.15s, color 0.15s',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {y}
                {kind === 'shadow' && (
                  <span style={{
                    fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    color: active ? '#1a1a1a' : ORANGE,
                    opacity: active ? 0.8 : 1,
                  }}>demo</span>
                )}
              </button>
            )
          })}
        </div>

        {/* Method picker */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Calculator size={14} style={{ color: 'var(--text-muted)' }} />
          <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginRight: '0.25rem' }}>
            Method
          </span>
          {METHODS.map(m => {
            const active = m === method
            return (
              <button
                key={m}
                onClick={() => setMethod(m)}
                title={m === savedMethod ? `Your saved default (${m})` : `Override to ${m}`}
                style={{
                  padding: '0.4rem 0.65rem',
                  background: active ? GOLD : 'var(--bg-elevated)',
                  color: active ? '#1a1a1a' : 'var(--text-sub)',
                  border: active ? `1px solid ${GOLD}` : '1px solid var(--border)',
                  borderRadius: '0.35rem',
                  fontSize: '0.78rem', fontWeight: 700,
                  letterSpacing: '0.04em',
                  cursor: 'pointer', transition: 'background 0.15s, color 0.15s',
                }}
              >
                {m}
              </button>
            )
          })}
        </div>
      </div>

      {/* ── Summary cards row (§988 — no short/long split) ────────────── */}
      {summary && (
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <SummaryCard
            label="Disposals"
            value={summary.disposal_count}
            sub="all §988 ordinary"
          />
          <SummaryCard label="Proceeds"   value={fmtUsd(summary.total_proceeds)}   />
          <SummaryCard label="Cost basis" value={fmtUsd(summary.total_cost_basis)} />
          <SummaryCard
            label="Ordinary income"
            value={fmtUsd(summary.total_ordinary_gain ?? summary.realized_gain)}
            tone={(summary.total_ordinary_gain ?? summary.realized_gain) >= 0 ? 'pos' : 'neg'}
            sub="Sched 1 line 8z"
          />
          <SummaryCard
            label="Fees"
            value={fmtUsd(summary.total_fees)}
            sub="spread + financing"
          />
        </div>
      )}

      {/* ── Disposals table ──────────────────────────────────────────── */}
      {err && (
        <div style={{
          background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.3)',
          borderRadius: '0.5rem', padding: '0.85rem 1rem',
          fontSize: '0.85rem', color: '#fca5a5',
        }}>
          {err}
        </div>
      )}
      {csvErr && (
        <div style={{
          fontSize: '0.78rem', color: '#fca5a5',
        }}>
          {csvErr}
        </div>
      )}

      <div className="rounded-xl overflow-hidden" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Computing lots…
          </div>
        ) : !report || (report?.disposals?.length ?? 0) === 0 ? (
          <div style={{
            padding: '2.5rem 1.5rem', textAlign: 'center',
            color: 'var(--text-muted)', fontSize: '0.85rem',
          }}>
            <Info size={20} style={{ color: 'var(--text-muted)', margin: '0 auto 0.5rem', display: 'block' }} />
            No closed lots in {year}{isDemo ? ' (shadow trades)' : ''}.
            <div style={{ fontSize: '0.75rem', marginTop: '0.35rem', opacity: 0.7 }}>
              Open positions don't generate disposals — only completed sells do.
            </div>
          </div>
        ) : (
          <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-row)' }}>
                {['Symbol', 'Acquired', 'Sold', 'Qty', 'Proceeds', 'Cost basis', 'G/L (ordinary)', 'Days', 'Fees'].map(h => (
                  <th
                    key={h}
                    className="text-left px-3 py-2.5 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.disposals.map((d, i) => {
                const gl   = d.gain_loss_usd
                const win  = gl > 0
                const loss = gl < 0
                // Note: no isLong/term-chip column — under §988 every disposal
                // is ordinary income. Days kept as informational only.
                return (
                  <tr
                    key={`${d.symbol}-${d.date_sold}-${i}`}
                    style={{ borderBottom: '1px solid var(--border-row)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <td className="px-3 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                      {d.symbol}
                    </td>
                    <td className="px-3 py-2.5 text-xs whitespace-nowrap" style={{ color: 'var(--text-sub)' }}>
                      {fmtDate(d.date_acquired)}
                    </td>
                    <td className="px-3 py-2.5 text-xs whitespace-nowrap" style={{ color: 'var(--text-sub)' }}>
                      {fmtDate(d.date_sold)}
                    </td>
                    <td className="px-3 py-2.5 text-right" style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums', fontFamily: "'JetBrains Mono', monospace" }}>
                      {fmtQty(d.qty)}
                    </td>
                    <td className="px-3 py-2.5 text-right" style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums', fontFamily: "'JetBrains Mono', monospace" }}>
                      {fmtUsd(d.proceeds_usd)}
                    </td>
                    <td className="px-3 py-2.5 text-right" style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums', fontFamily: "'JetBrains Mono', monospace" }}>
                      {fmtUsd(d.cost_basis_usd)}
                    </td>
                    <td className="px-3 py-2.5 text-right" style={{
                      color: win ? '#4ade80' : loss ? '#f87171' : 'var(--text-primary)',
                      fontVariantNumeric: 'tabular-nums', fontFamily: "'JetBrains Mono', monospace",
                      fontWeight: 600,
                    }}>
                      {fmtUsd(gl)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-xs" style={{ color: 'var(--text-sub)', fontVariantNumeric: 'tabular-nums' }}>
                      {d.holding_days}
                    </td>
                    <td className="px-3 py-2.5 text-right text-xs" style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums', fontFamily: "'JetBrains Mono', monospace" }}>
                      {fmtUsd(d.fees_usd)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Methodology footnote ─────────────────────────────────────── */}
      {report && (
        <div style={{
          fontSize: '0.72rem', color: 'var(--text-muted)',
          padding: '0.75rem 0', lineHeight: 1.65,
        }}>
          <div><strong>Lot-matching method:</strong> {method} ·
            {' '}<strong>Tax treatment:</strong> IRC §988 ordinary income (default for retail spot FX).
            No short/long-term split. Lot choice is informational under §988 — gains aggregate
            regardless of which lot was matched.
          </div>
          <div style={{ marginTop: '0.2rem' }}>
            <strong>Cost-basis convention:</strong> per-unit acquisition price (in base currency)
            plus a pro-rata share of buy-side spread/fees.
            <strong> Proceeds:</strong> per-unit closing price minus a pro-rata share of sell-side
            spread/fees. Financing/swap charges, the §988(a)(1)(B) opt-out into capital-gains
            treatment, and Section 1256 regulated-futures election are not modeled.
          </div>
        </div>
      )}
    </div>
  )
}
