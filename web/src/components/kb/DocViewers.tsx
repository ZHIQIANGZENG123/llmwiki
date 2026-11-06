'use client'

import * as React from 'react'
import { Loader2, FileText } from 'lucide-react'
import { useUserStore } from '@/stores'
import { apiFetch } from '@/lib/api'
import { cn } from '@/lib/utils'
import dynamic from 'next/dynamic'

const PdfViewer = dynamic(() => import('@/components/viewer/PdfViewer'), { ssr: false })
const MarkdownClipViewer = dynamic(
  () => import('@/components/viewer/MarkdownClipViewer'),
  { ssr: false },
)

function useDocumentUrl(documentId: string) {
  // Depend on token presence, not its value: a refreshed token must not re-sign the URL and reload the viewer.
  const hasToken = useUserStore((s) => Boolean(s.accessToken))
  const [url, setUrl] = React.useState<string | null>(null)
  const [error, setError] = React.useState(false)

  React.useEffect(() => {
    const token = useUserStore.getState().accessToken
    if (!token) return
    let cancelled = false
    apiFetch<{ url: string }>(`/v1/documents/${documentId}/url`, token)
      .then((res) => { if (!cancelled) setUrl(res.url) })
      .catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true }
  }, [documentId, hasToken])

  return { url, error }
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="size-5 animate-spin text-muted-foreground" />
    </div>
  )
}

function ErrorMessage({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center h-full">
      <p className="text-sm text-destructive">{message}</p>
    </div>
  )
}

export function PdfDocViewer({ documentId, title, initialPage, hideToolbar }: { documentId: string; title: string; initialPage?: number; hideToolbar?: boolean }) {
  const { url, error } = useDocumentUrl(documentId)
  if (error) return <ErrorMessage message="Failed to load PDF" />
  if (!url) return <LoadingSpinner />
  return <PdfViewer fileUrl={url} documentId={documentId} title={title} initialPage={initialPage} hideToolbar={hideToolbar} />
}

export function ImageViewer({ documentId, title }: { documentId: string; title: string }) {
  const { url, error } = useDocumentUrl(documentId)
  if (error) return <ErrorMessage message="Failed to load image" />
  if (!url) return <LoadingSpinner />

  return (
    <div className="h-full overflow-auto flex items-center justify-center p-4 bg-muted/30">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={title} className="max-w-full max-h-full object-contain rounded-md" />
    </div>
  )
}

export function HtmlDocViewer({ documentId, title: _title }: { documentId: string; title: string }) {
  return <MarkdownClipViewer documentId={documentId} className="h-full" />
}


/**
 * The spreadsheet processor stores one `## sheet` heading plus pipe-delimited rows, so the
 * viewer can rebuild a real table from text it already has. Anything that does not look
 * like rows is shown verbatim, which keeps unusual workbooks readable.
 */
type SheetBlock =
  | { kind: 'table'; heading: string | null; rows: string[][] }
  | { kind: 'text'; heading: string | null; lines: string[] }

function parseSpreadsheet(content: string): SheetBlock[] {
  const blocks: SheetBlock[] = []
  let heading: string | null = null
  let rows: string[][] = []
  let lines: string[] = []

  const flush = () => {
    if (rows.length >= 2) {
      blocks.push({ kind: 'table', heading, rows })
    } else if (rows.length === 1) {
      blocks.push({ kind: 'text', heading, lines: [...lines, rows[0].join(' | ')] })
    } else if (lines.length) {
      blocks.push({ kind: 'text', heading, lines })
    }
    rows = []
    lines = []
  }

  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trimEnd()
    if (/^##\s+/.test(line)) {
      flush()
      heading = line.replace(/^##\s+/, '').trim()
      continue
    }
    if (!line.trim()) {
      flush()
      continue
    }
    if (line.includes('|')) {
      const cells = line.split('|').map((cell) => cell.trim())
      // A trailing pipe yields one empty cell that is not data.
      if (cells.length > 1 && cells[cells.length - 1] === '') cells.pop()
      rows.push(cells)
      continue
    }
    if (rows.length) flush()
    lines.push(line)
  }
  flush()
  return blocks
}

const NUMERIC_CELL = /^-?\d+([.,]\d+)?$/

function SpreadsheetView({ content }: { content: string }) {
  const blocks = React.useMemo(() => parseSpreadsheet(content), [content])

  return (
    <div className="flex-1 overflow-auto px-6 py-6">
      <div className="mx-auto max-w-5xl space-y-8">
        {blocks.map((block, index) => (
          <section key={index}>
            {block.heading && (
              <h2 className="mb-2 text-sm font-semibold text-foreground">{block.heading}</h2>
            )}
            {block.kind === 'table' ? (
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full border-collapse text-xs">
                  <thead className="bg-muted/50">
                    <tr>
                      {block.rows[0].map((cell, column) => (
                        <th
                          key={column}
                          className="whitespace-nowrap border-b border-border px-2 py-1.5 text-left font-medium"
                        >
                          {cell || '\u00a0'}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.slice(1).map((row, rowIndex) => (
                      <tr key={rowIndex} className="hover:bg-muted/30">
                        {row.map((cell, column) => (
                          <td
                            key={column}
                            className={cn(
                              'border-b border-border/60 px-2 py-1.5 align-top',
                              NUMERIC_CELL.test(cell) && 'text-right tabular-nums',
                            )}
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
                {block.lines.join('\n')}
              </pre>
            )}
          </section>
        ))}
        {blocks.length === 0 && (
          <p className="text-xs text-muted-foreground">这个表格没有可显示的内容</p>
        )}
      </div>
    </div>
  )
}

export function ContentViewer({ documentId, title, fileType }: { documentId: string; title: string; fileType: string }) {
  const token = useUserStore((s) => s.accessToken)
  const [content, setContent] = React.useState<string | null>(null)
  const [error, setError] = React.useState(false)

  React.useEffect(() => {
    if (!token) return
    let cancelled = false
    apiFetch<{ content: string }>(`/v1/documents/${documentId}/content`, token)
      .then((res) => { if (!cancelled) setContent(res.content ?? '') })
      .catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true }
  }, [documentId, token])

  if (error) return <ErrorMessage message="Failed to load content" />
  if (content === null) return <LoadingSpinner />

  const isHtml = fileType === 'html' || fileType === 'htm'
  const isSpreadsheet = ['xlsx', 'xls', 'csv'].includes(fileType)

  if (isSpreadsheet) {
    return (
      <div className="h-full flex flex-col">
        <SpreadsheetView content={content} />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {isHtml ? (
        <iframe
          srcDoc={content}
          sandbox="allow-same-origin"
          className="flex-1 w-full bg-white"
          title={title}
        />
      ) : (
        <div className="flex-1 overflow-auto">
          <div className="max-w-3xl mx-auto px-8 py-6 prose prose-sm dark:prose-invert">
            <pre className="whitespace-pre-wrap text-sm font-mono">{content}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

export function UnsupportedViewer({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8">
      <div className="flex items-center justify-center w-16 h-16 rounded-xl bg-muted">
        <FileText size={28} className="text-muted-foreground" />
      </div>
      <div className="text-center">
        <h1 className="text-lg font-medium">{title}</h1>
        <p className="text-xs text-muted-foreground mt-2">File viewer coming soon</p>
      </div>
    </div>
  )
}

export function ProcessingViewer({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8">
      <Loader2 className="size-8 animate-spin text-muted-foreground" />
      <div className="text-center">
        <h1 className="text-lg font-medium">{title}</h1>
        <p className="text-xs text-muted-foreground mt-2">Processing document...</p>
      </div>
    </div>
  )
}

export function FailedViewer({ title, errorMessage }: { title: string; errorMessage?: string | null }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8">
      <div className="flex items-center justify-center w-16 h-16 rounded-xl bg-destructive/10">
        <FileText size={28} className="text-destructive" />
      </div>
      <div className="text-center">
        <h1 className="text-lg font-medium">{title}</h1>
        <p className="text-xs text-destructive mt-2">Processing failed</p>
        {errorMessage && (
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">{errorMessage}</p>
        )}
      </div>
    </div>
  )
}
