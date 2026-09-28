import { afterEach, expect, it, vi } from 'vitest'

import { pluginRest, setApiRequestConnection, setApiRequestProfile } from './hermes'

afterEach(() => {
  setApiRequestConnection(null)
  setApiRequestProfile(null)
  vi.unstubAllGlobals()
})

it.each([null, 'remote-work'])(
  'routes plugin catalog reads through their active connection %s and profile',
  async connection => {
    const api = vi.fn().mockResolvedValue({ items: [] })
    vi.stubGlobal('window', { hermesDesktop: { api } })
    setApiRequestConnection(connection)
    setApiRequestProfile('expert')
    await pluginRest('catalog', '/catalog', { timeoutMs: 8000 })
    expect(api).toHaveBeenCalledWith({
      path: '/api/plugins/catalog/catalog',
      timeoutMs: 8000,
      profile: 'expert',
      ...(connection ? { connectionId: connection } : {})
    })
  }
)
