import { useState } from "react"
import { Button } from "../../../../components/ui/Button"
import { datasetLoadModes, schemaEvolutionPolicies, type DatasetContract } from "../types"
import { validateContract } from "../validation"
import { DatasetContractColumnRow } from "./DatasetContractColumnRow"

type Props = {
  initialContract: DatasetContract
  onSave: (contract: DatasetContract) => void
  onCancel: () => void
}
export function DatasetContractForm({ initialContract, onSave, onCancel }: Props) {
  const [contract, setContract] = useState<DatasetContract>(() => ({
    ...initialContract, columns: initialContract.columns.map((column) => ({ ...column })), primaryKey: [...initialContract.primaryKey],
  }))
  const [saveError, setSaveError] = useState("")
  const validationError = validateContract(contract)
  return (
    <form className="space-y-5" onSubmit={(event) => {
      event.preventDefault()
      if (validationError) return
      try { onSave(contract) } catch (error) { setSaveError(error instanceof Error ? error.message : "Unable to save contract.") }
    }}>
      <p className="text-sm text-slate-600">Configure {contract.datasetName}. The business key uniquely identifies a record. Key columns are always required.</p>
      <div className="space-y-2">
        {contract.columns.map((column, index) => <DatasetContractColumnRow key={column.name} column={column}
          isKey={contract.primaryKey.includes(column.name)}
          onChange={(updated) => setContract((current) => ({ ...current, columns: current.columns.map((item, i) => i === index ? updated : item) }))}
          onKeyChange={(selected) => setContract((current) => ({ ...current,
            primaryKey: selected ? [...current.primaryKey.filter((key) => key !== column.name), column.name] : current.primaryKey.filter((key) => key !== column.name),
            columns: current.columns.map((item, i) => i === index && selected ? { ...item, required: true } : item),
          }))} />)}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-2 text-sm font-medium text-slate-700">Load mode
          <select className="block min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3" value={contract.loadMode}
            onChange={(event) => setContract({ ...contract, loadMode: event.target.value as DatasetContract["loadMode"] })}>
            {datasetLoadModes.map((mode) => <option key={mode}>{mode}</option>)}
          </select>
        </label>
        <label className="space-y-2 text-sm font-medium text-slate-700">Schema evolution policy
          <select className="block min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3" value={contract.schemaEvolutionPolicy}
            onChange={(event) => setContract({ ...contract, schemaEvolutionPolicy: event.target.value as DatasetContract["schemaEvolutionPolicy"] })}>
            {schemaEvolutionPolicies.map((policy) => <option key={policy} value={policy}>{policy === "STRICT" ? "Exact structure required" : "Allow additional columns with warnings"}</option>)}
          </select>
        </label>
      </div>
      <p className="text-sm text-slate-500">UPSERT and SNAPSHOT require a business key. Exact structure blocks extra columns; allowing additional columns produces warnings without changing the contract. Saving does not start processing.</p>
      {(validationError || saveError) && <p role="alert" className="text-sm text-red-700">{validationError || saveError}</p>}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={!!validationError}>Save Contract</Button>
      </div>
    </form>
  )
}
