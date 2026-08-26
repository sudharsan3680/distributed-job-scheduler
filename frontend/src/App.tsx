import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import LoginPage from "./pages/LoginPage";
import ProjectSetupPage from "./pages/ProjectSetupPage";
import OverviewPage from "./pages/OverviewPage";
import QueuesPage from "./pages/QueuesPage";
import JobsPage from "./pages/JobsPage";
import WorkersPage from "./pages/WorkersPage";
import DlqPage from "./pages/DlqPage";
import SchedulesPage from "./pages/SchedulesPage";

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

function Home() {
  const projectId = localStorage.getItem("project_id");
  return <Navigate to={projectId ? "/dashboard" : "/select-project"} replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/select-project" element={<RequireAuth><ProjectSetupPage /></RequireAuth>} />
          <Route path="/dashboard" element={<RequireAuth><OverviewPage /></RequireAuth>} />
          <Route path="/dashboard/queues" element={<RequireAuth><QueuesPage /></RequireAuth>} />
          <Route path="/dashboard/jobs" element={<RequireAuth><JobsPage /></RequireAuth>} />
          <Route path="/dashboard/workers" element={<RequireAuth><WorkersPage /></RequireAuth>} />
          <Route path="/dashboard/schedules" element={<RequireAuth><SchedulesPage /></RequireAuth>} />
          <Route path="/dashboard/dlq" element={<RequireAuth><DlqPage /></RequireAuth>} />
          <Route path="/" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
