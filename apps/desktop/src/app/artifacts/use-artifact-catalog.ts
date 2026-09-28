import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { getAllSessionMessages, getApiRequestConnection, getApiRequestProfile, listAllProfileSessions } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'

import { type ArtifactSource, loadArtifactSources } from './artifact-sources'
import { type ArtifactRecord, loadArtifactsForSessions } from './artifact-utils'

interface CatalogState {
  connection: ReturnType<typeof $connection.get>
  profile: string
  sources: readonly ArtifactSource[]
  artifacts: ArtifactRecord[] | null
  unavailable: string[]
  partial: string[]
  sessionFailures: number
  refreshing: boolean
}

// State is window-local and never persisted. A connection/profile change or
// plugin unload hides the old snapshot immediately, before the next effect.
export function useArtifactCatalog(sources: readonly ArtifactSource[]) {
  const connection = useStore($connection)
  const profile = useStore($activeGatewayProfile)
  const [state, setState] = useState<CatalogState | null>(null)
  const pending = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    const routeConnection = getApiRequestConnection()
    const routeProfile = getApiRequestProfile()

    const current = () =>
      !controller.signal.aborted &&
      $connection.get() === connection &&
      $activeGatewayProfile.get() === profile &&
      getApiRequestConnection() === routeConnection &&
      getApiRequestProfile() === routeProfile

    const empty = { connection, profile, sources, artifacts: null, unavailable: [], partial: [], sessionFailures: 0 }

    setState(previous => ({
      ...(previous?.connection === connection && previous.profile === profile && previous.sources === sources
        ? previous
        : empty),
      refreshing: true
    }))

    const sessions = async () => {
      try {
        const page = await listAllProfileSessions(30, 1)

        const result = await loadArtifactsForSessions(page.sessions, async session => {
          if (!current()) {
            throw new Error('Artifact context changed')
          }

          return (await getAllSessionMessages(session.id, session.profile)).messages
        })

        return { artifacts: result.artifacts, failures: result.failures.length }
      } catch {
        return { artifacts: [], failures: 1 }
      }
    }

    const [native, external] = await Promise.all([sessions(), loadArtifactSources(sources, controller.signal)])

    if (current()) {
      setState({
        connection,
        profile,
        sources,
        artifacts: [...native.artifacts, ...external.artifacts].sort((left, right) => right.timestamp - left.timestamp),
        unavailable: external.unavailable,
        partial: external.partial,
        sessionFailures: native.failures,
        refreshing: false
      })
    }
  }, [connection, profile, sources])

  useEffect(() => {
    void refresh()

    return () => pending.current?.abort()
  }, [refresh])

  const visible = state?.connection === connection && state.profile === profile && state.sources === sources

  return {
    artifacts: visible ? state.artifacts : null,
    unavailable: visible ? state.unavailable : [],
    partial: visible ? state.partial : [],
    sessionFailures: visible ? state.sessionFailures : 0,
    refreshing: visible ? state.refreshing : true,
    refresh
  }
}
