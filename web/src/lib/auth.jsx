import { createContext, useContext, useState } from 'react'
import api from './api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('ionic_token'))

  const login = async (username, password) => {
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    const r = await api.post('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
    localStorage.setItem('ionic_token', r.data.access_token)
    setToken(r.data.access_token)
  }

  const logout = () => {
    localStorage.removeItem('ionic_token')
    setToken(null)
  }

  return (
    <AuthContext.Provider value={{ token, login, logout, isAuth: !!token }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
