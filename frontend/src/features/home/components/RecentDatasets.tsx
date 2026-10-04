import {
  MoreHorizontal,
} from "lucide-react"

import { Badge } from "../../../components/ui/Badge"
import { Card } from "../../../components/ui/Card"
import type {
  DatasetStatus,
  RecentDataset,
} from "../types"

type RecentDatasetsProps = {
  datasets: RecentDataset[]
}

function getStatusVariant(
  status: DatasetStatus,
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

    default:
      return "neutral"
  }
}

function formatRows(
  value: number,
) {
  return new Intl.NumberFormat().format(value)
}

export function RecentDatasets({
  datasets,
}: RecentDatasetsProps) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-base font-semibold text-slate-950">
            Recent Datasets
          </h2>

          <p className="mt-1 text-xs text-slate-500">
            Latest datasets in this workspace
          </p>
        </div>

        <button className="text-sm font-medium text-indigo-600 hover:text-indigo-700">
          View all
        </button>
      </div>

      {/* Desktop / tablet table */}
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
                Rows
              </th>

              <th className="px-5 py-3">
                Columns
              </th>

              <th className="px-5 py-3">
                Updated
              </th>

              <th className="w-12 px-5 py-3">
                <span className="sr-only">
                  Actions
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
                <td className="px-5 py-4 font-medium text-slate-900">
                  {dataset.name}
                </td>

                <td className="px-5 py-4">
                  <Badge
                    variant={getStatusVariant(
                      dataset.status,
                    )}
                  >
                    {dataset.status
                      .replace("_", " ")
                      .toLowerCase()
                      .replace(/\b\w/g, (value) =>
                        value.toUpperCase(),
                      )}
                  </Badge>
                </td>

                <td className="px-5 py-4 text-slate-600">
                  {formatRows(dataset.rows)}
                </td>

                <td className="px-5 py-4 text-slate-600">
                  {dataset.columns}
                </td>

                <td className="px-5 py-4 text-slate-500">
                  {dataset.updatedAt}
                </td>

                <td className="px-5 py-4">
                  <button
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
                    aria-label={`Open actions for ${dataset.name}`}
                  >
                    <MoreHorizontal size={17} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile cards */}
      <div className="divide-y divide-slate-100 md:hidden">
        {datasets.map((dataset) => (
          <div
            key={dataset.id}
            className="p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-900">
                  {dataset.name}
                </p>

                <p className="mt-1 text-xs text-slate-500">
                  {dataset.updatedAt}
                </p>
              </div>

              <Badge
                variant={getStatusVariant(
                  dataset.status,
                )}
              >
                {dataset.status
                  .replace("_", " ")
                  .toLowerCase()
                  .replace(/\b\w/g, (value) =>
                    value.toUpperCase(),
                  )}
              </Badge>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-slate-50 p-3">
                <p className="text-xs text-slate-500">
                  Rows
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-900">
                  {formatRows(dataset.rows)}
                </p>
              </div>

              <div className="rounded-lg bg-slate-50 p-3">
                <p className="text-xs text-slate-500">
                  Columns
                </p>

                <p className="mt-1 text-sm font-semibold text-slate-900">
                  {dataset.columns}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}