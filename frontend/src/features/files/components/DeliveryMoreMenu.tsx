import { Link } from "react-router-dom"
import { archiveAction } from "../deliveryArchive"
import type { ProcessingContext } from "../../../services/api/rules"
export function DeliveryMoreMenu({context,fileName,processingHref,onArchive,onViewDetails}: {context:ProcessingContext;fileName:string;processingHref:string;onArchive:()=>void;onViewDetails:()=>void}) {
  const action = archiveAction(context)
  return <details className="relative"><summary aria-label={`More actions for ${fileName}`} className="min-h-11 cursor-pointer rounded-lg px-3 py-3 text-sm text-slate-600">More</summary><div className="flex min-w-48 flex-col gap-1 rounded-lg border bg-white p-2 text-sm shadow-sm">
    <button className="min-h-11 px-2 text-left" onClick={onViewDetails}>View details</button>
    <Link className="inline-flex min-h-11 items-center px-2" to={processingHref}>View Processing</Link>
    <button className="min-h-11 px-2 text-left disabled:opacity-50" disabled={action.blocked} onClick={onArchive}>{action.label}</button>
    {action.blocked && <p className="max-w-xs px-2 text-xs text-slate-500">Currently processing; removal is unavailable.</p>}
  </div></details>
}
