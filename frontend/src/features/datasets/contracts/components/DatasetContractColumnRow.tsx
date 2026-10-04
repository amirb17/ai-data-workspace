import { columnDataTypes, type DatasetColumnContract } from "../types"

type Props = {
  column: DatasetColumnContract
  isKey: boolean
  onChange: (column: DatasetColumnContract) => void
  onKeyChange: (selected: boolean) => void
}
export function DatasetContractColumnRow({ column, isKey, onChange, onKeyChange }: Props) {
  return (
    <div className="grid gap-3 rounded-lg border border-slate-200 p-3 sm:grid-cols-[minmax(0,1fr)_150px_100px_140px] sm:items-center">
      <p className="break-all font-medium text-slate-900">{column.name}</p>
      <label className="text-sm text-slate-600">
        <span className="sr-only">Data type for {column.name}</span>
        <select className="min-h-10 w-full rounded-lg border border-slate-300 bg-white px-2" value={column.dataType}
          onChange={(event) => onChange({ ...column, dataType: event.target.value as DatasetColumnContract["dataType"] })}>
          {columnDataTypes.map((type) => <option key={type}>{type}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={column.required} disabled={isKey} aria-label={`Required: ${column.name}`}
          onChange={(event) => onChange({ ...column, required: event.target.checked })} /> Required
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={isKey} aria-label={`Business key: ${column.name}`}
          onChange={(event) => onKeyChange(event.target.checked)} /> Business key
      </label>
    </div>
  )
}
