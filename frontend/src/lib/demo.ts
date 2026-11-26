import { useEffect, useState } from 'react'
import type { ClipEvents, Manifest, ManifestClip } from '../types'
import { DEMO_BASE } from '../types'

/* Shared loaders for the pre-rendered demo assets in public/demo/.
   This is the events.json contract both pages bind to — do not fork it. */

export function useManifest(): Manifest | null {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  useEffect(() => {
    fetch(`${DEMO_BASE}/manifest.json`)
      .then((r) => r.json())
      .then((m: Manifest) => setManifest(m))
      .catch(() => setManifest({ clips: [] }))
  }, [])
  return manifest
}

export function useClipEvents(clipId: string | null): ClipEvents | null {
  const [clip, setClip] = useState<ClipEvents | null>(null)
  useEffect(() => {
    if (!clipId) { setClip(null); return }
    fetch(`${DEMO_BASE}/${clipId}/events.json`)
      .then((r) => r.json())
      .then((c: ClipEvents) => setClip(c))
      .catch(() => setClip(null))
  }, [clipId])
  return clip
}

/** Rendered (annotated) video URL for a manifest clip. */
export function videoUrl(c: ManifestClip): string {
  return `${DEMO_BASE}/${c.video}`
}

/** Thumbnail URL given a clip id and the relative path stored in an event. */
export function thumbUrl(clipId: string, rel: string | null): string | null {
  return rel ? `${DEMO_BASE}/${clipId}/${rel}` : null
}
