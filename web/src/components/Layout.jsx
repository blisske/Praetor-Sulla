import { useState } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'
import { useTheme } from '../lib/theme.jsx'
import { LayoutDashboard, History, FlaskConical, BarChart2, Settings, BookOpen, Menu, X, Sun, Moon, LogOut } from 'lucide-react'

const GOLD      = '#c8922a'
const GOLD_LITE = '#e8b84b'
const BLUE      = '#3B82F6'

// Ionic column capital — defining feature is two spiral volutes ("scrolls")
// flanking a horizontal band. Middle order in age; the scholarly one. Maps
// to FX, where every trade is a pair (two opposing curves).
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
      {/* Abacus */}
      <rect x="2"  y="3"  width="50" height="3"  fill="url(#ionicGrad)" />
      {/* Volute connecting band */}
      <rect x="10" y="6"  width="34" height="12" fill="url(#ionicGrad)" />
      {/* Left volute */}
      <circle cx="10" cy="12" r="9" fill="url(#ionicGrad)" />
      <circle cx="10" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="10" cy="12" r="2" fill="url(#ionicGrad)" />
      {/* Right volute */}
      <circle cx="44" cy="12" r="9" fill="url(#ionicGrad)" />
      <circle cx="44" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="44" cy="12" r="2" fill="url(#ionicGrad)" />
      {/* Egg-and-dart band */}
      <rect x="13" y="20" width="28" height="2.5" fill="#1D4ED8" opacity="0.65" />
      {/* Necking */}
      <rect x="19" y="24" width="16" height="3"  fill="#1D4ED8" />
      {/* Shaft */}
      <rect x="17" y="27" width="20" height="23" fill="url(#ionicGrad)" />
      {/* Fluting */}
      <line x1="20" y1="27" x2="20" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="24" y1="27" x2="24" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="27" y1="27" x2="27" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="30" y1="27" x2="30" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="34" y1="27" x2="34" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
    </svg>
  )
}

const nav = [
  { to: '/',        icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/trades',  icon: History,         label: 'Trades'    },
  { to: '/tuning',  icon: FlaskConical,    label: 'Tuning'    },
  { to: '/market',  icon: BarChart2,       label: 'Market'    },
  { to: '/config',  icon: Settings,        label: 'Config'    },
  { to: '/guide',   icon: BookOpen,        label: 'Guide'     },
]

function Sidebar({ onClose, dark, toggle }) {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const handleLogout = () => { logout(); navigate('/login') }

  return (
    <div style={{ background: 'var(--bg-surface)', borderRight: '1px solid var(--border)' }}
         className="w-56 flex-shrink-0 flex flex-col h-full">
      <div style={{ borderBottom: '1px solid var(--border)' }} className="px-6 py-5 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FoundationMark size={22} />
            <span className="font-bold tracking-widest text-sm" style={{ color: 'var(--text-primary)' }}>FOUNDATION</span>
          </div>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Ionic · FX</p>
        </div>
        {onClose && (
          <button onClick={onClose} style={{ color: 'var(--text-muted)' }} className="lg:hidden">
            <X size={18} />
          </button>
        )}
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {nav.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} end={to === '/'} onClick={onClose}>
            {({ isActive }) => (
              <div style={isActive ? {
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 9px 10px 12px',
                borderRadius: 8,
                background: 'rgba(59,130,246,0.15)',
                color: BLUE,
                fontWeight: 600,
                fontSize: 14,
                borderLeft: `3px solid ${BLUE}`,
              } : {
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 12px',
                borderRadius: 8,
                color: 'var(--text-sub)',
                fontSize: 14,
                cursor: 'pointer',
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
      </nav>

      <div style={{ borderTop: '1px solid var(--border)' }} className="px-3 py-4 space-y-1">
        <button onClick={toggle}
          className="flex items-center gap-3 px-3 py-2.5 w-full rounded-lg text-sm transition-colors"
          style={{ color: 'var(--text-sub)', background: 'transparent' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-sub)' }}
        >
          {dark ? <Sun size={16} /> : <Moon size={16} />}
          {dark ? 'Light Mode' : 'Dark Mode'}
        </button>
        <button onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2.5 w-full rounded-lg text-sm transition-colors"
          style={{ color: 'var(--text-sub)', background: 'transparent' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-sub)' }}
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

  return (
    <div style={{ background: 'var(--bg-base)' }} className="flex h-screen">
      <div className="hidden lg:flex">
        <Sidebar dark={dark} toggle={toggle} />
      </div>
      {open && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="fixed inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="relative z-50 flex">
            <Sidebar onClose={() => setOpen(false)} dark={dark} toggle={toggle} />
          </div>
        </div>
      )}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}
             className="lg:hidden flex items-center gap-3 px-4 py-3">
          <button onClick={() => setOpen(true)} style={{ color: 'var(--text-sub)' }}>
            <Menu size={20} />
          </button>
          <div className="flex items-center gap-2">
            <FoundationMark size={18} />
            <span className="font-bold tracking-widest text-sm" style={{ color: 'var(--text-primary)' }}>FOUNDATION</span>
          </div>
        </div>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
