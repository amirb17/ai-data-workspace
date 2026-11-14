export function LoadingState({label='Loading...'}:{label?:string}){
 return <div role="status" aria-live="polite" className="space-y-4"><p className="text-sm text-slate-600">{label}</p><div aria-hidden="true" className="space-y-3 motion-safe:animate-pulse"><div className="h-4 w-2/5 rounded bg-slate-200"/><div className="h-4 w-4/5 rounded bg-slate-100"/><div className="h-16 rounded-lg bg-slate-100"/></div></div>
}
