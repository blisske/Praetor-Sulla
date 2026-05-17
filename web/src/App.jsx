import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './lib/auth.jsx'
import { ThemeProvider } from './lib/theme.jsx'
import Layout from './components/Layout.jsx'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Trades from './pages/Trades.jsx'
import Tuning from './pages/Tuning.jsx'
import Market from './pages/Market.jsx'
import Config from './pages/Config.jsx'
import Guide from './pages/Guide.jsx'

function Protected({ children }) {
  const { isAuth } = useAuth()
  return isAuth ? children : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<Protected><Layout /></Protected>}>
              <Route index element={<Dashboard />} />
              <Route path="trades" element={<Trades />} />
              <Route path="tuning" element={<Tuning />} />
              <Route path="market" element={<Market />} />
              <Route path="config" element={<Config />} />
              <Route path="guide" element={<Guide />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  )
}
