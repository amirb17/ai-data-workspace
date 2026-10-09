import { useParams } from 'react-router-dom'
import { DatasetUnderstanding } from '../../../features/datasets/understanding/DatasetUnderstanding'
export function DatasetUnderstandingPage() {
  const {workspaceId='',datasetId=''} = useParams()
  return <DatasetUnderstanding workspaceId={workspaceId} datasetId={datasetId}/>
}
