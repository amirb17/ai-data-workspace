import {
  Search,
} from "lucide-react"

import { Input } from "../../../components/ui/Input"
import type { DatasetStatus } from "../types"

export type DatasetStatusFilter =
  | "ALL"
  | DatasetStatus

type DatasetsToolbarProps = {
  search: string
  status: DatasetStatusFilter
  onSearchChange: (
    value: string,
  ) => void
  onStatusChange: (
    value: DatasetStatusFilter,
  ) => void
}

export function DatasetsToolbar({
  search,
  status,
  onSearchChange,
  onStatusChange,
}: DatasetsToolbarProps) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center">
      <div className="relative flex-1">
        <Search
          size={17}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
        />

        <Input
          aria-label="Search datasets"
          value={search}
          onChange={(event) =>
            onSearchChange(
              event.target.value,
            )
          }
          placeholder="Search datasets..."
          className="pl-9"
        />
      </div>

      <select
        aria-label="Dataset status filter"
        value={status}
        onChange={(event) =>
          onStatusChange(
            event.target
              .value as DatasetStatusFilter,
          )
        }
        className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
      >
        <option value="ALL">
          All statuses
        </option>

        <option value="ACTIVE">Active</option>
        <option value="ARCHIVED">Archived</option>
      </select>
    </div>
  )
}
