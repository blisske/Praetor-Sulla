import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './lib/auth.jsx'
import Login from './pages/Login.jsx'
import Signup from './pages/Signup.jsx'
import ForgotPassword from './pages/ForgotPassword.jsx'
import ResetPassword from './pages/ResetPassword.jsx'
import VerifyEmail from './pages/VerifyEmail.jsx'
import DemoMode from './pages/DemoMode.jsx'
import LegalDoc from './pages/LegalDoc.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Trades from './pages/Trades.jsx'
import Tuning from './pages/Tuning.jsx'
import Market from './pages/Market.jsx'
import Config from './pages/Config.jsx'
import Guide from './pages/Guide.jsx'
import Layout from './components/Layout.jsx'
import Settings from './pages/Settings.jsx'
import SettingsAccount from './pages/settings/Account.jsx'
import SettingsBroker  from './pages/settings/Broker.jsx'
import SettingsTrading from './pages/settings/Trading.jsx'
import SettingsMode    from './pages/settings/Mode.jsx'
import SettingsDanger  from './pages/settings/Danger.jsx'
import AdminUsers       from './pages/admin/Users.jsx'
import AdminUserDetail  from './pages/admin/UserDetail.jsx'
import AdminProvisioner from './pages/admin/Provisioner.jsx'

function Protected({ children }) {
  const { isAuthed } = useAuth()
  return isAuthed ? children : <Navigate to="/login" replace />
}

function RequireAdmin({ children }) {
  const { isAuthed, user } = useAuth()
  if (!isAuthed) return <Navigate to="/login" replace />
  if (!user?.is_admin) return <Navigate to="/" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      {/* Public auth flows */}
      <Route path="/login"           element={<Login />} />
      <Route path="/signup"          element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password"  element={<ResetPassword />} />
      <Route path="/verify"          element={<VerifyEmail />} />
      <Route path="/demo"            element={<DemoMode />} />
      <Route path="/terms"           element={<LegalDoc which="tos" />} />
      <Route path="/privacy"         element={<LegalDoc which="privacy" />} />

      {/* Authenticated app */}
      <Route path="/" element={<Protected><Layout /></Protected>}>
        <Route index element={<Dashboard />} />
        <Route path="trades" element={<Trades />} />
        <Route path="tuning" element={<Tuning />} />
        <Route path="market" element={<Market />} />
        <Route path="config" element={<Config />} />
        <Route path="guide" element={<Guide />} />
        <Route path="settings" element={<Settings />}>
          <Route index element={<Navigate to="/settings/account" replace />} />
          <Route path="account" element={<SettingsAccount />} />
          <Route path="broker"  element={<SettingsBroker />} />
          <Route path="trading" element={<SettingsTrading />} />
          <Route path="mode"    element={<SettingsMode />} />
          <Route path="danger"  element={<SettingsDanger />} />
        </Route>

        {/* Admin-only — non-admins get bounced to / via RequireAdmin */}
        <Route path="admin" element={<RequireAdmin><AdminUsers /></RequireAdmin>} />
        <Route path="admin/users/:id" element={<RequireAdmin><AdminUserDetail /></RequireAdmin>} />
        <Route path="admin/provisioner" element={<RequireAdmin><AdminProvisioner /></RequireAdmin>} />
      </Route>
    </Routes>
  )
}
