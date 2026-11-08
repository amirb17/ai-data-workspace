import {createContext,useContext} from 'react'
export const LaunchContext=createContext(false)
export const usePublicReadOnly=()=>useContext(LaunchContext)
