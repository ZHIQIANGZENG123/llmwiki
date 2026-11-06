'use client'

import * as React from 'react'
import Link from 'next/link'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronLeft, ChevronRight, FileText, NotepadText,
  Upload, BookOpen, ArrowUpRight, Search as SearchIcon,
  Lightbulb, Box, ScrollText, Network, Folder, FolderOpen, Check, ListChecks, Lock, Plug,
  PanelLeftClose, PanelLeftOpen, History,
} from 'lucide-react'
import {
  CommandDialog, CommandInput, CommandList, CommandItem,
  CommandEmpty, CommandGroup, CommandSeparator,
} from '@/components/ui/command'
import { cn, toDisplayTitle } from '@/lib/utils'
import { apiFetch } from '@/lib/api'
import { highlightTerms } from '@/lib/highlight'
import { useUserStore } from '@/stores'
import { WikiSelector } from '@/components/kb/WikiSelector'
import { KBSidebarTree } from '@/components/kb/KBSidebarTree'
import { SidenavUserMenu } from '@/components/kb/SidenavUserMenu'
import { openMcpConnectionDock } from '@/components/connections/McpConnectionDock'
import type { DocumentListItem, WikiNode } from '@/lib/types'

const SIDENAV_COLLAPSED_KEY = 'kb-sidenav-collapsed'
const isLocal = process.env.NEXT_PUBLIC_MODE === 'local'

interface ContentHit {
  document_id: string
  document_number: number
  filename: string
  title: string
  path: string
  file_type: string
  page: number | null
  snippet: string
}

// Restores the collapsed state before first paint (no expanded-rail flash) without SSR warnings.
const useIsomorphicLayoutEffect = typeof window === 'undefined' ? React.useEffect : React.useLayoutEffect

// Only normalize all-lowercase names (file slugs); preserve intentional casing like "GRPO" or "LoRA".
function withoutLegacyActivityPages(nodes: WikiNode[]): WikiNode[] {
  const result: WikiNode[] = []
  for (const node of nodes) {
    const children = node.children ? withoutLegacyActivityPages(node.children) : undefined
    const normalizedPath = node.path?.replace(/^\/wiki\/?/, '')
    if (normalizedPath === 'log.md' || normalizedPath === 'index.json') {
      if (children?.length) result.push({ ...node, path: undefined, docNumber: null, children })
      continue
    }
    result.push({ ...node, children })
  }
  return result
}


interface KBSidenavProps {
  kbId: string
  kbName: string
  wikiTree: WikiNode[]
  wikiActivePath: string | null
  onWikiNavigate: (path: string, docNumber?: number | null) => void
  sourceDocs: DocumentListItem[]
  hasWiki: boolean
  loading: boolean
  onUpload: () => void
  filesViewActive: boolean
  onOpenFiles: () => void
  graphViewActive: boolean
  onGraphToggle: () => void
  onOpenSourceDoc: (docId: string) => void
  /** Folder shown in the main pane, so the tree can highlight it. */
  activeFolderPath?: string | null
  /** Open a folder's contents in the main pane. */
  onOpenFolder?: (path: string) => void
  recentActive?: boolean
  onRecentSelect?: () => void
  courseMode?: boolean
  courseCurrentPath?: string | null
  courseProgress?: { completed: number; total: number }
}

export function KBSidenav({
  kbId,
  kbName,
  wikiTree,
  wikiActivePath,
  onWikiNavigate,
  sourceDocs,
  hasWiki,
  loading,
  onUpload,
  filesViewActive,
  onOpenFiles,
  graphViewActive,
  onGraphToggle,
  onOpenSourceDoc,
  activeFolderPath = null,
  onOpenFolder,
  recentActive = false,
  onRecentSelect,
  courseMode = false,
  courseCurrentPath = null,
  courseProgress,
}: KBSidenavProps) {
  const [searchOpen, setSearchOpen] = React.useState(false)
  const searchTriggerRef = React.useRef<HTMLButtonElement>(null)
  const collapsedSearchRef = React.useRef<HTMLButtonElement>(null)

  // The palette used to match titles only. Typing a word that appears inside a document
  // found nothing, so ask the API for content hits as well.
  const [contentQuery, setContentQuery] = React.useState('')
  const [contentHits, setContentHits] = React.useState<ContentHit[]>([])
  const [searchingContent, setSearchingContent] = React.useState(false)

  React.useEffect(() => {
    if (!searchOpen) {
      setContentQuery('')
      setContentHits([])
      setSearchingContent(false)
      return
    }
    const query = contentQuery.trim()
    if (query.length < 2) {
      setContentHits([])
      setSearchingContent(false)
      return
    }
    let cancelled = false
    setSearchingContent(true)
    const timer = setTimeout(() => {
      const token = useUserStore.getState().accessToken ?? ''
      apiFetch<{ results: ContentHit[] }>(`/v1/search?q=${encodeURIComponent(query)}&limit=20`, token)
        .then((res) => { if (!cancelled) setContentHits(res.results ?? []) })
        .catch(() => { if (!cancelled) setContentHits([]) })
        .finally(() => { if (!cancelled) setSearchingContent(false) })
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [contentQuery, searchOpen])

  const restoreSearchFocus = React.useCallback((event: Event) => {
    event.preventDefault()
    const target = searchTriggerRef.current ?? collapsedSearchRef.current
    target?.focus()
  }, [])
  const [collapsed, setCollapsed] = React.useState(false)

  useIsomorphicLayoutEffect(() => {
    setCollapsed(localStorage.getItem(SIDENAV_COLLAPSED_KEY) === '1')
  }, [])

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(SIDENAV_COLLAPSED_KEY, next ? '1' : '0')
  }

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  const isMac = React.useMemo(() =>
    typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.userAgent),
  [])

  const visibleWikiTree = React.useMemo(() => withoutLegacyActivityPages(wikiTree), [wikiTree])

  // The tree shows the workspace, not the generated wiki, so the hub page needs its own pin.
  const overviewNode = React.useMemo(
    () => visibleWikiTree.find((node) => node.path != null && /(^|\/)overview\.md$/.test(node.path)) ?? null,
    [visibleWikiTree],
  )

  const paletteTerms = React.useMemo(
    () => contentQuery.trim().split(/\s+/).filter(Boolean),
    [contentQuery],
  )

  const allSearchableItems = React.useMemo(() => {
    const items: { type: 'wiki' | 'source'; title: string; keywords: string; tags: string[]; path?: string; docNumber?: number | null; doc?: DocumentListItem }[] = []
    const addWikiNodes = (nodes: WikiNode[], parentPath = '') => {
      for (const node of nodes) {
        if (node.path) {
          const matchingDoc = sourceDocs.find((d) => d.path === '/wiki/' && d.filename === node.path?.split('/').pop())
          const tags = matchingDoc?.tags ?? []
          items.push({
            type: 'wiki',
            title: node.title,
            keywords: [node.title, node.path, parentPath, ...tags].filter(Boolean).join(' '),
            tags,
            path: node.path,
            docNumber: node.docNumber,
          })
        }
        if (node.children) addWikiNodes(node.children, node.title)
      }
    }
    addWikiNodes(visibleWikiTree)
    for (const doc of sourceDocs) {
      const tags = doc.tags ?? []
      items.push({
        type: 'source',
        title: doc.title || doc.filename,
        keywords: [doc.title, doc.filename, doc.path, doc.file_type, ...tags].filter(Boolean).join(' '),
        tags,
        doc,
      })
    }
    return items
  }, [visibleWikiTree, sourceDocs])

  const sourceCount = sourceDocs.length

  return (
    <div
      className={cn(
        'h-full flex flex-col bg-sidebar border-r border-sidebar-border overflow-hidden transition-[width] duration-200',
        collapsed ? 'w-12' : 'w-[272px]',
      )}
    >
      {collapsed && (
        <div className="flex flex-col items-center gap-1.5 pt-2">
          <AllWikisLink className="size-8" />
          <button
            onClick={toggleCollapsed}
            title="Expand sidebar"
            className="flex items-center justify-center size-8 rounded-md text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent transition-colors cursor-pointer"
          >
            <PanelLeftOpen className="size-3.5" />
          </button>
          <button
            ref={collapsedSearchRef}
            onClick={() => setSearchOpen(true)}
            title="Search"
            className="flex items-center justify-center size-8 rounded-md text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent transition-colors cursor-pointer"
          >
            <SearchIcon className="size-3.5" />
          </button>
          {!courseMode && onRecentSelect && (
            <button
              onClick={onRecentSelect}
              title="Recent changes"
              aria-current={recentActive ? 'page' : undefined}
              className={cn(
                'flex size-8 cursor-pointer items-center justify-center rounded-md transition-colors',
                recentActive
                  ? 'bg-sidebar-accent text-foreground'
                  : 'text-muted-foreground/50 hover:bg-sidebar-accent hover:text-muted-foreground',
              )}
            >
              <History className="size-3.5" />
            </button>
          )}
          {!courseMode && (
            <>
              <button
                onClick={onGraphToggle}
                title="Knowledge graph"
                className={cn(
                  'flex items-center justify-center size-8 rounded-md transition-colors cursor-pointer',
                  graphViewActive
                    ? 'bg-sidebar-accent text-foreground'
                    : 'text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent',
                )}
              >
                <Network className="size-3.5" />
              </button>
              <button
                onClick={onUpload}
                title="Upload files"
                className="flex items-center justify-center size-8 rounded-md text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent transition-colors cursor-pointer"
              >
                <Upload className="size-3.5" />
              </button>
            </>
          )}
        </div>
      )}

      {!collapsed && (
        <>
          <div className="shrink-0 px-2 pt-2 pb-2 flex items-center gap-1">
            <AllWikisLink className="size-7 shrink-0" />
            <div className="flex-1 min-w-0">
              <WikiSelector kbId={kbId} kbName={kbName} />
            </div>
            <button
              onClick={toggleCollapsed}
              title="Collapse sidebar"
              className="flex items-center justify-center size-7 shrink-0 rounded-md text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent transition-colors cursor-pointer"
            >
              <PanelLeftClose className="size-3.5" />
            </button>
          </div>
        </>
      )}

      {!collapsed && courseMode && courseProgress && courseProgress.total > 0 && (
        <div className="shrink-0 px-3 pb-2 -mt-1 flex items-center gap-2">
          <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-foreground/30 transition-[width]"
              style={{ width: `${Math.round((courseProgress.completed / courseProgress.total) * 100)}%` }}
            />
          </div>
          <span className="text-[11px] text-muted-foreground/50 tabular-nums">
            {courseProgress.completed}/{courseProgress.total}
          </span>
        </div>
      )}

      {!collapsed && (
      <div className="shrink-0 px-2 pb-1 flex items-center gap-1.5">
        <button
          ref={searchTriggerRef}
          onClick={() => setSearchOpen(true)}
          aria-label="Search pages and sources"
          className="flex items-center gap-2 flex-1 px-2.5 h-8 text-xs text-muted-foreground/50 hover:text-muted-foreground border border-border hover:bg-sidebar-accent rounded-md transition-colors cursor-pointer"
        >
          <SearchIcon className="size-3" />
          <span className="flex-1 text-left">Search</span>
          <kbd className="text-[10px] text-muted-foreground/30 bg-muted px-1 rounded">{isMac ? '⌘K' : 'Ctrl+K'}</kbd>
        </button>
        {!courseMode && (
          <button
            onClick={onGraphToggle}
            className={cn(
              'flex items-center justify-center size-8 shrink-0 border rounded-md transition-colors cursor-pointer',
              graphViewActive
                ? 'bg-sidebar-accent text-foreground border-border'
                : 'text-muted-foreground/50 hover:text-muted-foreground border-border hover:bg-sidebar-accent',
            )}
            title="Knowledge graph"
          >
            <Network className="size-3" />
          </button>
        )}
      </div>
      )}

      <CommandDialog open={searchOpen} onOpenChange={setSearchOpen} onCloseAutoFocus={restoreSearchFocus}>
        <CommandInput
          placeholder="Search pages, sources, and document contents..."
          aria-label="Search pages, sources, and document contents"
          onValueChange={setContentQuery}
        />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          {allSearchableItems.some((i) => i.type === 'wiki') && (
            <CommandGroup heading="Wiki">
              {allSearchableItems.filter((i) => i.type === 'wiki').map((item) => (
                <CommandItem
                  key={`wiki-${item.path}`}
                  value={item.keywords}
                  onSelect={() => {
                    setSearchOpen(false)
                    if (item.path) onWikiNavigate(item.path, item.docNumber)
                  }}
                  className="flex items-center"
                >
                  <FileText className="size-3.5 mr-2 opacity-50 shrink-0" />
                  <span className="truncate">{highlightTerms(toDisplayTitle(item.title), paletteTerms)}</span>
                  {item.tags.length > 0 && (
                    <span className="ml-auto flex items-center gap-1 shrink-0 pl-2">
                      {item.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="text-[10px] text-muted-foreground/50 bg-muted px-1.5 py-0.5 rounded">
                          {tag}
                        </span>
                      ))}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {allSearchableItems.some((i) => i.type === 'source') && (
            <CommandGroup heading="Sources">
              {allSearchableItems.filter((i) => i.type === 'source').map((item) => (
                <CommandItem
                  key={`source-${item.doc?.id}`}
                  value={item.keywords}
                  onSelect={() => {
                    setSearchOpen(false)
                    if (item.doc) onOpenSourceDoc(item.doc.id)
                  }}
                  className="flex items-center"
                >
                  <NotepadText className="size-3.5 mr-2 opacity-50 shrink-0" />
                  <span className="truncate">{highlightTerms(item.title, paletteTerms)}</span>
                  {item.tags.length > 0 && (
                    <span className="ml-auto flex items-center gap-1 shrink-0 pl-2">
                      {item.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="text-[10px] text-muted-foreground/50 bg-muted px-1.5 py-0.5 rounded">
                          {tag}
                        </span>
                      ))}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(searchingContent || contentHits.length > 0) && (
            <CommandGroup heading="In document contents">
              {contentHits.map((hit) => (
                <CommandItem
                  // cmdk filters on `value`; include the query so a snippet-only match survives.
                  key={`content-${hit.document_id}`}
                  value={`${contentQuery} ${hit.title} ${hit.snippet}`}
                  onSelect={() => {
                    setSearchOpen(false)
                    onOpenSourceDoc(hit.document_id)
                  }}
                  className="flex flex-col items-start gap-0.5"
                >
                  <span className="flex w-full items-center gap-2">
                    <SearchIcon className="size-3.5 mr-2 opacity-50 shrink-0" />
                    <span className="truncate">{highlightTerms(hit.title, paletteTerms)}</span>
                    {hit.page ? (
                      <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/60">p.{hit.page}</span>
                    ) : null}
                  </span>
                  <span className="line-clamp-2 pl-6 text-[11px] leading-relaxed text-muted-foreground/70">
                    {highlightTerms(hit.snippet, paletteTerms)}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandSeparator />
          <CommandGroup heading="Actions">
            {!courseMode && onRecentSelect && (
              <CommandItem onSelect={() => { setSearchOpen(false); onRecentSelect() }}>
                <History className="size-3.5 mr-2 opacity-50" />
                Recent changes
              </CommandItem>
            )}
            {!isLocal && (
              <CommandItem onSelect={() => { setSearchOpen(false); openMcpConnectionDock() }}>
                <Plug className="size-3.5 mr-2 opacity-50" />
                Connect AI
              </CommandItem>
            )}
            <CommandItem onSelect={() => { setSearchOpen(false); onOpenFiles() }}>
              <Folder className="size-3.5 mr-2 opacity-50" />
              Browse files
            </CommandItem>
            <CommandItem onSelect={() => { setSearchOpen(false); onUpload() }}>
              <Upload className="size-3.5 mr-2 opacity-50" />
              Upload files
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>

      {!collapsed && !courseMode && onRecentSelect && (
        <div className="shrink-0 px-2 pb-1 pt-1">
          <button
            type="button"
            onClick={onRecentSelect}
            aria-current={recentActive ? 'page' : undefined}
            className={cn(
              'flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
              recentActive
                ? 'bg-sidebar-accent font-medium text-foreground'
                : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
            )}
          >
            <History className="size-3 shrink-0 opacity-60" />
            <span className="truncate">Recent</span>
          </button>
        </div>
      )}

      {!collapsed && (
      <>
      <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar px-2 pt-1.5">
        {loading ? (
          <SidenavSkeleton lines={3} />
        ) : (
          <>
            {overviewNode && (
              <button
                type="button"
                onClick={() => onWikiNavigate(overviewNode.path!, overviewNode.docNumber)}
                className={cn(
                  'flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                  wikiActivePath === overviewNode.path
                    ? 'bg-sidebar-accent font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                )}
              >
                <BookOpen className="size-3 shrink-0 opacity-60" />
                <span className="truncate">Overview</span>
              </button>
            )}
            <KBSidebarTree
              activePath={wikiActivePath}
              activeFolderPath={activeFolderPath}
              onOpenDocument={(path, docNumber) => onWikiNavigate(path, docNumber)}
              onOpenFiles={onOpenFiles}
              onOpenFolder={onOpenFolder}
            />
            {!hasWiki && (
              <div className="px-2 py-3 text-center text-[11px] text-muted-foreground/60">
                还没有编译出的知识页
              </div>
            )}
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-border p-2">
        <SidenavUserMenu />
      </div>
      </>
      )}
    </div>
  )
}

function AllWikisLink({ className }: { className: string }) {
  return (
    <Link
      href="/wikis"
      title="All wikis"
      aria-label="All wikis"
      className={cn(
        'flex items-center justify-center rounded-md text-muted-foreground/50 hover:text-muted-foreground hover:bg-sidebar-accent transition-colors',
        className,
      )}
    >
      <ChevronLeft className="size-3.5" />
    </Link>
  )
}

function SidenavSkeleton({ lines }: { lines: number }) {
  return (
    <div className="space-y-1 px-2 py-1">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-5 rounded-md bg-muted/50 animate-pulse"
          style={{ width: `${60 + ((i * 13) % 3) * 10}%` }}
        />
      ))}
    </div>
  )
}

function wikiNodeIcon(node: WikiNode, depth: number) {
  const slug = node.path?.replace(/\.(md|txt|json)$/, '').split('/')[0] ?? ''
  const titleLower = node.title.toLowerCase()

  if (slug === 'overview' || (depth === 0 && titleLower === 'overview'))
    return <BookOpen className="size-3 shrink-0 opacity-60" />
  if (slug === 'log' || (depth === 0 && titleLower === 'log'))
    return <ScrollText className="size-3 shrink-0 opacity-60" />
  if (slug === 'plan' || (depth === 0 && titleLower === 'plan'))
    return <ListChecks className="size-3 shrink-0 opacity-60" />
  if (slug === 'concepts' || (depth === 0 && titleLower === 'concepts'))
    return <Lightbulb className="size-3 shrink-0 opacity-60" />
  if (slug === 'entities' || (depth === 0 && titleLower === 'entities'))
    return <Box className="size-3 shrink-0 opacity-60" />

  if (depth > 0)
    return <FileText className="size-3 shrink-0 opacity-40" />

  return <FileText className="size-3 shrink-0 opacity-50" />
}

// `current` beats `locked`: the page you're on never shows a lock.
function CourseGlyph({ node, current }: { node: WikiNode; current: boolean }) {
  if (node.status === 'complete') return <Check className="size-3.5 text-emerald-500" />
  if (current) return <span className="size-1.5 rounded-full bg-foreground" />
  if (node.locked) return <Lock className="size-3 text-muted-foreground/40" />
  return <span className="size-2.5 rounded-full border border-border" />
}

function WikiTreeNode({
  node,
  depth,
  activePath,
  onNavigate,
  courseMode = false,
  currentPath = null,
}: {
  node: WikiNode
  depth: number
  activePath: string | null
  onNavigate: (path: string, docNumber?: number | null) => void
  courseMode?: boolean
  currentPath?: string | null
}) {
  const hasChildren = node.children && node.children.length > 0
  const isActive = node.path != null && node.path === activePath
  const hasActiveChild = hasChildren && node.children!.some((c) => c.path === activePath)
  const locked = courseMode && !!node.locked && !isActive
  const [expanded, setExpanded] = React.useState(false)

  React.useEffect(() => {
    if (hasActiveChild) setExpanded(true)
  }, [hasActiveChild])

  return (
    <div>
      <div
        className={cn(
          'flex items-center gap-1.5 w-full text-left text-[13px] rounded-md px-2 py-1.5 transition-colors cursor-pointer',
          locked
            ? 'text-muted-foreground/40 hover:text-muted-foreground'
            : isActive
              ? 'bg-sidebar-accent text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent/60',
        )}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        title={locked ? 'Ahead of your current lesson' : undefined}
        onClick={() => {
          if (hasChildren) setExpanded((prev) => !prev)
          else if (node.path) onNavigate(node.path, node.docNumber)
        }}
      >
        {hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded((prev) => !prev) }}
            className="p-0.5 -ml-0.5 cursor-pointer"
          >
            <ChevronRight
              className={cn(
                'size-2.5 transition-transform duration-150',
                expanded && 'rotate-90',
              )}
            />
          </button>
        ) : (
          <span className="w-3.5" />
        )}
        {courseMode && node.path
          ? <span className="grid place-items-center size-4 shrink-0"><CourseGlyph node={node} current={node.path === currentPath || isActive} /></span>
          : hasChildren
            ? (expanded
                ? <FolderOpen className="size-3 shrink-0 opacity-60" />
                : <Folder className="size-3 shrink-0 opacity-60" />)
            : wikiNodeIcon(node, depth)}
        <span className="truncate flex-1 min-w-0">{toDisplayTitle(node.title)}</span>
      </div>
      <AnimatePresence initial={false}>
        {hasChildren && expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15, ease: [0.25, 0.1, 0.25, 1] }}
            style={{ overflow: 'hidden' }}
            className=""
          >
            {node.children!.map((child, i) => (
              <WikiTreeNode
                key={child.path ?? child.title ?? i}
                node={child}
                depth={depth + 1}
                activePath={activePath}
                onNavigate={onNavigate}
                courseMode={courseMode}
                currentPath={currentPath}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
