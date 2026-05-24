import { useState } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'
import { LayoutDashboard, History, FlaskConical, LogOut, BarChart2, Settings, SlidersHorizontal, Menu, X, Sun, Moon, BookOpen, UserCog, ShieldCheck, FileText } from 'lucide-react'
import { useTheme } from '../lib/theme.jsx'
import DemoModeBanner from './DemoModeBanner.jsx'
import TosReacceptModal from './TosReacceptModal.jsx'
import ProvisionGate from './ProvisionGate.jsx'

const GOLD      = '#c8922a'
const GOLD_LITE = '#e8b84b'
const ORANGE    = '#F7931A'

// Ionic column capital — abacus + echinus + necking + fluted shaft.
// Ionic column capital — abacus + connecting band + paired volutes
// (scrolls) + egg-and-dart band + fluted shaft. The "scholar's" order
// of classical Greek architecture — graceful, balanced, intermediate
// between Doric's plainness and Corinthian's ornament. Matches FX:
// macro-driven, deliberate, less volatile than crypto. Blue gradient
// = Ionic's brand color (vs Doric emerald, Corinthian gold).
function FoundationMark({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ionicGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#93C5FD" />
          <stop offset="55%"  stopColor="#3B82F6" />
          <stop offset="100%" stopColor="#1D4ED8" />
        </linearGradient>
      </defs>
      <rect x="2"  y="3"  width="50" height="3"  fill="url(#ionicGrad)" />
      <rect x="10" y="6"  width="34" height="12" fill="url(#ionicGrad)" />
      <circle cx="10" cy="12" r="9" fill="url(#ionicGrad)" />
      <circle cx="10" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="10" cy="12" r="2" fill="url(#ionicGrad)" />
      <circle cx="44" cy="12" r="9" fill="url(#ionicGrad)" />
      <circle cx="44" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="44" cy="12" r="2" fill="url(#ionicGrad)" />
      <rect x="13" y="20" width="28" height="2.5" fill="#1D4ED8" opacity="0.65" />
      <rect x="19" y="24" width="16" height="3"  fill="#1D4ED8" />
      <rect x="17" y="27" width="20" height="23" fill="url(#ionicGrad)" />
      <line x1="20" y1="27" x2="20" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="24" y1="27" x2="24" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="27" y1="27" x2="27" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="30" y1="27" x2="30" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="34" y1="27" x2="34" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
    </svg>
  )
}

// Trading-app nav (top of sidebar — bot operations)
const nav = [
  { to: '/',             icon: LayoutDashboard,   label: 'Dashboard' },
  { to: '/trades',       icon: History,           label: 'Trades'    },
  { to: '/tuning',       icon: FlaskConical,      label: 'Tuning'    },
  { to: '/market',       icon: BarChart2,         label: 'Market'    },
  { to: '/config',       icon: SlidersHorizontal, label: 'Config'    },
  { to: '/reports/tax',  icon: FileText,          label: 'Tax'       },
  { to: '/guide',        icon: BookOpen,          label: 'Guide'     },
]

// Account-level nav (bottom of sidebar — user profile + broker + mode)
const accountNav = [
  { to: '/settings/account', icon: UserCog,  label: 'Settings' },
]

// Admin-only nav — gated on user.is_admin in the render path
const adminNav = [
  { to: '/admin',              icon: ShieldCheck, label: 'Admin' },
  { to: '/admin/provisioner',  icon: SlidersHorizontal, label: 'Provisioner' },
]

function Sidebar({ onClose, dark, toggle }) {
  const { logout, user } = useAuth()
  const navigate = useNavigate()
  const handleLogout = () => { logout(); navigate('/login') }
  const isAdmin = !!user?.is_admin

  return (
    <div style={{ background: 'var(--bg-surface)', borderRight: '1px solid var(--border)' }}
         className="w-56 flex-shrink-0 flex flex-col h-full">

      {/* Header */}
      <div style={{ borderBottom: '1px solid var(--border)' }} className="px-6 py-5 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FoundationMark size={22} />
            <span className="font-bold text-[var(--text-primary)] tracking-widest text-sm">FOUNDATION</span>
          </div>
          <p className="text-[var(--text-muted)] text-xs mt-1">Ionic · FX Majors</p>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] lg:hidden">
            <X size={18} />
          </button>
        )}
      </div>

      {/* Nav — trading-app sections */}
      <nav className="flex-1 px-3 py-4 space-y-1 flex flex-col">
        <div className="space-y-1">
          {nav.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={onClose}
            >
              {({ isActive }) => (
                <div style={isActive ? {
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 12px',
                  borderRadius: 8,
                  background: 'rgba(247,147,26,0.15)',
                  color: ORANGE,
                  fontWeight: 600,
                  fontSize: 14,
                  borderLeft: `3px solid ${ORANGE}`,
                  paddingLeft: 9,
                } : {
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 12px',
                  borderRadius: 8,
                  color: 'var(--text-sub)',
                  fontSize: 14,
                  cursor: 'pointer',
                  transition: 'background 0.15s, color 0.15s',
                }}
                onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = '#fff' }}}
                onMouseLeave={e => { if (!isActive) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-sub)' }}}
                >
                  <Icon size={16} />
                  {label}
                </div>
              )}
            </NavLink>
          ))}
        </div>

        {/* Spacer pushes account-section to bottom of the nav region */}
        <div className="flex-1" />

        {/* Admin section — only renders when user.is_admin */}
        {isAdmin && (
          <div className="space-y-1 pt-2 mt-2 border-t border-[var(--border)]">
            <div style={{
              fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.18em',
              textTransform: 'uppercase', color: 'var(--text-dim)',
              padding: '0.3rem 0.75rem 0.4rem',
            }}>
              Operator
            </div>
            {adminNav.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/admin'}
                onClick={onClose}
              >
                {({ isActive }) => (
                  <div style={isActive ? {
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px',
                    borderRadius: 8,
                    background: 'rgba(200,146,42,0.15)',
                    color: GOLD,
                    fontWeight: 600,
                    fontSize: 14,
                    borderLeft: `3px solid ${GOLD}`,
                    paddingLeft: 9,
                  } : {
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px',
                    borderRadius: 8,
                    color: 'var(--text-sub)',
                    fontSize: 14,
                    cursor: 'pointer',
                    transition: 'background 0.15s, color 0.15s',
                  }}
                  onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = '#fff' }}}
                  onMouseLeave={e => { if (!isActive) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-sub)' }}}
                  >
                    <Icon size={16} />
                    {label}
                  </div>
                )}
              </NavLink>
            ))}
          </div>
        )}

        {/* Account-level — separated from trading-app sections */}
        <div className="space-y-1 pt-2 mt-2 border-t border-[var(--border)]">
          {accountNav.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
            >
              {({ isActive }) => {
                // Active when on any /settings/* sub-route
                const sectionActive = isActive || window.location.pathname.startsWith('/settings/')
                return (
                  <div style={sectionActive ? {
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px',
                    borderRadius: 8,
                    background: 'rgba(200,146,42,0.15)',
                    color: GOLD,
                    fontWeight: 600,
                    fontSize: 14,
                    borderLeft: `3px solid ${GOLD}`,
                    paddingLeft: 9,
                  } : {
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px',
                    borderRadius: 8,
                    color: 'var(--text-sub)',
                    fontSize: 14,
                    cursor: 'pointer',
                    transition: 'background 0.15s, color 0.15s',
                  }}
                  onMouseEnter={e => { if (!sectionActive) { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = '#fff' }}}
                  onMouseLeave={e => { if (!sectionActive) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-sub)' }}}
                  >
                    <Icon size={16} />
                    {label}
                  </div>
                )
              }}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Footer */}
      <div style={{ borderTop: '1px solid var(--border)' }} className="px-3 py-4 space-y-1">
        <button
          onClick={toggle}
          className="flex items-center gap-3 px-3 py-2.5 w-full rounded-lg text-sm text-[var(--text-sub)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors"
        >
          {dark ? <Sun size={16} /> : <Moon size={16} />}
          {dark ? 'Light Mode' : 'Dark Mode'}
        </button>
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2.5 w-full rounded-lg text-sm text-[var(--text-sub)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors"
        >
          <LogOut size={16} />
          Sign Out
        </button>
      </div>
    </div>
  )
}

export default function Layout() {
  const [open, setOpen] = useState(false)
  const { dark, toggle } = useTheme()
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div style={{ background: 'var(--bg-base)' }} className="flex h-screen">
      {/* ToS re-acceptance modal — surfaces when user.tos_needs_reaccept
          is true (operator bumped CURRENT_TOS_VERSION OR user was seeded
          before any ToS existed). Blocking — must accept OR sign out. */}
      <TosReacceptModal
        user={user}
        isDemo={!!user?.is_demo}
        onSignOut={() => { logout(); navigate('/login') }}
      />

      {/* Desktop sidebar */}
      <div className="hidden lg:flex">
        <Sidebar dark={dark} toggle={toggle} />
      </div>

      {/* Mobile overlay */}
      {open && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="fixed inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="relative z-50 flex">
            <Sidebar onClose={() => setOpen(false)} dark={dark} toggle={toggle} />
          </div>
        </div>
      )}

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile topbar */}
        <div style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}
             className="lg:hidden flex items-center gap-3 px-4 py-3">
          <button onClick={() => setOpen(true)} className="text-[var(--text-sub)] hover:text-[var(--text-primary)]">
            <Menu size={20} />
          </button>
          <div className="flex items-center gap-2">
            <FoundationMark size={18} />
            <span className="font-bold text-[var(--text-primary)] tracking-widest text-sm">FOUNDATION</span>
          </div>
        </div>
        <main className="flex-1 overflow-auto">
          {/* Banner renders ONLY when JWT has is_demo=true — zero
              footprint for regular users + admins. */}
          <div className="px-6 pt-4">
            <DemoModeBanner />
          </div>
          <ProvisionGate>
            <Outlet />
          </ProvisionGate>
        </main>
      </div>
    </div>
  )
}
