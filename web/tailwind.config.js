export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        base:    'var(--bg-base)',
        surface: 'var(--bg-surface)',
        elevated:'var(--bg-elevated)',
        theme: {
          border: 'var(--border)',
          text:   'var(--text-primary)',
          sub:    'var(--text-sub)',
          muted:  'var(--text-muted)',
          dim:    'var(--text-dim)',
        }
      },
      borderColor: {
        theme: 'var(--border)',
      }
    }
  }
}
