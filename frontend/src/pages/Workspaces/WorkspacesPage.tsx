import {
  Plus,
  Search,
} from "lucide-react"
import { useCallback, useMemo, useState } from "react"

import { CreateWorkspaceDialog } from "../../features/workspaces/components/CreateWorkspaceDialog"
import { listWorkspaces, createWorkspace } from "../../services/api/workspaces"
import { useApiResource } from "../../services/api/useApiResource"
import { ApiFeedback } from "../../components/ui/ApiFeedback"

import { Button } from "../../components/ui/Button"
import { Input } from "../../components/ui/Input"
import { PageHeader } from "../../components/ui/PageHeader"
import { WorkspaceCard } from "../../features/workspaces/components/WorkspaceCard"


export function WorkspacesPage() {
  const [search, setSearch] =
    useState("")
  const load = useCallback((signal: AbortSignal) => listWorkspaces(signal), [])
  const resource = useApiResource("workspaces", load)
  const workspaces = useMemo(() => resource.data ?? [], [resource.data])

    const [createOpen, setCreateOpen] = useState(false)

  const filteredWorkspaces =
    useMemo(() => {
      const query =
        search.trim().toLowerCase()

      if (!query) {
        return workspaces
      }

      return workspaces.filter(
        (workspace) =>
          workspace.name
            .toLowerCase()
            .includes(query) ||
          workspace.description
            .toLowerCase()
            .includes(query),
      )
    }, [search,workspaces])
  async function handleCreateWorkspace(input: { name: string; description: string }) {
    await createWorkspace(input)
    resource.retry()
  }
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} />

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Workspaces"
        description="Organize datasets, processing, and analytics by business area or use case."
        action={
          <Button
            onClick={() => setCreateOpen(true)}
            >
            <Plus size={17} />
            New Workspace
        </Button>
        }
      />

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="relative max-w-xl">
          <Search
            size={17}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
          />

          <Input
            value={search}
            onChange={(event) =>
              setSearch(
                event.target.value,
              )
            }
            placeholder="Search workspaces..."
            className="pl-9"
          />
        </div>
      </div>

      {filteredWorkspaces.length > 0 ? (
        <section className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {filteredWorkspaces.map(
            (workspace) => (
              <WorkspaceCard
                key={workspace.id}
                workspace={workspace}
              />
            ),
          )}
        </section>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white p-12 text-center">
          <h2 className="text-base font-semibold text-slate-900">
            No workspaces found
          </h2>

          <p className="mt-2 text-sm text-slate-500">
            Try a different search.
          </p>
        </div>
      )}
      <CreateWorkspaceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={handleCreateWorkspace}
        />
    </div>
  )
}