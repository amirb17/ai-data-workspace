import {
  Navigate,
  Route,
  Routes,
} from "react-router-dom"

import { AppShell } from "./components/layout/AppShell"
import { DatasetDetailPage } from "./pages/Dataset/DatasetDetailPage"
import { LoginPage } from "./pages/Auth/LoginPage"
import { SignupPage } from "./pages/Auth/SignupPage"
import { HomePage } from "./pages/Home/HomePage"
import { ProcessingPage } from "./pages/Processing/ProcessingPage"
import { AnalyticsPage } from "./pages/Analytics/AnalyticsPage"
import { DataQualityPage } from "./pages/DataQuality/DataQualityPage"
import { SettingsPage } from "./pages/Settings/SettingsPage"
import { WorkspacesPage } from "./pages/Workspaces/WorkspacesPage"
import { WorkspaceDetailPage } from "./pages/Workspace/WorkspaceDetailPage"
import { WorkspaceOverviewPage } from "./pages/Workspace/Overview/WorkspaceOverviewPage"
import { WorkspaceDatasetsPage } from "./pages/Workspace/Datasets/WorkspaceDatasetsPage"
import { WorkspaceProcessingPage } from "./pages/Workspace/Processing/WorkspaceProcessingPage"
import { WorkspaceDataQualityPage } from "./pages/Workspace/DataQuality/WorkspaceDataQualityPage"
import { WorkspaceAnalyticsPage } from "./pages/Workspace/Analytics/WorkspaceAnalyticsPage"
import { DatasetOverviewPage } from "./pages/Dataset/Overview/DatasetOverviewPage"
import { DatasetContractPage } from "./pages/Dataset/Contract/DatasetContractPage"
import { DatasetFilesPage } from "./pages/Dataset/Files/DatasetFilesPage"
import { DatasetRulesPage } from "./pages/Dataset/Rules/DatasetRulesPage"
import { DatasetProcessingPage } from "./pages/Dataset/Processing/DatasetProcessingPage"
import { DatasetDataQualityPage } from "./pages/Dataset/DataQuality/DatasetDataQualityPage"
import { DatasetAnalyticsPage } from "./pages/Dataset/Analytics/DatasetAnalyticsPage"
import { DatasetHistoryPage } from "./pages/Dataset/History/DatasetHistoryPage"

function AppLayout() {
  return (
    <AppShell>
      <Routes>
        <Route
          index
          element={<HomePage />}
        />

        <Route
          path="processing"
          element={<ProcessingPage />}
        />

        <Route
          path="analytics"
          element={<AnalyticsPage />}
        />

        <Route
          path="data-quality"
          element={<DataQualityPage />}
        />

        <Route
          path="settings"
          element={<SettingsPage />}
        />
        <Route
          path="workspaces"
          element={<WorkspacesPage />}
        />

        <Route
        path="workspaces/:workspaceId"
        element={<WorkspaceDetailPage />}
        
      >
        <Route
  path="datasets/:datasetId"
  element={<DatasetDetailPage />}
    >
      <Route
        index
        element={<DatasetOverviewPage />}
      />

      <Route
        path="files"
        element={<DatasetFilesPage />}
      />

      <Route path="contract" element={<DatasetContractPage />} />

      <Route
        path="rules"
        element={<DatasetRulesPage />}
      />

      <Route
        path="processing"
        element={<DatasetProcessingPage />}
      />

      <Route
        path="data-quality"
        element={<DatasetDataQualityPage />}
      />

      <Route
        path="analytics"
        element={<DatasetAnalyticsPage />}
      />

      <Route
        path="history"
        element={<DatasetHistoryPage />}
      />
    </Route>
        <Route
          index
          element={<WorkspaceOverviewPage />}
        />

        <Route
          path="datasets"
          element={<WorkspaceDatasetsPage />}
        />

        <Route
          path="processing"
          element={<WorkspaceProcessingPage />}
        />

        <Route
          path="data-quality"
          element={<WorkspaceDataQualityPage />}
        />

        <Route
          path="analytics"
          element={<WorkspaceAnalyticsPage />}
        />
      </Route>
            </Routes>
    </AppShell>
  )
}

function App() {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <Navigate
            to="/app"
            replace
          />
        }
      />

      <Route
        path="/login"
        element={<LoginPage />}
      />

      <Route
        path="/signup"
        element={<SignupPage />}
      />

      <Route
        path="/app/*"
        element={<AppLayout />}
        
      />

      <Route
        path="*"
        element={
          <Navigate
            to="/"
            replace
          />
        }
      />
    </Routes>
  )
}

export default App