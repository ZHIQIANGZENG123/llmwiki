import { describe, expect, it } from 'vitest'
import {
  buildCodexMcpConfig,
  buildStarterPrompt,
  defaultMcpUrl,
  resolveMcpUrl,
} from './mcp'

describe('MCP connection helpers', () => {
  it('builds a Codex Streamable HTTP OAuth configuration', () => {
    expect(buildCodexMcpConfig('http://localhost:3000/mcp', 'oauth')).toBe([
      '[mcp_servers.llmwiki]',
      'url = "http://localhost:3000/mcp"',
      'auth = "oauth"',
    ].join('\n'))
  })

  it('falls back to the hosted default URL outside local mode', () => {
    expect(defaultMcpUrl()).toBe('http://localhost:8080/mcp')
    expect(resolveMcpUrl()).toBe('http://localhost:8080/mcp')
  })

  it('builds a source-optional starter prompt for a named wiki', () => {
    const prompt = buildStarterPrompt('Systems Course')

    expect(prompt).toContain('"Systems Course"')
    expect(prompt).toContain('about [topic]')
    expect(prompt).toContain('otherwise research with your available tools')
  })

  it('uses a generic target when no wiki name is available', () => {
    expect(buildStarterPrompt()).toContain('my LLM Wiki')
  })
})
