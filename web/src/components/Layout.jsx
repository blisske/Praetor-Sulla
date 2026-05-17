import { useState } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'
import { useTheme } from '../lib/theme.jsx'
import { LayoutDashboard, History, FlaskConical, BarChart2, Settings, BookOpen, Menu, X, Sun, Moon, LogOut } from 'lucide-react'

const GOLD      = '#c8922a'
const GOLD_LITE = '#e8b84b'
const GREEN     = '#10B981'

function PraetorMark({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="pgold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%"   stopColor={GOLD_LITE} />
          <stop offset="55%"  stopColor={GOLD} />
          <stop offset="100%" stopColor="#8a5e10" />
        </linearGradient>
      </defs>
      <rect x="14" y="8" width="4" height="38" rx="2" fill="url(#pgold)" />
      <path d="M18 8 Q38 8 38 19 Q38 30 18 30" stroke="url(#pgold)" strokeWidth="3.5" fill="none" strokeLinecap="round" />
      <path d="M10 34 C14 28, 20 24, 28 18" stroke={GOLD_LITE} strokeWidth="1.2" fill="none" strokeLinecap="round" opacity="0.85" />
      <path d="M8 38 C13 31, 21 26, 30 20"  stroke={GOLD}      strokeWidth="1.0" fill="none" strokeLinecap="round" opacity="0.65" />
      <path d="M7 42 C13 35, 22 29, 32 23"  stroke={GOLD}      strokeWidth="0.8" fill="none" strokeLinecap="round" opacity="0.45" />
      <path d="M8 46 C15 39, 24 33, 33 27"  stroke={GOLD}      strokeWidth="0.6" fill="none" strokeLinecap="round" opacity="0.28" />
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
            <PraetorMark size={22} />
            <span className="font-bold tracking-widest text-sm" style={{ color: 'var(--text-primary)' }}>PRAETOR</span>
          </div>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Sulla · FX</p>
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
                background: 'rgba(16,185,129,0.15)',
                color: GREEN,
                fontWeight: 600,
                fontSize: 14,
                borderLeft: `3px solid ${GREEN}`,
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
            <PraetorMark size={18} />
            <span className="font-bold tracking-widest text-sm" style={{ color: 'var(--text-primary)' }}>PRAETOR</span>
          </div>
        </div>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
