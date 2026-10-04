import type { DatasetContract } from "../types"

export function DatasetContractSummary({ contract }: { contract: DatasetContract }) {
  return (
    <div className="space-y-4">
      <dl className="grid gap-4 rounded-lg bg-slate-50 p-4 sm:grid-cols-3">
        <div><dt className="text-sm text-slate-500">Load mode</dt><dd className="font-semibold">{contract.loadMode}</dd></div>
        <div><dt className="text-sm text-slate-500">Schema policy</dt><dd className="font-semibold">{contract.schemaEvolutionPolicy}</dd></div>
        <div><dt className="text-sm text-slate-500">Primary / business key</dt><dd className="break-words font-semibold">{contract.primaryKey.join(", ") || "None"}</dd></div>
      </dl>
      <div className="space-y-2">
        {contract.columns.map((column) => <div key={column.name} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-3 text-sm">
          <span className="min-w-0 break-all font-medium">{column.name}</span>
          <span className="text-slate-600">{column.dataType} · {column.required ? "Required" : "Optional"}{contract.primaryKey.includes(column.name) ? " · Business key" : ""}</span>
        </div>)}
      </div>
    </div>
  )
}
