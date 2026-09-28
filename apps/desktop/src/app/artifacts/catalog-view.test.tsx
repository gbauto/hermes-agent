import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createPluginContext } from '@/contrib/plugin'
import { setApiRequestConnection, setApiRequestProfile } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'

import { ArtifactsView } from './index'

const href = 'https://6ab9d13b0b89b5644e56270a--gbautoxyz.netlify.app/weekly-summary'
let dispose: (() => void) | undefined
const openExternal = vi.fn()
const api = vi.fn()

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { api, openExternal } })
  api.mockImplementation(async ({ path }) => {
    if (path.startsWith('/api/profiles/sessions')) {
      return {
        sessions: [{ id: 'native-session', title: 'Real Hermes conversation', profile: 'default', started_at: 1000 }],
        total: 1
      }
    }

    if (path.startsWith('/api/sessions/')) {
      return { messages: [{ role: 'assistant', content: 'Created /tmp/report.txt', timestamp: 1000 }], total: 1 }
    }

    if (path === '/api/plugins/catalog/links') {
      return {
        items: [{ id: 'weekly', label: 'Weekly review', href, attribution: 'Codex · Netlify', timestamp: 2000 }]
      }
    }

    throw new Error('Unexpected test API path: ' + path)
  })
  const ctx = createPluginContext('catalog')
  dispose = ctx.register({
    id: 'links',
    area: 'artifacts.sources',
    title: 'Published reports',
    data: { version: 1, load: () => ctx.rest('/links') }
  })
})
afterEach(() => {
  cleanup()
  dispose?.()
  $connection.set(null)
  $activeGatewayProfile.set('default')
  setApiRequestConnection(null)
  setApiRequestProfile(null)
  Reflect.deleteProperty(window, 'hermesDesktop')
  Reflect.deleteProperty(Element.prototype, 'scrollIntoView')
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it('renders catalog reports in the native page, opens the URL and filters without a fake chat', async () => {
  render(
    <MemoryRouter>
      <ArtifactsView />
    </MemoryRouter>
  )
  const report = await screen.findByRole('link', { name: 'Weekly review' })
  expect(report.getAttribute('href')).toBe(href)
  const row = report.closest('tr')!
  expect(within(row).getByText('Codex · Netlify')).toBeTruthy()
  expect(within(row).queryByRole('button', { name: /Codex/ })).toBeNull()
  expect(await screen.findByText('Real Hermes conversation')).toBeTruthy()
  fireEvent.click(report)
  expect(openExternal).toHaveBeenCalledWith(href)
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Source' }), { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('option', { name: 'Published reports' }))
  expect(screen.queryByText('Real Hermes conversation')).toBeNull()
  expect(await screen.findByRole('link', { name: 'Weekly review' })).toBeTruthy()
  act(() => {
    dispose?.()
    dispose = undefined
  })
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Weekly review' })).toBeNull())
  expect(await screen.findByText('Real Hermes conversation')).toBeTruthy()
})

it('shows catalog failure and supports retry without exposing backend errors', async () => {
  api.mockImplementation(async ({ path }) => {
    if (path.startsWith('/api/profiles/sessions')) {
      return { sessions: [], total: 0 }
    }

    throw new Error('private backend error')
  })
  render(
    <MemoryRouter>
      <ArtifactsView />
    </MemoryRouter>
  )
  expect(await screen.findByText('Unavailable: Published reports. Refresh to retry.')).toBeTruthy()
  expect(screen.queryByText('private backend error')).toBeNull()
  api
    .mockResolvedValueOnce({ sessions: [], total: 0 })
    .mockResolvedValueOnce({
      items: [{ id: 'weekly', label: 'Recovered report', href, attribution: 'Codex', timestamp: 2000 }]
    })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh artifacts' }))
  expect(await screen.findByRole('link', { name: 'Recovered report' })).toBeTruthy()
})
