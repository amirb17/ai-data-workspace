import {
  Plus,
} from "lucide-react"
import {
  useCallback,
  useMemo,
  useState,
} from "react"
import { useOutletContext, useParams } from "react-router-dom"

import { CreateDatasetDialog } from "../../../features/datasets/components/CreateDatasetDialog"
import { listDatasets, createDataset } from "../../../services/api/datasets"
import { useApiResource } from "../../../services/api/useApiResource"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { Button } from "../../../components/ui/Button"
import { DatasetList } from "../../../features/datasets/components/DatasetList"
import {
  DatasetsToolbar,
  type DatasetStatusFilter,
} from "../../../features/datasets/components/DatasetsToolbar"



export function WorkspaceDatasetsPage() {
  const { workspaceId } = useParams()
  return <WorkspaceDatasetsContent key={workspaceId} />
}

function WorkspaceDatasetsContent() {
  const { onDatasetsChanged } = useOutletContext<{ onDatasetsChanged: () => void }>()
  const { workspaceId } =
    useParams()
  const load = useCallback((signal: AbortSignal) => listDatasets(workspaceId ?? "", signal), [workspaceId])
  const resource = useApiResource(`datasets:${workspaceId}`, load)
  const datasets = useMemo(() => resource.data ?? [], [resource.data])
  const [search, setSearch] =
    useState("")

  const [status, setStatus] =
    useState<DatasetStatusFilter>(
      "ALL",
    )


const [createOpen, setCreateOpen] =
  useState(false)


  const filteredDatasets =
    useMemo(() => {
      const normalizedSearch =
        search.trim().toLowerCase()

      return datasets.filter(
        (dataset) => {
          const matchesSearch =
            !normalizedSearch ||
            dataset.name
              .toLowerCase()
              .includes(
                normalizedSearch,
              ) ||
            dataset.description
              .toLowerCase()
              .includes(
                normalizedSearch,
              )

          const matchesStatus =
            status === "ALL" ||
            dataset.status === status

          return (
            matchesSearch &&
            matchesStatus
          )
        },
      )
    }, [datasets, search, status])
  async function handleCreateDataset(input: { name: string; description: string }) {
    await createDataset(workspaceId ?? "", input)
    onDatasetsChanged()
    resource.retry()
  }
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} />

  return (
    <div className="space-y-5">

      <section className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-950">
            Datasets
          </h2>

          <p className="mt-1 text-sm text-slate-500">
            Manage datasets inside this workspace.
          </p>
        </div>

        <Button
        onClick={() => setCreateOpen(true)}
        >
        <Plus size={17} />
        New Dataset
        </Button>
      </section>

      <DatasetsToolbar
        search={search}
        status={status}
        onSearchChange={setSearch}
        onStatusChange={setStatus}
      />

      <DatasetList
        datasets={filteredDatasets}
        workspaceId={workspaceId ?? ""}
      />
      <CreateDatasetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={handleCreateDataset}
        />
    </div>
  )
}