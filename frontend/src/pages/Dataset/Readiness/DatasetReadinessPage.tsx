import {useParams} from 'react-router-dom'
import {ProductReadiness} from '../../../features/datasets/readiness/ProductReadiness'
export function DatasetReadinessPage(){const {workspaceId='',datasetId=''}=useParams();return <ProductReadiness workspaceId={workspaceId} datasetId={datasetId}/>}
