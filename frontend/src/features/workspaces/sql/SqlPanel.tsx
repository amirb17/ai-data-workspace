import {createPortal} from 'react-dom'
import {X} from 'lucide-react'
import {useDialogFocus} from '../../../components/ui/useDialogFocus'
import type {ReactNode} from 'react'
export function SqlPanel({title,close,children}: {title:string;close:()=>void;children:ReactNode}) {
  const ref=useDialogFocus(true,close)
  return createPortal(<div className="fixed inset-0 z-50 flex justify-end bg-slate-950/30" onClick={e=>{if(e.currentTarget===e.target)close()}}><div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="flex h-[100dvh] w-full max-w-lg flex-col bg-white shadow-xl"><header className="flex shrink-0 items-center justify-between border-b px-5 py-3"><h2 className="text-lg font-semibold">{title}</h2><button aria-label={'Close '+title} className="min-h-11 min-w-11" onClick={close}><X size={20} className="mx-auto"/></button></header><div className="min-h-0 overflow-y-auto p-4">{children}</div></div></div>,document.body)
}
export function SqlConfirm({reverse=false,newQuery=false,close,confirm}: {reverse?:boolean;newQuery?:boolean;close:()=>void;confirm:()=>void}) {
  const ref=useDialogFocus(true,close)
  return createPortal(<div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/40 p-4"><div ref={ref} role="dialog" aria-modal="true" aria-labelledby="sql-confirm-title" className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl"><h2 id="sql-confirm-title" className="font-semibold">{reverse?'Return to Visual Builder?':'You have unsaved changes.'}</h2><p className="mt-3 text-sm text-slate-600">{reverse?'This query uses SQL features that cannot be edited safely in Visual Builder. Reset Builder to start a new visual query.':'Discard this draft to open a different query or start a new one.'}</p><div className="mt-5 flex flex-wrap justify-end gap-2"><button className="min-h-11 rounded-lg border px-3 text-sm" onClick={close}>{reverse?'Keep SQL Editor':'Keep editing'}</button><button className="min-h-11 rounded-lg bg-indigo-600 px-3 text-sm text-white" onClick={confirm}>{reverse?'Reset Builder':newQuery?'Discard and start new query':'Discard and continue'}</button></div></div></div>,document.body)
}
