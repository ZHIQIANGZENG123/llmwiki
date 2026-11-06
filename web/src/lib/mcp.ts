// The MCP server is its own deployment — its URL is never derivable from the API URL.
// Local workspaces proxy /mcp through the web app, so the URL is resolved at runtime
// from the origin the user actually opened (loopback or a LAN address).
import { isLocalMode } from './apiUrl'

const HOSTED_DEFAULT_MCP_URL = 'http://localhost:8080/mcp'
const LOCAL_MCP_PATH = '/mcp'

/** Value safe to render on the server and during hydration. */
export function defaultMcpUrl(): string {
  const configured = process.env.NEXT_PUBLIC_MCP_URL
  if (configured) return configured
  return isLocalMode ? LOCAL_MCP_PATH : HOSTED_DEFAULT_MCP_URL
}

/** Resolve the live MCP endpoint; only call from the browser. */
export function resolveMcpUrl(): string {
  const configured = process.env.NEXT_PUBLIC_MCP_URL
  if (configured) return configured
  if (isLocalMode) {
    if (typeof window === 'undefined') return LOCAL_MCP_PATH
    return `${window.location.origin}${LOCAL_MCP_PATH}`
  }
  return HOSTED_DEFAULT_MCP_URL
}

export function buildCodexMcpConfig(
  mcpUrl: string = resolveMcpUrl(),
  auth: 'oauth' | 'none' = isLocalMode ? 'none' : 'oauth',
): string {
  return [
    '[mcp_servers.llmwiki]',
    `url = "${mcpUrl}"`,
    `auth = "${auth}"`,
  ].join('\n')
}

export function buildStarterPrompt(wikiName?: string): string {
  const target = wikiName?.trim() ? ` "${wikiName.trim()}"` : ' my LLM Wiki'
  return [
    'Call the LLM Wiki guide first.',
    `Then create or update${target} about [topic].`,
    'Use my existing sources when relevant; otherwise research with your available tools.',
    'Build a clear structure I can read, annotate, and refine.',
  ].join(' ')
}
