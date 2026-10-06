import { useEffect, useState } from "react"
import { Link, useOutletContext, useParams } from "react-router-dom"
import { Button } from "../../../components/ui/Button"
import { DatasetContractForm } from "../../../features/datasets/contracts/components/DatasetContractForm"
import { DatasetContractSummary } from "../../../features/datasets/contracts/components/DatasetContractSummary"
import { contractStorageKey, getDatasetContract, saveDatasetContract } from "../../../features/datasets/contracts/storage"
import type { DatasetContract } from "../../../features/datasets/contracts/types"
import type { DatasetListItem } from "../../../features/datasets/types"

export function DatasetContractPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const dataset = useOutletContext<DatasetListItem>()
  const [editing, setEditing] = useState(false)
  const [, refresh] = useState(0)
  useEffect(() => {
    const update = () => { setEditing(false); refresh((value) => value + 1) }
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === contractStorageKey(workspaceId, datasetId)) update()
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener("datarise-contract-changed", update)
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("datarise-contract-changed", update) }
  }, [workspaceId, datasetId])
  let contract: DatasetContract | undefined
  let error = ""
  try { contract = getDatasetContract(workspaceId, datasetId) }
  catch (caught) { error = caught instanceof Error ? caught.message : "Unable to read the contract." }
  return (
    <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-xl font-semibold text-slate-950">Dataset Contract</h2><p className="mt-1 text-sm text-slate-500">Defines the structure DataRise expects for {dataset.name}. Saved in this browser during the prototype.</p></div>
        {contract && !editing && <Button onClick={() => setEditing(true)}>Edit Contract</Button>}
      </div>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {contract ? editing ? <DatasetContractForm initialContract={{ ...contract, datasetName: dataset.name }} onCancel={() => setEditing(false)}
        onSave={(updated) => { saveDatasetContract(workspaceId, datasetId, updated); setEditing(false); refresh((value) => value + 1) }} />
        : <DatasetContractSummary contract={contract} />
        : !error && <div className="space-y-3"><h3 className="font-semibold">No contract configured</h3><p className="text-sm text-slate-600">The first contract defines the expected structure of future deliveries. Choose a CSV to inspect and configure its columns; the same selected file stays available after saving.</p><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/files?setup=1`}>Configure Contract</Link></div>}
    </section>
  )
}
