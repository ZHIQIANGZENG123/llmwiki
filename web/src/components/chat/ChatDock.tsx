'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useRouter } from 'next/navigation'
import { ChevronDown, Loader2, MessageSquarePlus, Send, Sparkles, Trash2, Wrench, X } from 'lucide-react'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { newId } from '@/lib/ids'
import { apiUrl, isLocalMode } from '@/lib/apiUrl'
import { cn } from '@/lib/utils'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  failed?: boolean
}

interface ChatThread {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
}

const THREADS_KEY = 'llmwiki-chat-threads'
const LEGACY_KEY = 'llmwiki-chat-thread'
const MAX_THREADS = 50

function makeThread(messages: ChatMessage[] = []): ChatThread {
  const now = Date.now()
  const firstUser = messages.find((m) => m.role === 'user')
  return {
    id: newId(),
    title: firstUser ? firstUser.content.slice(0, 24) : '',
    createdAt: now,
    updatedAt: now,
    messages,
  }
}

function formatWhen(timestamp: number): string {
  const date = new Date(timestamp)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

interface SseFrame {
  event: string
  data: Record<string, unknown>
}

/** Split an SSE byte stream into frames; returns the leftover partial frame. */
function readFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  const frames: SseFrame[] = []
  for (const part of parts) {
    let event = 'message'
    let raw = ''
    for (const line of part.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) raw += line.slice(5).trim()
    }
    if (!raw) continue
    try {
      frames.push({ event, data: JSON.parse(raw) as Record<string, unknown> })
    } catch {
      // A partial or non-JSON frame is not fatal; drop it and keep streaming.
    }
  }
  return { frames, rest }
}

export function ChatDock() {
  const router = useRouter()
  const [open, setOpen] = React.useState(false)
  const [threads, setThreads] = React.useState<ChatThread[]>([])
  const [activeId, setActiveId] = React.useState('')
  const [loaded, setLoaded] = React.useState(false)
  const [input, setInput] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [activity, setActivity] = React.useState<string | null>(null)
  const listRef = React.useRef<HTMLDivElement>(null)
  const inputRef = React.useRef<HTMLTextAreaElement>(null)

  const activeThread = threads.find((thread) => thread.id === activeId) ?? null
  const messages = activeThread?.messages ?? []

  // The streaming loop captures callbacks; read the active id through a ref so a thread
  // switch mid-answer cannot write into the wrong conversation.
  const activeIdRef = React.useRef(activeId)
  activeIdRef.current = activeId

  React.useEffect(() => {
    let restored: { threads: ChatThread[]; activeId: string } | null = null
    try {
      const raw = localStorage.getItem(THREADS_KEY)
      if (raw) restored = JSON.parse(raw) as { threads: ChatThread[]; activeId: string }
    } catch {
      restored = null
    }

    // Carry a conversation started before thread history existed into the new store.
    if (!restored?.threads?.length) {
      try {
        const legacy = sessionStorage.getItem(LEGACY_KEY)
        const old = legacy ? (JSON.parse(legacy) as ChatMessage[]) : []
        if (old.length > 0) {
          const thread = makeThread(old)
          restored = { threads: [thread], activeId: thread.id }
          sessionStorage.removeItem(LEGACY_KEY)
        }
      } catch {
        // Ignore an unreadable legacy entry.
      }
    }

    if (restored?.threads?.length) {
      setThreads(restored.threads)
      setActiveId(restored.activeId || restored.threads[0].id)
    } else {
      const thread = makeThread()
      setThreads([thread])
      setActiveId(thread.id)
    }
    setLoaded(true)
  }, [])

  React.useEffect(() => {
    if (!loaded) return
    try {
      localStorage.setItem(
        THREADS_KEY,
        JSON.stringify({ threads: threads.slice(0, MAX_THREADS), activeId }),
      )
    } catch {
      // Storage is a convenience here, never a requirement.
    }
  }, [threads, activeId, loaded])

  /** Rewrite the active thread's messages; keeps the existing setMessages call sites. */
  const setMessages = React.useCallback(
    (updater: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
      setThreads((prev) => prev.map((thread) => {
        if (thread.id !== activeIdRef.current) return thread
        const nextMessages = typeof updater === 'function' ? updater(thread.messages) : updater
        return { ...thread, messages: nextMessages, updatedAt: Date.now() }
      }))
    },
    [],
  )

  React.useEffect(() => {
    const node = listRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, activity, open])

  React.useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120)
  }, [open])

  const appendDelta = React.useCallback((text: string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const next = [...prev]
      const last = next[next.length - 1]
      if (last.role !== 'assistant') return prev
      next[next.length - 1] = { ...last, content: last.content + text }
      return next
    })
  }, [])

  const send = React.useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return
    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    setThreads((prev) => prev.map((thread) => (
      thread.id === activeIdRef.current && !thread.title
        ? { ...thread, title: text.slice(0, 24) }
        : thread
    )))
    setBusy(true)
    setActivity('thinking')

    try {
      const response = await fetch(apiUrl('/v1/chat/stream'), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
      })
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parsed = readFrames(buffer)
        buffer = parsed.rest
        for (const frame of parsed.frames) {
          if (frame.event === 'delta') {
            setActivity(null)
            const chunk = frame.data.text
            if (typeof chunk === 'string') appendDelta(chunk)
          } else if (frame.event === 'tool') {
            const name = String(frame.data.name || 'tool')
            setActivity(name)
          } else if (frame.event === 'error') {
            const message = String(frame.data.message || 'The agent stopped')
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: last.content || message, failed: true }
              return next
            })
          }
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Request failed'
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          next[next.length - 1] = { ...last, content: last.content || `无法连接助手：${message}`, failed: true }
        }
        return next
      })
    } finally {
      setBusy(false)
      setActivity(null)
    }
  }, [appendDelta, busy, input, messages])

  const startNewThread = React.useCallback(() => {
    const thread = makeThread()
    setThreads((prev) => [thread, ...prev].slice(0, MAX_THREADS))
    setActiveId(thread.id)
    setActivity(null)
    inputRef.current?.focus()
  }, [])

  const openThread = React.useCallback((id: string) => {
    setActiveId(id)
    setActivity(null)
  }, [])

  const deleteThread = React.useCallback((id: string) => {
    setThreads((prev) => {
      const remaining = prev.filter((thread) => thread.id !== id)
      if (remaining.length === 0) {
        const fresh = makeThread()
        setActiveId(fresh.id)
        return [fresh]
      }
      if (id === activeIdRef.current) setActiveId(remaining[0].id)
      return remaining
    })
  }, [])

  const clearThreads = React.useCallback(() => {
    const fresh = makeThread()
    setThreads([fresh])
    setActiveId(fresh.id)
    setActivity(null)
  }, [])

  const openInternal = React.useCallback((href: string) => {
    const origin = window.location.origin
    const path = href.startsWith(origin) ? href.slice(origin.length) : href
    setOpen(false)
    router.push(path)
  }, [router])

  if (!isLocalMode) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-40 flex size-11 items-center justify-center rounded-full bg-foreground text-background shadow-lg transition-transform hover:scale-105 cursor-pointer"
        title="Ask your library"
        aria-label="Ask your library"
      >
        <Sparkles className="size-5" />
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-[520px]">
          <SheetHeader className="flex-row items-center justify-between space-y-0 border-b border-border px-4 py-3">
            <div className="flex min-w-0 items-center gap-1">
              <SheetTitle className="truncate text-sm font-medium">
                {activeThread?.title || 'Ask your library'}
              </SheetTitle>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground cursor-pointer"
                    title="历史对话"
                    aria-label="历史对话"
                  >
                    <ChevronDown className="size-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="max-h-80 w-72 overflow-y-auto">
                  {threads.map((thread) => (
                    <DropdownMenuItem
                      key={thread.id}
                      onSelect={() => openThread(thread.id)}
                      className="flex items-center gap-2"
                    >
                      <span className={cn('min-w-0 flex-1 truncate', thread.id === activeId && 'font-medium text-foreground')}>
                        {thread.title || '新对话'}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground/60">{formatWhen(thread.updatedAt)}</span>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          deleteThread(thread.id)
                        }}
                        className="shrink-0 rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-destructive cursor-pointer"
                        aria-label="删除该对话"
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={clearThreads}>清空全部历史</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={startNewThread}
                className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground cursor-pointer"
                title="New chat"
              >
                <MessageSquarePlus className="size-3.5" />
                New
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground cursor-pointer"
                aria-label="Close"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </SheetHeader>

          <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-3">
            {messages.length === 0 && (
              <div className="mt-10 text-center text-xs leading-relaxed text-muted-foreground">
                问点什么。助手会检索你的原始文档和已编译的 wiki 页，并给出带页码的出处。
              </div>
            )}
            <div className="flex flex-col gap-3">
              {messages.map((message, index) => (
                <div key={index} className={cn('flex flex-col', message.role === 'user' ? 'items-end' : 'items-start')}>
                  {message.role === 'user' ? (
                    <div className="max-w-[85%] rounded-lg bg-foreground px-3 py-2 text-xs leading-relaxed text-background whitespace-pre-wrap">
                      {message.content}
                    </div>
                  ) : message.content ? (
                    <div className="prose-chat max-w-full text-xs leading-relaxed text-foreground">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ href, children }) => (
                            <a
                              href={href}
                              onClick={(event) => {
                                if (!href) return
                                if (href.startsWith('/') || href.startsWith(window.location.origin)) {
                                  event.preventDefault()
                                  openInternal(href)
                                }
                              }}
                              className="text-foreground underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground"
                              target={href?.startsWith('http') ? '_blank' : undefined}
                              rel="noopener noreferrer"
                            >
                              {children}
                            </a>
                          ),
                          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                          ul: ({ children }) => <ul className="mb-2 list-disc pl-4">{children}</ul>,
                          ol: ({ children }) => <ol className="mb-2 list-decimal pl-4">{children}</ol>,
                          code: ({ children }) => (
                            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">{children}</code>
                          ),
                          table: ({ children }) => (
                            <div className="my-2 overflow-x-auto">
                              <table className="w-full border-collapse text-[11px]">{children}</table>
                            </div>
                          ),
                          th: ({ children }) => (
                            <th className="border-b border-border bg-muted/50 px-2 py-1 text-left font-medium">{children}</th>
                          ),
                          td: ({ children }) => (
                            <td className="border-b border-border px-2 py-1 text-muted-foreground">{children}</td>
                          ),
                        }}
                      >
                        {message.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" />
                      {activity && activity !== 'thinking' ? `调用 ${activity}…` : '检索中…'}
                    </div>
                  )}
                  {message.failed && (
                    <span className="mt-1 text-[11px] text-destructive">本轮没有完成，可以重试。</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-border p-3">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void send()
                  }
                }}
                rows={2}
                placeholder="问你的知识库…  (Enter 发送，Shift+Enter 换行)"
                className="max-h-32 min-h-[44px] flex-1 resize-none rounded-md border border-border bg-transparent px-2.5 py-2 text-xs leading-relaxed placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-border"
                disabled={busy}
              />
              <button
                type="button"
                onClick={() => void send()}
                disabled={busy || !input.trim()}
                className="flex size-9 shrink-0 items-center justify-center rounded-md bg-foreground text-background transition-opacity disabled:opacity-40 cursor-pointer"
                aria-label="Send"
              >
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
              </button>
            </div>
            <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground/60">
              <Wrench className="size-3" />
              答案来自 MCP 工具检索，未经人工复核
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
