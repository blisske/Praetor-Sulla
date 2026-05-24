import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /terms + /privacy — public-no-auth pages that render the operator's
 * canonical legal docs.
 *
 * The Markdown body is fetched from /api/legal/{tos|privacy} (which
 * reads the .md files baked into the image at /app/docs/). Rendered
 * with a tiny purpose-built markdown→HTML pass — headings, lists,
 * paragraphs, bold, inline code, links. No external markdown library.
 */
export default function LegalDoc({ which }) {
  const params = useParams()
  // Allow either prop-driven mounting (App.jsx routes pass `which`) or
  // path-param mounting (e.g. /legal/:doc) for flexibility.
  const doc = which || params.doc
  const isTos = doc === 'tos' || doc === 'terms'
  const endpoint = isTos ? '/api/legal/tos' : '/api/legal/privacy'

  const [body, setBody]       = useState('')
  const [version, setVersion] = useState('')
  const [err, setErr]         = useState('')

  useEffect(() => {
    let cancelled = false
    axios.get(endpoint)
      .then(r => {
        if (cancelled) return
        setBody(r.data.body_markdown)
        setVersion(r.data.version)
      })
      .catch(() => {
        if (cancelled) return
        setErr('Could not load the document. Try refreshing.')
      })
    return () => { cancelled = true }
  }, [endpoint])

  const title = isTos ? 'Terms of Service' : 'Privacy Policy'

  return (
    <AuthScaffold
      title={title}
      subtitle={version ? `Version ${version}` : undefined}
      footer={<>
        <Link to="/" className="auth-link">← Home</Link>
        {' · '}
        <Link to={isTos ? '/privacy' : '/terms'} className="auth-link">
          {isTos ? 'Privacy Policy' : 'Terms of Service'}
        </Link>
      </>}
    >
      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.85rem', borderRadius: '0.3rem',
          padding: '0.7rem 0.9rem',
        }}>
          {err}
        </div>
      )}
      {!err && !body && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
          Loading…
        </p>
      )}
      {body && <MarkdownBody text={body} />}
    </AuthScaffold>
  )
}


/**
 * Tiny Markdown renderer — handles the subset of syntax we use in the
 * docs (h1/h2/h3 headings, paragraphs, bullets, bold, inline code,
 * horizontal rules, table syntax for the privacy-policy retention chart).
 * NOT a general markdown engine; deliberate scope limit.
 */
function MarkdownBody({ text }) {
  const html = mdToHtml(text)
  return (
    <div
      style={{
        textAlign: 'left',
        fontSize: '0.85rem',
        color: 'var(--text-primary)',
        lineHeight: 1.65,
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}


function escapeHtml(s) {
  return s.replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
}

function mdToHtml(md) {
  const lines = md.split('\n')
  const out = []
  let i = 0
  let inList = false
  let inTable = false
  let tableHeader = []

  const flushList = () => { if (inList) { out.push('</ul>'); inList = false } }
  const flushTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false } }

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // Headings
    if (/^### /.test(trimmed)) {
      flushList(); flushTable()
      out.push(`<h3 style="margin: 1.4rem 0 0.5rem; color: #c8922a; font-size: 0.95rem;">${inline(trimmed.slice(4))}</h3>`)
    } else if (/^## /.test(trimmed)) {
      flushList(); flushTable()
      out.push(`<h2 style="margin: 1.6rem 0 0.6rem; color: #fcd34d; font-size: 1.05rem;">${inline(trimmed.slice(3))}</h2>`)
    } else if (/^# /.test(trimmed)) {
      flushList(); flushTable()
      out.push(`<h1 style="margin: 0 0 0.8rem; color: var(--text-primary); font-size: 1.25rem;">${inline(trimmed.slice(2))}</h1>`)
    }
    // Horizontal rule
    else if (/^---+$/.test(trimmed)) {
      flushList(); flushTable()
      out.push('<hr style="border: none; border-top: 1px solid rgba(247,147,26,0.15); margin: 1.2rem 0;" />')
    }
    // Table row (very simple: |a|b|c| with separator |---|---|---| on the second row)
    else if (/^\|/.test(trimmed)) {
      flushList()
      const cells = trimmed.split('|').slice(1, -1).map(c => c.trim())
      // Detect separator row → header was on previous line
      if (cells.every(c => /^[-:]+$/.test(c))) {
        // Start the table body — replace the last emitted line with a <thead>
        const headerHtml = '<thead><tr>' + tableHeader.map(c => `<th style="text-align: left; padding: 0.4rem 0.7rem; border-bottom: 1px solid var(--border); color: #c8922a; font-weight: 600; font-size: 0.78rem;">${inline(c)}</th>`).join('') + '</tr></thead>'
        out.push(`<table style="width: 100%; border-collapse: collapse; margin: 0.8rem 0; font-size: 0.82rem;">${headerHtml}<tbody>`)
        inTable = true
      } else if (inTable) {
        out.push('<tr>' + cells.map(c => `<td style="padding: 0.4rem 0.7rem; border-bottom: 1px solid var(--border); color: var(--text-primary);">${inline(c)}</td>`).join('') + '</tr>')
      } else {
        // Header row — buffer until we see the separator
        tableHeader = cells
      }
    }
    // List items
    else if (/^[-*] /.test(trimmed)) {
      flushTable()
      if (!inList) {
        out.push('<ul style="margin: 0.4rem 0 0.8rem; padding-left: 1.4rem;">')
        inList = true
      }
      out.push(`<li style="margin-bottom: 0.3rem;">${inline(trimmed.slice(2))}</li>`)
    }
    // Blank line — close lists/tables, no <br> in output (let paragraphs handle spacing)
    else if (trimmed === '') {
      flushList(); flushTable()
    }
    // Paragraph
    else {
      flushList(); flushTable()
      out.push(`<p style="margin: 0.5rem 0;">${inline(trimmed)}</p>`)
    }

    i++
  }

  flushList(); flushTable()
  return out.join('\n')
}


function inline(s) {
  // 1. Escape HTML
  let out = escapeHtml(s)
  // 2. Inline code (handled before bold so backticks don't double-escape)
  out = out.replace(/`([^`]+)`/g, '<code style="background: var(--bg-elevated); padding: 0.1rem 0.35rem; border-radius: 3px; font-size: 0.85em;">$1</code>')
  // 3. Bold **text**
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // 4. Links [label](url) — note the inline style is appended via concatenation
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" style="color: #c8922a;">$1</a>')
  return out
}
