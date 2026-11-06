'use client'

import * as React from 'react'
import {
  ChevronRight, FileText, Folder, FolderOpen, Loader2,
  Image as ImageIcon, Sheet, Presentation, FileCode,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { cn, toDisplayTitle } from '@/lib/utils'
import { useUserStore } from '@/stores'

export interface TreeFolder {
  name: string
  path: string
}

export interface TreeFile {
  name: string
  path: string
  size: number | null
  file_type: string
  document_id: string | null
  document_number: number | null
}

interface TreeListing {
  path: string
  folders: TreeFolder[]
  files: TreeFile[]
}

const EXPANDED_KEY = 'kb-sidebar-tree-expanded'

const CODE_TYPES = new Set(['mjs', 'js', 'ts', 'tsx', 'jsx', 'py', 'sh', 'json', 'xml', 'yml', 'yaml', 'html', 'htm'])
const IMAGE_TYPES = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg', 'bmp'])
const SHEET_TYPES = new Set(['xlsx', 'xls', 'csv'])
const SLIDE_TYPES = new Set(['pptx', 'ppt'])

function fileIcon(fileType: string) {
  if (IMAGE_TYPES.has(fileType)) return <ImageIcon className="size-3 shrink-0 opacity-40" />
  if (SHEET_TYPES.has(fileType)) return <Sheet className="size-3 shrink-0 opacity-40" />
  if (SLIDE_TYPES.has(fileType)) return <Presentation className="size-3 shrink-0 opacity-40" />
  if (CODE_TYPES.has(fileType)) return <FileCode className="size-3 shrink-0 opacity-40" />
  return <FileText className="size-3 shrink-0 opacity-40" />
}

function loadExpanded(): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY)
    if (raw) return new Set(JSON.parse(raw) as string[])
  } catch {
    // A corrupt entry just means a collapsed tree.
  }
  return new Set()
}

interface SidebarTreeProps {
  activePath: string | null
  /** Folder currently shown in the main pane, for highlighting. */
  activeFolderPath?: string | null
  onOpenDocument: (path: string, documentNumber: number | null) => void
  onOpenFiles: () => void
  /** Open a folder's contents in the main pane; the row click goes here. */
  onOpenFolder?: (path: string) => void
  /** Bump to reload the whole tree (after upload/delete/folder changes). */
  version?: number
}

/**
 * Lazy file-explorer tree over the workspace.
 *
 * Each folder is fetched the first time it is expanded and cached for the session; the
 * expanded set is persisted so the tree comes back the way it was left.
 */
export function KBSidebarTree({ activePath, activeFolderPath = null, onOpenDocument, onOpenFiles, onOpenFolder, version = 0 }: SidebarTreeProps) {
  const [listings, setListings] = React.useState<Map<string, TreeListing>>(new Map())
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set())
  const [loading, setLoading] = React.useState<Set<string>>(() => new Set())
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    setExpanded(loadExpanded())
  }, [])

  const fetchLevel = React.useCallback(async (path: string) => {
    setLoading((prev) => new Set(prev).add(path))
    try {
      const token = useUserStore.getState().accessToken ?? ''
      const listing = await apiFetch<TreeListing>(`/v1/tree?path=${encodeURIComponent(path)}`, token)
      setListings((prev) => new Map(prev).set(path, listing))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载目录失败')
    } finally {
      setLoading((prev) => {
        const next = new Set(prev)
        next.delete(path)
        return next
      })
    }
  }, [])

  // Root is always loaded; `version` drops every cached level so the tree reflects a write.
  React.useEffect(() => {
    setListings(new Map())
    void fetchLevel('/')
  }, [fetchLevel, version])

  const toggle = React.useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
        setListings((known) => {
          if (!known.has(path)) void fetchLevel(path)
          return known
        })
      }
      try {
        localStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]))
      } catch {
        // Persisting is a convenience; the tree still works without it.
      }
      return next
    })
  }, [fetchLevel])

  const renderLevel = (path: string, depth: number): React.ReactNode => {
    const listing = listings.get(path)
    if (!listing) {
      return loading.has(path) ? (
        <div className="flex items-center gap-2 px-2 py-1.5 text-[12px] text-muted-foreground/60" style={{ paddingLeft: depth * 12 + 20 }}>
          <Loader2 className="size-3 animate-spin" /> 加载中…
        </div>
      ) : null
    }

    return (
      <>
        {listing.folders.map((folder) => {
          const isOpen = expanded.has(folder.path)
          return (
            <div key={folder.path}>
              <div
                className={cn(
                  'group flex w-full items-center gap-1 rounded-md py-1.5 pr-2 text-[13px] transition-colors',
                  activeFolderPath === folder.path
                    ? 'bg-sidebar-accent font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                )}
                style={{ paddingLeft: depth * 12 + 6 }}
              >
                {/* Chevron toggles; the name opens the folder in the main pane. */}
                <button
                  type="button"
                  onClick={(event) => { event.stopPropagation(); toggle(folder.path) }}
                  className="shrink-0 rounded p-0.5 hover:bg-sidebar-accent cursor-pointer"
                  aria-label={isOpen ? '折叠' : '展开'}
                >
                  <ChevronRight
                    className={cn(
                      'size-3 text-muted-foreground/40 transition-transform duration-150',
                      isOpen && 'rotate-90',
                    )}
                  />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (!isOpen) toggle(folder.path)
                    onOpenFolder?.(folder.path)
                  }}
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left cursor-pointer"
                >
                  {isOpen
                    ? <FolderOpen className="size-3.5 shrink-0 opacity-60" />
                    : <Folder className="size-3.5 shrink-0 opacity-60" />}
                  <span className="truncate">{folder.name}</span>
                </button>
              </div>
              {isOpen && renderLevel(folder.path, depth + 1)}
            </div>
          )
        })}

        {listing.files.map((file) => {
          const indexed = file.document_number != null || file.document_id != null
          const isActive = activePath === file.path
          return (
            <button
              key={file.path}
              type="button"
              onClick={() => {
                if (indexed) onOpenDocument(file.path, file.document_number)
                else onOpenFiles()
              }}
              title={indexed ? file.name : `${file.name}（未索引，在文件视图中查看）`}
              className={cn(
                'flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-[13px] transition-colors cursor-pointer',
                isActive
                  ? 'bg-sidebar-accent font-medium text-foreground'
                  : indexed
                    ? 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground'
                    : 'text-muted-foreground/50 hover:bg-sidebar-accent/60 hover:text-muted-foreground',
              )}
              style={{ paddingLeft: depth * 12 + 20 }}
            >
              {fileIcon(file.file_type)}
              <span className="truncate">{toDisplayTitle(file.name)}</span>
            </button>
          )
        })}

        {listing.folders.length === 0 && listing.files.length === 0 && (
          <div className="px-2 py-1 text-[12px] text-muted-foreground/40" style={{ paddingLeft: depth * 12 + 20 }}>
            空文件夹
          </div>
        )}
      </>
    )
  }

  return (
    <div>
      {renderLevel('/', 0)}
      {error && <div className="px-2 py-1.5 text-[11px] text-destructive/80">{error}</div>}
    </div>
  )
}
