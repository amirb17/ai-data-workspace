import {useEffect,useState,type ReactNode} from 'react'
import {authConfig} from '../../services/api/auth'
import {LaunchContext,UploadAllowedContext} from '../../services/publicLaunchContext'

export function PublicLaunchPolicy({children}:{children:ReactNode}){
  const [status,setStatus]=useState<{mode:'checking'|'off'|'csv-readonly'|'unavailable'; upload:boolean; creation:boolean}>({mode:'checking',upload:false,creation:false})
  useEffect(()=>{
    let current=true
    authConfig().then(value=>{
      if(!current)return
      const mode=value.public_launch_mode==='off'?'off':value.public_launch_mode==='csv-readonly'?'csv-readonly':'unavailable'
      setStatus({mode,upload:mode==='off'||(mode==='csv-readonly'&&value.csv_upload_available===true),
        creation:mode==='off'||(mode==='csv-readonly'&&value.workspace_creation_available===true)})
    }).catch(()=>{if(current)setStatus({mode:'unavailable',upload:false,creation:false})})
    return()=>{current=false}
  },[])
  const restricted=status.mode!=='off'
  return <LaunchContext.Provider value={restricted}>
    <UploadAllowedContext.Provider value={status.upload}>
      {restricted&&<aside role="status" className="border-b border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
        {status.mode==='checking'?'Checking website permissions. ':status.mode==='unavailable'?'Website permissions are unavailable. ':'Public limited release. '}
        {status.mode==='csv-readonly'&&status.creation?'Workspace and dataset creation are enabled. ':'Workspace creation is unavailable. '}
        {status.mode==='csv-readonly'&&status.upload?'CSV deliveries can be uploaded, but processing does not start automatically. ':'CSV uploads remain disabled. '}
        Processing, rule changes, AI and warehouse execution remain disabled. XLSX is unsupported.
      </aside>}
      {children}
    </UploadAllowedContext.Provider>
  </LaunchContext.Provider>
}
