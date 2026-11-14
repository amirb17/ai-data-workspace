import {createContext,useContext} from 'react'
export const ReleaseFeatures = createContext({checking:true,processing:false,initialAppend:false,rules:false,model:false,ask:false})
export const useReleaseFeatures = () => useContext(ReleaseFeatures)
