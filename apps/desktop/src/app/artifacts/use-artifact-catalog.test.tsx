import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getAllSessionMessages, listAllProfileSessions } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'

import { type ArtifactSource } from './artifact-sources'
import { useArtifactCatalog } from './use-artifact-catalog'

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listAllProfileSessions: vi.fn(),
  getAllSessionMessages: vi.fn()
}))

const link = (label: string) => ({
  items: [{ id: 'report', href: 'https://reports.example/weekly', label, attribution: 'Codex', timestamp: 1000 }]
})

beforeEach(() => {
  vi.mocked(listAllProfileSessions).mockResolvedValue({ sessions: [], limit: 30, offset: 0, total: 0 })
})
afterEach(() => {
  cleanup()
  $connection.set(null)
  $activeGatewayProfile.set('default')
  vi.clearAllMocks()
})

it.each(['profile', 'connection'] as const)('discards a late catalog response after a %s switch', async field => {
  let oldRead!: (value: unknown) => void

  const sources: ArtifactSource[] = [
    {
      id: 'catalog',
      title: 'Catalog',
      load: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise(resolve => {
              oldRead = resolve
            })
        )
        .mockResolvedValue(link('New context'))
    }
  ]

  const { result } = renderHook(() => useArtifactCatalog(sources))
  await waitFor(() => expect(oldRead).toBeDefined())
  act(() => {
    if (field === 'profile') {
      $activeGatewayProfile.set('other')
    } else {
      $connection.set({ connectionId: 'other', mode: 'remote' } as never)
    }
  })
  await waitFor(() => expect(result.current.artifacts?.[0]?.label).toBe('New context'))
  await act(async () => oldRead(link('Old context')))
  expect(result.current.artifacts?.map(item => item.label)).toEqual(['New context'])
})

it('removes catalog entries when a provider is disabled, while retaining native artifacts', async () => {
  vi.mocked(listAllProfileSessions).mockResolvedValue({
    sessions: [{ id: 'real-session', title: 'Actual conversation', profile: 'default', started_at: 1000 }] as never,
    total: 1,
    offset: 0,
    limit: 30
  })
  vi.mocked(getAllSessionMessages).mockResolvedValue({
    messages: [{ role: 'assistant', content: 'https://example.com/native', timestamp: 2000 }]
  } as never)
  const sources: ArtifactSource[] = [{ id: 'catalog', title: 'Catalog', load: async () => link('External report') }]
  const { result, rerender } = renderHook(({ sources }) => useArtifactCatalog(sources), { initialProps: { sources } })
  await waitFor(() => expect(result.current.artifacts).toHaveLength(2))
  rerender({ sources: [] })
  expect(result.current.artifacts).toBeNull()
  await waitFor(() => expect(result.current.artifacts).toHaveLength(1))
  expect(result.current.artifacts?.[0]?.sessionId).toBe('real-session')
})
