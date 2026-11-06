import * as React from 'react'

/**
 * Wrap every occurrence of the query terms so a match stands out in a title or snippet.
 * Shared by the file-grid search panel and the sidebar command palette so both highlight
 * the same way.
 */
export function highlightTerms(text: string, terms: string[]): React.ReactNode {
  const usable = terms.map((term) => term.trim()).filter(Boolean)
  if (!text || usable.length === 0) return text
  const escaped = usable
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .sort((a, b) => b.length - a.length)
  const lowered = new Set(escaped.map((term) => term.toLowerCase()))
  return text
    .split(new RegExp(`(${escaped.join('|')})`, 'gi'))
    .map((part, index) =>
      lowered.has(part.toLowerCase()) ? (
        <mark key={index} className="rounded-sm bg-yellow-200/80 px-0.5 text-inherit">
          {part}
        </mark>
      ) : (
        part
      ),
    )
}
