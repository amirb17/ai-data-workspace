import {
  Plus,
} from "lucide-react"
import {
  useMemo,
  useState,
} from "react"
import { useParams } from "react-router-dom"

import { CreateDatasetDialog } from "../../../features/datasets/components/CreateDatasetDialog"
import type { DatasetListItem } from "../../../features/datasets/types"
import { Button } from "../../../components/ui/Button"
import { DatasetList } from "../../../features/datasets/components/DatasetList"
import {
  DatasetsToolbar,
  type DatasetStatusFilter,
} from "../../../features/datasets/components/DatasetsToolbar"
import { readWorkspaceDatasets, datasetStorageKey } from "../../../features/datasets/data/storage"


export function WorkspaceDatasetsPage() {
  const { workspaceId } = useParams()
  return <WorkspaceDatasetsContent key={workspaceId} />
}

function WorkspaceDatasetsContent() {
  const { workspaceId } =
    useParams()
  const storageKey = datasetStorageKey(workspaceId ?? "")
  const [storageError, setStorageError] = useState("")
  const [search, setSearch] =
    useState("")

  const [status, setStatus] =
    useState<DatasetStatusFilter>(
      "ALL",
    )
    const [datasets, setDatasets] =
  useState<DatasetListItem[]>(() => readWorkspaceDatasets(workspaceId ?? ""))

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
    function handleCreateDataset(input: {
        name: string
        description: string
        }) {
        const newDataset: DatasetListItem = {
            id: Date.now(),
            name: input.name,
            description:
            input.description ||
            "No description added yet.",
            status: "EMPTY",
            latestVersion: 0,
            rowCount: 0,
            columnCount: 0,
            updatedAt: "Just now",
        }

        const nextDatasets = [newDataset, ...datasets]
        try {
          localStorage.setItem(storageKey, JSON.stringify(nextDatasets))
          setDatasets(nextDatasets)
          setStorageError("")
        } catch {
          setStorageError("Dataset could not be saved. Enable browser storage and retry.")
        }
        }

  return (
    <div className="space-y-5">
      {storageError && <p role="alert" className="text-sm text-red-700">{storageError}</p>}
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