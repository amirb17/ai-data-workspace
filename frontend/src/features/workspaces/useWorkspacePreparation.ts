import { useCallback } from "react"
import { datasetProcessing, type DatasetProcessing } from "../../services/api/rules"
import { getAnalyticsReadiness, type DatasetAnalytics } from "../../services/api/analytics"
import { getWorkspaceAnalytics, type WorkspaceAnalytics } from "../../services/api/workspaceAnalytics"
import { ApiError } from "../../services/api/client"
import { getProfileReadiness, type DatasetProfile } from "../../services/api/profile"
import { getUnderstandingReadiness, type Understanding } from "../../services/api/understanding"
import { useApiResource } from "../../services/api/useApiResource"
import type { DatasetListItem } from "../datasets/types"

export type DatasetPreparation = { dataset: DatasetListItem; processing: DatasetProcessing; analytics: DatasetAnalytics; profile: DatasetProfile | null; understanding: Understanding | null }

export function useWorkspacePreparation(workspace: string, datasets: DatasetListItem[]) {
  const identity = datasets.filter(dataset => dataset.status !== "ARCHIVED").map(dataset => dataset.id).join(",")
  const load = useCallback(async (signal: AbortSignal) => {
    const active = datasets.filter(dataset => dataset.status !== "ARCHIVED")
    const rows: DatasetPreparation[] = []
    // Bound metadata concurrency; each read retains its existing ownership checks.
    for (let start = 0; start < active.length; start += 4) {
      rows.push(...await Promise.all(active.slice(start, start + 4).map(async dataset => {
        const [processing, analytics] = await Promise.all([datasetProcessing(workspace, String(dataset.id), false, signal), getAnalyticsReadiness(workspace, String(dataset.id), signal)])
        const profile = analytics.current_state_id === null ? null : await getProfileReadiness(workspace, String(dataset.id), signal)
        const understanding = profile?.profile_ready ? await getUnderstandingReadiness(workspace, String(dataset.id), signal) : null
        return { dataset, processing, analytics, profile, understanding }
      })))
    }
    // An empty workspace has no derived analytics. Don't call an intentionally
    // blocked public analytics endpoint just to display the no-data state.
    if (active.length === 0) {
      const analytics: WorkspaceAnalytics = {
        workspace_id: Number(workspace), status: "NO_DATA", readiness: "NO_DATA",
        metrics_total: 0, metrics_available: 0, metrics_fresh: 0,
        metrics_stale: 0, metrics_failed: 0, metrics_blocked: 0,
        last_refreshed: null, can_refresh: false, metrics: [],
        summary: {trusted_datasets: 0, available_metrics: 0, current_metrics: 0,
          needs_attention: 0, confirmed_relationships: 0},
      }
      return {rows, analytics}
    }
    // Workspace-wide analytics is unavailable in this public release.
    // Keep it unknown rather than hiding authorized dataset metadata.
    let analytics: WorkspaceAnalytics | null
    try {
      analytics = await getWorkspaceAnalytics(workspace, signal)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 403) throw error
      analytics = null
    }
    return { rows, analytics }
  }, [workspace, datasets])
  return useApiResource(`preparation:${workspace}:${identity}`, load)
}

export function preparationAction(workspace: string, row: DatasetPreparation) {
  const base = `/app/workspaces/${workspace}/datasets/${row.dataset.id}`
  if (!row.processing.summary.total) return { label: "Add delivery", to: `${base}/files` }
  if (row.processing.summary.awaiting_rules) return { label: "Review rules", to: `${base}/rules` }
  if (row.processing.deliveries.some(delivery => delivery.context.rule_state === "FINALIZED" && !delivery.context.load_strategy && delivery.context.stages.silver === "PENDING")) return { label: "Configure load policy", to: `${base}/contract` }
  if (row.processing.summary.pending || row.processing.summary.processing || row.processing.summary.failed) return { label: "View processing", to: `${base}/processing` }
  if (row.analytics.current_state_id === null) return { label: "Configure load policy", to: `${base}/contract` }
  if (!row.analytics.analytics_ready) return { label: "Refresh analytics", to: `${base}/analytics` }
  if (!row.profile?.profile_ready) return { label: "Prepare data profile", to: `${base}/profile` }
  if (row.understanding?.status !== "READY") return { label: "Review description", to: `${base}/understanding` }
  return { label: "Explore dataset", to: base }
}
