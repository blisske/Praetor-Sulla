import { NavLink, Outlet } from 'react-router-dom'
import { User, Link2, ToggleLeft, AlertTriangle, Eye, SlidersHorizontal } from 'lucide-react'
import { useAuth } from '../lib/auth.jsx'

/**
 * /settings — tabbed settings hub. The tab nav lives here; the active
 * tab's content is rendered into the <Outlet /> by React Router.
 *
 * Tabs:
 *   Account   /settings/account  — change password, change email
 *   Broker    /settings/broker   — Oanda connect/replace/disconnect
 *   Trading   /settings/trading  — tighten risk caps (under operator's ceiling)
 *   Mode      /settings/mode     — shadow ↔ live toggle
 *   Danger    /settings/danger   — delete account, disconnect key
 */
const tabs = [
  { to: '/settings/account', icon: User,             label: 'Account' },
  { to: '/settings/broker',  icon: Link2,            label: 'Broker'  },
  { to: '/settings/trading', icon: SlidersHorizontal,label: 'Trading' },
  { to: '/settings/mode',    icon: ToggleLeft,       label: 'Mode'    },
  { to: '/settings/danger',  icon: AlertTriangle,    label: 'Danger'  },
]

export default function Settings() {
  const { user } = useAuth()
  const isDemo = !!user?.is_demo
  return (
    <div className="px-6 py-6 max-w-4xl">
      <h1 className="text-2xl font-bold text-[var(--text-primary)] tracking-tight mb-1">
        Settings
      </h1>
      <p className="text-sm text-[var(--text-muted)] mb-6">
        Manage your account, broker connection, and trading mode.
      </p>

      {isDemo && (
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: '0.7rem',
          background: 'rgba(247,147,26,0.08)',
          border: '1px solid rgba(247,147,26,0.25)',
          borderRadius: '0.4rem',
          padding: '0.75rem 1rem',
          marginBottom: '1.25rem',
        }}>
          <Eye size={16} style={{ color: '#F7931A', flexShrink: 0, marginTop: 2 }} />
          <p style={{ fontSize: '0.84rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.55 }}>
            Read-only in demo mode. Settings changes, broker connections, and account actions
            require a real account.
          </p>
        </div>
      )}

      {/* Tab nav */}
      <div className="flex gap-1 border-b border-[var(--border)] mb-6 -mx-1 overflow-x-auto">
        {tabs.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `
              flex items-center gap-2 px-4 py-2.5 text-sm font-medium
              border-b-2 -mb-px transition-colors whitespace-nowrap
              ${isActive
                ? 'border-[#3B82F6] text-[#3B82F6]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'}
            `}
          >
            <Icon size={15} />
            {label}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </div>
  )
}
