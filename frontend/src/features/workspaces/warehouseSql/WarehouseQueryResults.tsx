import type {WarehouseResults} from '../../../services/api/warehouseQueries'
export function WarehouseQueryResults({page}:{page:WarehouseResults}){
 return <section aria-label="Verified warehouse results" className="min-w-0 rounded-xl border bg-white p-4">
  <h2 className="font-semibold">Verified results</h2><p className="my-2 text-sm text-slate-600">{page.rows.length} rows in this bounded first page.</p>
  {page.rows.length===0?<p>No result rows.</p>:<div className="max-h-96 overflow-auto" tabIndex={0} aria-label="Scrollable result table"><table className="w-full text-left text-sm"><thead><tr>{page.columns.map(c=><th scope="col" className="border-b p-3" key={c}>{c}</th>)}</tr></thead><tbody>{page.rows.map((row,i)=><tr key={i}>{row.map((cell,j)=><td className="max-w-xs break-words border-b p-3 align-top whitespace-pre-wrap" key={j}>{cell===null?<span className="text-slate-500">NULL</span>:String(cell)}</td>)}</tr>)}</tbody></table></div>}
  {page.has_more&&<p className="mt-3 text-sm text-amber-800">Additional rows exist. This interface supports the first page only.</p>}
 </section>
}
