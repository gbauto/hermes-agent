import type { Contribution } from '@/contrib/types'

import type { ArtifactRecord } from './artifact-utils'

// A read-only data contribution to the native Artifacts page. The provider
// owns its catalog and transport; Desktop owns validation and presentation.
export const ARTIFACT_SOURCES_AREA = 'artifacts.sources'

export interface ArtifactLinkSource {
  version: 1
  load: (context: { signal: AbortSignal }) => Promise<unknown>
}

export interface ArtifactSource {
  id: string
  title: string
  load: ArtifactLinkSource['load']
}

export interface ArtifactSourceResult {
  artifacts: ArtifactRecord[]
  unavailable: string[]
  partial: string[]
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function artifactSources(contributions: readonly Contribution[]): ArtifactSource[] {
  return contributions.flatMap(c => {
    const data = c.data

    return c.title && record(data) && data.version === 1 && typeof data.load === 'function'
      ? [{ id: c.id, title: c.title, load: data.load as ArtifactLinkSource['load'] }]
      : []
  })
}

function parseLinks(value: unknown, source: ArtifactSource): { artifacts: ArtifactRecord[]; partial: boolean } {
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 1000) {
    throw new Error('Invalid artifact source response')
  }

  const artifacts = new Map<string, ArtifactRecord>()
  let partial = value.partial === true

  for (const item of value.items) {
    if (
      !record(item) ||
      typeof item.id !== 'string' ||
      !item.id ||
      item.id.length > 400 ||
      typeof item.label !== 'string' ||
      !item.label.trim() ||
      item.label.length > 240 ||
      typeof item.attribution !== 'string' ||
      !item.attribution.trim() ||
      item.attribution.length > 240 ||
      typeof item.href !== 'string' ||
      item.href.length > 2048 ||
      /[\s\\]/.test(item.href) ||
      typeof item.timestamp !== 'number' ||
      !Number.isFinite(item.timestamp) ||
      item.timestamp < 0 ||
      item.timestamp > 8.64e15
    ) {
      partial = true

      continue
    }

    let url: URL

    try {
      url = new URL(item.href)
    } catch {
      partial = true

      continue
    }

    // Catalog providers contribute external links only. They cannot import
    // local paths, privileged protocols, or synthetic native session ids.
    if (url.protocol !== 'https:' || url.username || url.password) {
      partial = true

      continue
    }

    const id = JSON.stringify([source.id, item.id])

    artifacts.set(id, {
      id,
      kind: 'link',
      href: url.href,
      value: url.href,
      label: item.label,
      sessionId: null,
      sessionTitle: item.attribution,
      sourceId: source.id,
      timestamp: item.timestamp
    })
  }

  return { artifacts: [...artifacts.values()], partial }
}

export async function loadArtifactSources(
  sources: readonly ArtifactSource[],
  signal: AbortSignal
): Promise<ArtifactSourceResult> {
  const results = await Promise.all(
    sources.map(async source => {
      const controller = new AbortController()
      const abort = () => controller.abort()
      signal.addEventListener('abort', abort, { once: true })
      let timer: ReturnType<typeof setTimeout> | undefined
      let stop: (() => void) | undefined

      try {
        const cancelled = new Promise<never>((_resolve, reject) => {
          stop = () => reject(new Error('Artifact source read cancelled'))
          controller.signal.addEventListener('abort', stop, { once: true })
          timer = setTimeout(abort, 15_000)

          if (signal.aborted) {
            abort()
          }
        })

        const value = await Promise.race([
          cancelled,
          Promise.resolve().then(() => {
            controller.signal.throwIfAborted()

            return source.load({ signal: controller.signal })
          })
        ])

        return { source, ...parseLinks(value, source), unavailable: false }
      } catch {
        // Provider errors may contain private paths/SQL. Surface the source name
        // and a retry affordance, never the arbitrary backend exception text.
        return { source, artifacts: [], partial: false, unavailable: true }
      } finally {
        clearTimeout(timer)
        signal.removeEventListener('abort', abort)

        if (stop) {
          controller.signal.removeEventListener('abort', stop)
        }
      }
    })
  )

  return {
    artifacts: results.flatMap(result => result.artifacts),
    unavailable: results.filter(result => result.unavailable).map(result => result.source.title),
    partial: results.filter(result => result.partial).map(result => result.source.title)
  }
}
