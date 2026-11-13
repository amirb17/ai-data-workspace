import {createContext,useContext} from 'react'
export const LaunchContext=createContext(false)
export const usePublicReadOnly=()=>useContext(LaunchContext)

export const UploadAllowedContext=createContext(false)
export const usePublicUploadAllowed=()=>useContext(UploadAllowedContext)
