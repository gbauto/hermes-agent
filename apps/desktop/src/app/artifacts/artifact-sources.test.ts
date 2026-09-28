import { afterEach, describe, expect, it, vi } from 'vitest'

import { artifactSources, loadArtifactSources } from './artifact-sources'

const link = {
  id: 'report-v1',
  label: 'Weekly review',
  href: 'https://reports.example/weekly',
  attribution: 'Codex',
  timestamp: 1000
}

const source = (load = async (): Promise<unknown> => ({ items: [link] })) => ({
  id: 'catalog:links',
  title: 'Catalog',
  load
})

afterEach(() => vi.useRealTimers())

describe('native artifact catalog sources', () => {
  it('merges links without inventing a session and namespaces identities', async () => {
    const sources = artifactSources([
      { id: 'catalog:links', area: 'artifacts.sources', title: 'Catalog', data: { version: 1, load: source().load } }
    ])

    const result = await loadArtifactSources(
      [...sources, { ...source(), id: 'other:links' }],
      new AbortController().signal
    )

    expect(result.artifacts).toHaveLength(2)
    expect(new Set(result.artifacts.map(item => item.id)).size).toBe(2)
    expect(result.artifacts[0]).toMatchObject({
      sessionId: null,
      sessionTitle: 'Codex',
      label: link.label,
      href: link.href
    })
  })

  it('rejects executable protocols, local paths, credentials and malformed records but keeps valid links', async () => {
    const bad = [
      'javascript:alert(1)',
      'file:///etc/passwd',
      'http://reports.example/',
      'https://user:pass@example.com/',
      'https://example.com/ bad'
    ]

    const result = await loadArtifactSources(
      [
        source(async () => ({
          items: [link, link, ...bad.map(href => ({ ...link, href })), { ...link, timestamp: NaN }]
        }))
      ],
      new AbortController().signal
    )

    expect(result.artifacts).toHaveLength(1)
    expect(result.partial).toEqual(['Catalog'])
  })

  it('isolates failures and reports explicitly bounded catalog coverage', async () => {
    const result = await loadArtifactSources(
      [
        source(async () => {
          throw new Error('private backend details')
        }),
        { ...source(async () => ({ items: [link], partial: true })), id: 'working', title: 'Working' }
      ],
      new AbortController().signal
    )

    expect(result.unavailable).toEqual(['Catalog'])
    expect(result.partial).toEqual(['Working'])
    expect(result.artifacts).toHaveLength(1)
    expect(JSON.stringify(result)).not.toContain('private backend details')
  })

  it('bounds a hung provider and aborts a pending read', async () => {
    vi.useFakeTimers()
    const hung = source(() => new Promise(() => {}))
    const timed = loadArtifactSources([hung], new AbortController().signal)
    await vi.advanceTimersByTimeAsync(15_000)
    expect((await timed).unavailable).toEqual(['Catalog'])
    const controller = new AbortController()
    const pending = loadArtifactSources([hung], controller.signal)
    controller.abort()
    expect((await pending).artifacts).toEqual([])
  })
})
