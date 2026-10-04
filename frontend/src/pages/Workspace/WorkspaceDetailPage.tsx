import {
  BarChart3,
  Database,
  Plus,
  ShieldAlert,
  Workflow,
} from "lucide-react"
import {
  Link,
  NavLink,
  Outlet,
  useParams,
} from "react-router-dom"

import { Button } from "../../components/ui/Button"
import { StatCard } from "../../components/ui/StatCard"
import { readWorkspaceDatasets } from "../../features/datasets/data/storage"
import { workspacesMock } from "../../features/workspaces/data/workspaces.mock"
import type { WorkspaceListItem } from "../../features/workspaces/types"

const tabs = [
  {
    label: "Overview",
    to: "",
    end: true,
  },
  {
    label: "Datasets",
    to: "datasets",
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
]

export function WorkspaceDetailPage() {
  const { workspaceId } = useParams()

  const storedWorkspaces = localStorage.getItem(
    "datarise-workspaces",
  )

  let workspaces: WorkspaceListItem[] =
    workspacesMock

  if (storedWorkspaces) {
    try {
      workspaces = JSON.parse(
        storedWorkspaces,
      ) as WorkspaceListItem[]
    } catch {
      workspaces = workspacesMock
    }
  }

  const workspace = workspaces.find(
    (item) =>
      String(item.id) === workspaceId,
  )

  if (!workspaceId || !workspace) {
    return (
      <div className="mx-auto max-w-[1600px]">
        <h1 className="text-2xl font-semibold text-slate-950">
          Workspace not found
        </h1>

        <p className="mt-2 text-sm text-slate-500">
          This workspace does not exist or is no longer available.
        </p>

        <Link
          to="/app/workspaces"
          className="mt-4 inline-flex text-sm font-medium text-indigo-600 hover:text-indigo-700"
        >
          Back to workspaces
        </Link>
      </div>
    )
  }

  const datasets = readWorkspaceDatasets(workspaceId)

  const workspaceStats = {
    datasets: datasets.length,

    analyticsReady: datasets.filter(
      (dataset) =>
        dataset.status === "READY",
    ).length,

    processing: datasets.filter(
      (dataset) =>
        dataset.status ===
        "PROCESSING",
    ).length,

    qualityIssues: datasets.filter(
      (dataset) =>
        dataset.status ===
          "FAILED" ||
        dataset.status ===
          "NEEDS_ATTENTION",
    ).length,
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <section className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-medium text-indigo-600">
            Workspace
          </p>

          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
              {workspace.name}
            </h1>

            <span className="rounded-md bg-green-50 px-2 py-1 text-xs font-medium text-green-700">
              Active
            </span>
          </div>

          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            {workspace.description}
          </p>
        </div>

        <Link
          to={`/app/workspaces/${workspaceId}/datasets`}
        >
          <Button>
            <Plus size={17} />
            New Dataset
          </Button>
        </Link>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Datasets"
          value={workspaceStats.datasets}
          helper="In this workspace"
          icon={<Database size={20} />}
        />

        <StatCard
          label="Analytics Ready"
          value={
            workspaceStats.analyticsReady
          }
          helper="Ready for exploration"
          icon={<BarChart3 size={20} />}
        />

        <StatCard
          label="Processing"
          value={
            workspaceStats.processing
          }
          helper="Currently running"
          icon={<Workflow size={20} />}
        />

        <StatCard
          label="Data Quality Issues"
          value={
            workspaceStats.qualityIssues
          }
          helper="Need review"
          icon={<ShieldAlert size={20} />}
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

      <Outlet />
    </div>
  )
}