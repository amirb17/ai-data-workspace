import {
  // BarChart3,
  Database,
  FileText,
  History,
  // ListChecks,
  // ShieldCheck,
  Workflow,
} from "lucide-react"
import {
  Link,
  NavLink,
  Outlet,
  useParams,
} from "react-router-dom"

import { Badge } from "../../components/ui/Badge"
import { StatCard } from "../../components/ui/StatCard"
import { readWorkspaceDatasets } from "../../features/datasets/data/storage"
import type { DatasetListItem } from "../../features/datasets/types"

const tabs = [
  {
    label: "Overview",
    to: "",
    end: true,
  },
  {
    label: "Files",
    to: "files",
  },
  {
    label: "Contract",
    to: "contract",
  },
  {
    label: "Rules",
    to: "rules",
  },
  {
    label: "Processing",
    to: "processing",
  },
  {
    label: "Data Quality",
    to: "data-quality",
  },
  {
    label: "Analytics",
    to: "analytics",
  },
  {
    label: "History",
    to: "history",
  },
]

function getStatusVariant(
  status: DatasetListItem["status"],
) {
  switch (status) {
    case "READY":
      return "success"

    case "PROCESSING":
      return "processing"

    case "FAILED":
      return "error"

    case "NEEDS_ATTENTION":
      return "warning"

    case "EMPTY":
    default:
      return "neutral"
  }
}

function formatStatus(
  status: DatasetListItem["status"],
) {
  return status
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (value) =>
      value.toUpperCase(),
    )
}

export function DatasetDetailPage() {
  const {
    workspaceId,
    datasetId,
  } = useParams()

  if (!workspaceId || !datasetId) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-slate-950">
          Dataset not found
        </h1>
      </div>
    )
  }

  const datasets = readWorkspaceDatasets(workspaceId)

  const dataset = datasets.find(
    (item) =>
      String(item.id) === datasetId,
  )

  if (!dataset) {
    return (
      <div className="mx-auto max-w-[1600px]">
        <h1 className="text-2xl font-semibold text-slate-950">
          Dataset not found
        </h1>

        <p className="mt-2 text-sm text-slate-500">
          This dataset does not exist in the selected workspace.
        </p>

        <Link
          to={`/app/workspaces/${workspaceId}/datasets`}
          className="mt-4 inline-flex text-sm font-medium text-indigo-600 hover:text-indigo-700"
        >
          Back to datasets
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          to={`/app/workspaces/${workspaceId}/datasets`}
          className="text-sm font-medium text-slate-500 transition hover:text-slate-900"
        >
          ← Back to datasets
        </Link>
      </div>

      <section className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-sm font-medium text-indigo-600">
            Dataset
          </p>

          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
              {dataset.name}
            </h1>

            <Badge
              variant={getStatusVariant(
                dataset.status,
              )}
            >
              {formatStatus(
                dataset.status,
              )}
            </Badge>
          </div>

          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            {dataset.description}
          </p>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Latest Version"
          value={`v${dataset.latestVersion}`}
          helper="Current dataset version"
          icon={<History size={20} />}
        />

        <StatCard
          label="Rows"
          value={dataset.rowCount.toLocaleString()}
          helper="Latest processed version"
          icon={<Database size={20} />}
        />

        <StatCard
          label="Columns"
          value={dataset.columnCount}
          helper="Detected schema fields"
          icon={<FileText size={20} />}
        />

        <StatCard
          label="Status"
          value={formatStatus(
            dataset.status,
          )}
          helper={`Updated ${dataset.updatedAt}`}
          icon={<Workflow size={20} />}
        />
      </section>

      <div className="overflow-x-auto border-b border-slate-200">
        <nav className="flex min-w-max gap-6">
          {tabs.map((tab) => (
            <NavLink
              key={tab.label}
              to={tab.to}
              end={tab.end}
              className={({
                isActive,
              }) =>
                [
                  "border-b-2 px-1 pb-3 text-sm font-medium transition",
                  isActive
                    ? "border-indigo-600 text-indigo-600"
                    : "border-transparent text-slate-500 hover:text-slate-900",
                ].join(" ")
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet key={`${workspaceId}:${datasetId}`} context={dataset} />
    </div>
  )
}