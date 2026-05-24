/**
 * Reusable card for grouping a Settings section (e.g., "Change password",
 * "Oanda connection"). Matches the brand language used elsewhere in the
 * app — surface background, subtle border, gold accent header text.
 *
 * Props:
 *   title   Required. Section title.
 *   subtitle Optional explanatory copy under the title.
 *   tone    Optional. 'default' (default) | 'danger' — danger gets a red
 *           accent border on the left edge.
 *   children Body content.
 */
export default function SettingsCard({ title, subtitle, tone = 'default', children }) {
  const accent = tone === 'danger' ? '#dc2626' : '#3B82F6'
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderLeft: `3px solid ${accent}`,
      borderRadius: '0.5rem',
      padding: '1.25rem 1.5rem',
      marginBottom: '1rem',
    }}>
      <h2 style={{
        fontSize: '0.78rem',
        fontWeight: 700,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: tone === 'danger' ? '#fca5a5' : '#3B82F6',
        margin: 0,
        marginBottom: subtitle ? '0.3rem' : '1rem',
      }}>
        {title}
      </h2>
      {subtitle && (
        <p style={{
          fontSize: '0.85rem',
          color: 'var(--text-muted)',
          margin: 0,
          marginBottom: '1rem',
          lineHeight: 1.55,
        }}>
          {subtitle}
        </p>
      )}
      {children}
    </div>
  )
}
