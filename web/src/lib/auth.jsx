import { createContext, useContext, useState } from 'react'
import axios from 'axios'

const AuthContext = createContext(null)


/**
 * Decode a JWT's payload (no signature verification — server gates that).
 * Returns null on any failure; UI should treat null as "no claims known"
 * and call /api/auth/me if specifics matter.
 */
function decodeJwtClaims(token) {
  if (!token) return null
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    // base64url → base64
    const b64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4)
    const json = decodeURIComponent(
      atob(padded).split('').map(c =>
        '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)
      ).join('')
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}


export function AuthProvider({ children }) {
  const [token, setToken] = useState(localStorage.getItem('token'))

  // Post-SaaS cutover (2026-05-22): /api/auth/login takes JSON {email, password}
  // and returns one of TWO shapes:
  //   1. Normal: {token, user}                           — logged in immediately
  //   2. 2FA on: {totp_required, partial_token, ...}     — needs second step
  // Caller (Login.jsx) inspects totp_required to decide whether to navigate or
  // surface the TOTP code prompt.
  //
  // Returns an object: {ok: true, totp_required: false}  on full success
  //                    {ok: true, totp_required: true, partial_token, recovery_codes_remaining}
  //                                                      when 2FA is pending
  const login = async (username, password) => {
    const { data } = await axios.post('/api/auth/login', {
      email: username,
      password,
    })
    if (data.totp_required) {
      return {
        ok: true,
        totp_required: true,
        partial_token: data.partial_token,
        recovery_codes_remaining: data.recovery_codes_remaining,
      }
    }
    localStorage.setItem('token', data.token)
    setToken(data.token)
    return { ok: true, totp_required: false, data }
  }

  // Step 2 of 2FA login — exchange (partial_token, code) for a real JWT
  const completeTotpLogin = async (partial_token, code) => {
    const { data } = await axios.post('/api/auth/login/totp', {
      partial_token, code,
    })
    localStorage.setItem('token', data.token)
    setToken(data.token)
    return data
  }

  const logout = () => {
    localStorage.removeItem('token')
    setToken(null)
  }

  // Decode claims from the live token for quick is_admin / email lookups
  // without an API round-trip. Re-runs when the token state changes.
  // is_demo is set by /api/demo/login and gates write controls in the UI.
  const claims = decodeJwtClaims(token)
  const user = claims ? {
    id:             claims.sub != null ? parseInt(claims.sub, 10) : null,
    email:          claims.email || null,
    email_verified: !!claims.email_verified,
    is_admin:       !!claims.is_admin,
    is_demo:        !!claims.is_demo,
  } : null

  return (
    <AuthContext.Provider value={{ token, login, completeTotpLogin, logout, isAuthed: !!token, user }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
