import {useEffect,useRef} from 'react'
import {Compartment,EditorState} from '@codemirror/state'
import {EditorView,keymap,tooltips} from '@codemirror/view'
import {basicSetup} from 'codemirror'
import {sql,StandardSQL,schemaCompletionSource} from '@codemirror/lang-sql'
import {indentWithTab} from '@codemirror/commands'
import {autocompletion} from '@codemirror/autocomplete'
import type {CompletionContext} from '@codemirror/autocomplete'
import type {SqlCatalog} from '../../../services/api/sqlAnalytics'

export function SqlEditor({value,onChange,catalog,run,save,format,disabled}: {value:string;onChange:(text:string)=>void;catalog:SqlCatalog;run:()=>void;save:()=>void;format:()=>void;disabled:boolean}) {
  const host=useRef<HTMLDivElement>(null),view=useRef<EditorView|null>(null),handlers=useRef({onChange,run,save,format,disabled})
  const initialValue=useRef(value),completionConfig=useRef(new Compartment()),completionKey=useRef('')
  useEffect(()=>{handlers.current={onChange,run,save,format,disabled}},[onChange,run,save,format,disabled])
  useEffect(()=>{
    if(!host.current)return
    completionKey.current=''
    const editor=new EditorView({parent:host.current,state:EditorState.create({doc:initialValue.current,extensions:[basicSetup,completionConfig.current.of([]),
      keymap.of([{key:'Mod-Enter',run:()=>{if(!handlers.current.disabled)handlers.current.run();return true}},{key:'Mod-s',run:()=>{handlers.current.save();return true}},{key:'Mod-Shift-f',run:()=>{handlers.current.format();return true}},indentWithTab]),
      tooltips({parent:document.body}),EditorView.contentAttributes.of({'aria-label':'SQL editor','spellcheck':'false'}),EditorView.updateListener.of(update=>{if(update.docChanged)handlers.current.onChange(update.state.doc.toString())}),
      EditorView.theme({'&':{fontSize:'13px',backgroundColor:'#fff'},'.cm-scroller':{overflow:'auto',minHeight:'210px',maxHeight:'360px'},'.cm-content':{fontFamily:'ui-monospace, monospace'},'.cm-tooltip':{zIndex:'50'}})]})})
    view.current=editor
    return()=>{editor.destroy();view.current=null}
  },[])
  useEffect(()=>{
    const key=JSON.stringify([catalog.datasets.filter(d=>d.queryable).map(d=>({alias:d.alias,name:d.name,columns:d.columns.filter(c=>c.queryable)})),catalog.capabilities.functions])
    if(!view.current||key===completionKey.current)return
    completionKey.current=key
    const schema=Object.fromEntries(catalog.datasets.filter(d=>d.queryable).map(d=>[d.alias,d.columns.filter(c=>c.queryable).map(c=>c.name)]))
    const semantic=(context:CompletionContext)=>{const word=context.matchBefore(/[\w.]*/);if(!word||!word.text&&!context.explicit)return null;const prefix=word.text.includes('.')?word.text.split('.')[0]:undefined;
      const options=prefix?catalog.datasets.filter(d=>d.alias===prefix&&d.queryable).flatMap(d=>d.columns.filter(c=>c.queryable).map(c=>({label:c.name,apply:'"'+c.name.replaceAll('"','""')+'"',type:'property',detail:c.type+' · '+(c.role==='UNKNOWN'?'Meaning not confirmed':c.role.toLowerCase().replaceAll('_',' ')),info:c.description}))):catalog.datasets.filter(d=>d.queryable).map(d=>({label:d.alias,type:'class',detail:d.name+' · Current'}));
      return {from:word.from+(prefix?prefix.length+1:0),options,validFor:/^\w*$/}}
    view.current.dispatch({effects:completionConfig.current.reconfigure([sql({dialect:StandardSQL,schema}),autocompletion({override:[semantic,context=>{const qualifier=context.matchBefore(/\w+\.\w*$/)?.text.split('.')[0];return qualifier&&qualifier in schema?null:schemaCompletionSource({dialect:StandardSQL,schema})(context)},context=>{if(context.matchBefore(/\w+\.\w*$/))return null;const word=context.matchBefore(/\w*/);return word&&(word.text||context.explicit)?{from:word.from,options:[...['SELECT','FROM','WHERE','GROUP BY','HAVING','ORDER BY','LIMIT','WITH','CASE','JOIN','ON','AS'],...catalog.capabilities.functions].map(label=>({label,type:'keyword'}))}:null}]})])})
  },[catalog])
  useEffect(()=>{if(view.current&&view.current.state.doc.toString()!==value)view.current.dispatch({changes:{from:0,to:view.current.state.doc.length,insert:value}})},[value])
  return <div className="min-w-0 overflow-hidden rounded-lg border border-slate-200" ref={host}/>
}
