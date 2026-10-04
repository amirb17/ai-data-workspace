import {
  ArrowRight,
  Database,
} from "lucide-react"
import { Link } from "react-router-dom"

import { Card } from "../../../components/ui/Card"
import { DatasetStatusBadge } from "./DatasetStatusBadge"
import type { DatasetListItem } from "../types"

type DatasetListProps = {
  datasets: DatasetListItem[]
  workspaceId: string
}

function formatRows(
  value: number,
) {
  return new Intl.NumberFormat().format(value)
}

export function DatasetList({
  datasets,
  workspaceId,
}: DatasetListProps) {
  function getDatasetUrl(
    datasetId: number,
  ) {
    return `/app/workspaces/${workspaceId}/datasets/${datasetId}`
  }
  if (datasets.length === 0) {
    return (
      <Card className="p-8 text-center sm:p-12">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
          <Database size={22} />
        </div>

        <h2 className="mt-4 text-base font-semibold text-slate-900">
          No datasets found
        </h2>

        <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
          Try changing your search or status filter.
        </p>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      {/* Desktop */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs font-medium uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-5 py-3">
                Dataset
              </th>

              <th className="px-5 py-3">
                Status
              </th>

              <th className="px-5 py-3">
                Version
              </th>

              <th className="px-5 py-3">
                Rows
              </th>

              <th className="px-5 py-3">
                Columns
              </th>

              <th className="px-5 py-3">
                Updated
              </th>

              <th className="px-5 py-3">
                <span className="sr-only">
                  Open
                </span>
              </th>
            </tr>
          </thead>

          <tbody className="divide-y divide-slate-100">
            {datasets.map((dataset) => (
              <tr
                key={dataset.id}
                className="transition hover:bg-slate-50/70"
              >
                <td className="px-5 py-4">
                  <div>
                    <p className="font-semibold text-slate-900">
                      {dataset.name}
                    </p>

                    <p className="mt-1 max-w-md text-xs text-slate-500">
                      {dataset.description}
                    </p>
                  </div>
                </td>

                <td className="px-5 py-4">
                  <DatasetStatusBadge
                    status={dataset.status}
                  />
                </td>

                <td className="px-5 py-4 text-slate-600">
                  v{dataset.latestVersion}
                </td>

                <td className="px-5 py-4 text-slate-600">
                  {formatRows(
                    dataset.rowCount,
                  )}
                </td>

                <td className="px-5 py-4 text-slate-600">
                  {dataset.columnCount}
                </td>

                <td className="px-5 py-4 text-slate-500">
                  {dataset.updatedAt}
                </td>

                <td className="px-5 py-4">
                  <Link
                    to={getDatasetUrl(dataset.id)}
                    className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-indigo-600 transition hover:bg-indigo-50"
                    aria-label={`Open ${dataset.name}`}
                  >
                    <ArrowRight size={17} />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile */}
      <div className="divide-y divide-slate-100 md:hidden">
        {datasets.map((dataset) => (
          <Link
            key={dataset.id}
            to={getDatasetUrl(dataset.id)}
            className="block p-4 transition hover:bg-slate-50"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold text-slate-900">
                  {dataset.name}
                </p>

                <p className="mt-1 text-xs leading-5 text-slate-500">
                  {dataset.description}
                </p>
              </div>

              <DatasetStatusBadge
                status={dataset.status}
              />
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-xs text-slate-500">
                  Version
                </p>

                <p className="mt-1 font-medium text-slate-900">
                  v{dataset.latestVersion}
                </p>
              </div>

              <div>
                <p className="text-xs text-slate-500">
                  Rows
                </p>

                <p className="mt-1 font-medium text-slate-900">
                  {formatRows(
                    dataset.rowCount,
                  )}
                </p>
              </div>

              <div>
                <p className="text-xs text-slate-500">
                  Columns
                </p>

                <p className="mt-1 font-medium text-slate-900">
                  {dataset.columnCount}
                </p>
              </div>

              <div>
                <p className="text-xs text-slate-500">
                  Updated
                </p>

                <p className="mt-1 font-medium text-slate-900">
                  {dataset.updatedAt}
                </p>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-2 text-sm font-medium text-indigo-600">
              Open dataset
              <ArrowRight size={15} />
            </div>
          </Link>
        ))}
      </div>
    </Card>
  )
}