import {useEffect, type Dispatch, type SetStateAction} from 'react'

// A hidden mobile dialog must not retain keyboard containment on desktop.
export function useCloseOnBreakpoint(setOpen:Dispatch<SetStateAction<boolean>>,query:string) {
  useEffect(()=>{
    const media=window.matchMedia(query)
    const changed=()=>{if(media.matches)setOpen(false)}
    media.addEventListener('change',changed)
    return()=>media.removeEventListener('change',changed)
  },[setOpen,query])
}
