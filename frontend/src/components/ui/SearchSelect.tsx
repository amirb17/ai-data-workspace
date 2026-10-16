import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react'
import {createPortal} from 'react-dom'
import {Check,ChevronDown,X} from 'lucide-react'

export interface SelectOption {value:string; label:string; detail?:string; group?:string; disabled?:boolean; reason?:string}
export function SearchSelect({label,options,value,onChange,multiple=false,loading=false,disabled=false,searchable=true}: {
  label:string; options:SelectOption[]; value:string|string[]; onChange:(value:string[])=>void; multiple?:boolean; loading?:boolean; disabled?:boolean; searchable?:boolean
}) {
  const id=useId(),trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null),search=useRef<HTMLInputElement>(null)
  const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[active,setActive]=useState(0),[position,setPosition]=useState({left:12,top:12,width:280,height:300})
  const selected=Array.isArray(value)?value:[value], filtered=options.filter(o=>(o.label+' '+(o.detail||'')).toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  function close(focus=false){setOpen(false);if(focus)trigger.current?.focus()}
  function choose(option:SelectOption){if(option.disabled)return;onChange(multiple?selected.includes(option.value)?selected.filter(v=>v!==option.value):[...selected,option.value]:[option.value]);if(!multiple)close(true)}
  useLayoutEffect(()=>{
    if(!open)return
    const place=()=>{const box=trigger.current?.getBoundingClientRect();if(!box)return;const width=Math.min(Math.max(box.width,280),window.innerWidth-24),spaceBelow=window.innerHeight-box.bottom-16,above=spaceBelow<180&&box.top>spaceBelow;
      const height=Math.min(320,Math.max(120,above?box.top-16:spaceBelow));setPosition({left:Math.max(12,Math.min(box.left,window.innerWidth-width-12)),top:above?Math.max(8,box.top-height-6):box.bottom+6,width,height})}
    const frame=requestAnimationFrame(()=>{place();(search.current||panel.current?.querySelector<HTMLElement>('[role="listbox"]'))?.focus()})
    window.addEventListener('resize',place);window.addEventListener('scroll',place,true)
    return()=>{cancelAnimationFrame(frame);window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)}
  },[open])
  useEffect(()=>{if(!open)return;const outside=(e:PointerEvent)=>{if(!panel.current?.contains(e.target as Node)&&!trigger.current?.contains(e.target as Node))close()};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside)},[open])
  const title=selected.map(v=>options.find(o=>o.value===v)?.label).filter(Boolean)
  return <div className="min-w-0 space-y-1"><label id={id+'-label'} className="block text-xs font-medium text-slate-600">{label}</label>
    <button ref={trigger} type="button" role="combobox" aria-labelledby={id+'-label'} aria-expanded={open} aria-controls={id+'-list'} aria-haspopup="listbox" disabled={disabled||loading} onClick={()=>{setQuery('');setActive(0);setOpen(v=>!v)}} onKeyDown={e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();setQuery('');setActive(0);setOpen(true)}else if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&e.key!==' '){e.preventDefault();setQuery(e.key);setActive(0);setOpen(true)}}} className="flex min-h-11 w-full items-center justify-between gap-2 rounded-lg border bg-white px-3 text-left text-sm focus:ring-2 focus:ring-indigo-500 disabled:opacity-60"><span className="min-w-0 truncate">{loading?'Loading options…':multiple?`${title.length} selected`:title[0]||'Choose '+label.toLowerCase()}</span><ChevronDown size={16}/></button>
    {multiple&&title.length>0&&<div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">{selected.map(v=>{const option=options.find(o=>o.value===v);return option&&<button type="button" key={v} onClick={()=>onChange(selected.filter(x=>x!==v))} aria-label={'Remove '+option.label} className="inline-flex min-h-9 max-w-full items-center gap-1 rounded border bg-indigo-50 px-2 text-xs"><span className="truncate">{option.label}</span><X size={12}/></button>})}</div>}
    {open&&createPortal(<div ref={panel} className="fixed z-[70] flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white p-2 shadow-xl" style={{left:position.left,top:position.top,width:position.width,maxHeight:position.height}} onKeyDown={e=>{
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true)}
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const next=filtered.length?(active+(e.key==='ArrowDown'?1:-1)+filtered.length)%filtered.length:0;setActive(next);panel.current?.querySelector('#'+CSS.escape(id+'-option-'+next))?.scrollIntoView({block:'nearest'})}
      if(e.key==='Enter'&&filtered[active]){e.preventDefault();choose(filtered[active])}
      if(e.key==='Tab'){e.preventDefault();e.stopPropagation();const controls=[...document.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]')].filter(el=>!panel.current?.contains(el)&&el.getClientRects().length);const index=controls.indexOf(trigger.current!);close();(e.shiftKey?trigger.current:controls[index+1])?.focus()}
    }}>{searchable&&<input ref={search} aria-label={'Search '+label.toLowerCase()} role="combobox" aria-expanded="true" aria-controls={id+'-list'} aria-activedescendant={filtered[active]?id+'-option-'+active:undefined} placeholder={'Search '+label.toLowerCase()} value={query} onChange={e=>{setQuery(e.target.value);setActive(0)}} className="min-h-11 shrink-0 rounded border px-3 text-sm focus:ring-2 focus:ring-indigo-500"/>}
      <div id={id+'-list'} role="listbox" tabIndex={searchable?undefined:-1} aria-activedescendant={!searchable&&filtered[active]?id+'-option-'+active:undefined} aria-labelledby={id+'-label'} aria-multiselectable={multiple||undefined} className="mt-2 min-h-0 overflow-y-auto">{filtered.length?filtered.map((o,i)=><button key={o.value} id={id+'-option-'+i} tabIndex={-1} type="button" role="option" aria-selected={selected.includes(o.value)} aria-disabled={o.disabled||undefined} onMouseMove={()=>setActive(i)} onClick={()=>choose(o)} className={`flex min-h-11 w-full gap-2 rounded px-2 py-2 text-left text-sm ${active===i?'bg-indigo-50':''} ${o.disabled?'text-slate-400':'text-slate-800'}`}><span className="w-4 shrink-0">{selected.includes(o.value)&&<Check size={16}/>}</span><span className="min-w-0 break-words"><span className="block font-medium">{o.label}</span>{o.group&&<span className="block text-[10px] uppercase tracking-wide text-indigo-600">{o.group}</span>}{o.detail&&<span className="block text-xs text-slate-500">{o.detail}</span>}{o.disabled&&<span className="block text-xs">{o.reason||'Unavailable'}</span>}</span></button>):<p role="status" className="p-3 text-sm text-slate-500">No {label.toLowerCase()} match “{query}”. Try another field.</p>}</div>
    </div>,document.body)}
  </div>
}

export function SmallSelect({label,value,options,onChange}: {label:string;value:string;options:SelectOption[];onChange:(value:string)=>void}) {
  return <SearchSelect label={label} value={value} options={options} onChange={v=>onChange(v[0]||'')} searchable={false}/>
}
