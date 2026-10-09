import { useParams } from 'react-router-dom'
import { DataProfile } from '../../../features/datasets/profile/DataProfile'
export function DatasetProfilePage() {
  const {workspaceId='',datasetId=''}=useParams()
  return <DataProfile workspaceId={workspaceId} datasetId={datasetId} />
}
