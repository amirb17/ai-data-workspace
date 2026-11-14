import {useEffect,useState,type ReactNode} from 'react'
import {authConfig} from '../../services/api/auth'
import {LaunchContext,UploadAllowedContext} from '../../services/publicLaunchContext'
import {ReleaseFeatures} from '../../services/releaseFeatures'
export function PublicLaunchPolicy({children}:{children:ReactNode}){
  const [config,setConfig]=useState<Awaited<ReturnType<typeof authConfig>>>()
  const [failed,setFailed]=useState(false)
  useEffect(()=>{let current=true;authConfig().then(value=>{if(current)setConfig(value)}).catch(()=>{if(current)setFailed(true)});return()=>{current=false}},[])
  const publicMode=config?.public_launch_mode!=='off'
  const available=!!config && ['off','csv-readonly','csv-processing'].includes(config.public_launch_mode??'')
  const features={checking:!config&&!failed,processing:available&&config?.processing_available===true,initialAppend:available&&config?.initial_append_available===true,
    rules:available&&config?.rule_review_available===true,model:available&&config?.model_read_available===true,ask:available&&config?.ask_ai_available===true}
  return <LaunchContext.Provider value={publicMode}><UploadAllowedContext.Provider value={available&&(!publicMode||config?.csv_upload_available===true)}><ReleaseFeatures.Provider value={features}>
    {publicMode&&<aside role="status" className="border-b border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 sm:px-6">
      {!available ? (failed?'Website permissions unavailable. Refresh to retry.':'Checking website permissions...') :
        <><span className="font-medium">Development release</span><span className="mx-2" aria-hidden="true">/</span>{features.processing?'CSV processing available. Review rules when prompted.':'Processing awaits runtime approval.'}{!features.ask&&' Ask AI awaits approval.'}</>}
    </aside>}
    {children}
  </ReleaseFeatures.Provider></UploadAllowedContext.Provider></LaunchContext.Provider>
}
