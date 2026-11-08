import {useEffect,useState,type ReactNode} from 'react'
import {authConfig} from '../../services/api/auth'
import {LaunchContext} from '../../services/publicLaunchContext'

export function PublicLaunchPolicy({children}:{children:ReactNode}){
  const [mode,setMode]=useState<'checking'|'off'|'csv-readonly'|'unavailable'>('checking')
  useEffect(()=>{
    let current=true
    authConfig().then(value=>{if(current)setMode(value.public_launch_mode==='off'?'off':value.public_launch_mode==='csv-readonly'?'csv-readonly':'unavailable')})
      .catch(()=>{if(current)setMode('unavailable')})
    return()=>{current=false}
  },[])
  const restricted=mode!=='off'
  return <LaunchContext.Provider value={restricted}>
    {restricted&&<aside role="status" className="border-b border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
      {mode==='checking'?'Checking website permissions. ':mode==='unavailable'?'Website permissions are unavailable. ':'Public read-only release. '}
      Existing datasets, historical quality and approved previews are available when authorized. Uploads, creation, rule changes, processing, AI and warehouse execution are unavailable. XLSX is not supported. Newly uploaded files are never assumed processed.
    </aside>}
    {children}
  </LaunchContext.Provider>
}
