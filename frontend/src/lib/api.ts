import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export interface User { id: number; email: string; full_name: string; is_active: boolean }
export interface Project { id: number; name: string; slug: string; created_at: string; api_key?: string }
export interface Queue {
  id: number; name: string; priority: number; max_concurrency: number;
  is_paused: boolean; rate_limit_per_minute: number | null; created_at: string; updated_at: string;
}
export interface QueueStats {
  queue_id: number; name: string; queued: number; scheduled: number; claimed: number; running: number;
  completed: number; failed: number; dead_letter: number; is_paused: boolean; max_concurrency: number;
  current_in_flight: number; throughput_last_hour: number;
}
export interface Job {
  id: number; queue_id: number; job_type: string; payload: Record<string, unknown>; status: string;
  priority: number; run_at: string; attempt_count: number; max_attempts: number;
  claimed_by_worker_id: number | null; scheduled_job_id: number | null; batch_id: number | null;
  depends_on_job_id: number | null; created_at: string; updated_at: string; completed_at: string | null;
}
export interface JobExecution {
  id: number; attempt_number: number; status: string; worker_id: number | null;
  started_at: string; finished_at: string | null; duration_ms: number | null; error_message: string | null;
}
export interface JobDetail extends Job { executions: JobExecution[]; logs: { id: number; level: string; message: string; created_at: string }[] }
export interface WorkerOut {
  id: number; hostname: string; label: string; status: string; concurrency_capacity: number;
  current_load: number; last_heartbeat_at: string | null; started_at: string;
}
export interface SystemHealth {
  total_queues: number; total_workers_online: number; jobs_queued: number; jobs_running: number;
  jobs_completed_last_hour: number; jobs_failed_last_hour: number; dead_letter_count: number;
}

export const auth = {
  register: (body: { email: string; password: string; full_name: string; organization_name: string }) =>
    api.post("/auth/register", body).then((r) => r.data),
  login: (body: { email: string; password: string }) => api.post("/auth/login", body).then((r) => r.data),
};

export const projects = {
  list: (orgId: number) => api.get<Project[]>(`/organizations/${orgId}/projects`).then((r) => r.data),
  create: (orgId: number, body: { name: string; slug: string }) =>
    api.post<Project>(`/organizations/${orgId}/projects`, body).then((r) => r.data),
};

export const queues = {
  list: (projectId: number) => api.get<Queue[]>(`/projects/${projectId}/queues`).then((r) => r.data),
  create: (projectId: number, body: Record<string, unknown>) =>
    api.post<Queue>(`/projects/${projectId}/queues`, body).then((r) => r.data),
  pause: (projectId: number, id: number) => api.post(`/projects/${projectId}/queues/${id}/pause`).then((r) => r.data),
  resume: (projectId: number, id: number) => api.post(`/projects/${projectId}/queues/${id}/resume`).then((r) => r.data),
  stats: (projectId: number, id: number) => api.get<QueueStats>(`/projects/${projectId}/queues/${id}/stats`).then((r) => r.data),
};

export interface JobPage { items: Job[]; total: number; page: number; page_size: number }

export const jobs = {
  list: (projectId: number, queueId: number, params: Record<string, unknown> = {}) =>
    api.get<JobPage>(`/projects/${projectId}/queues/${queueId}/jobs`, { params }).then((r) => r.data),
  get: (projectId: number, jobId: number) => api.get<JobDetail>(`/projects/${projectId}/jobs/${jobId}`).then((r) => r.data),
  create: (projectId: number, queueId: number, body: Record<string, unknown>) =>
    api.post<Job>(`/projects/${projectId}/queues/${queueId}/jobs`, body).then((r) => r.data),
  cancel: (projectId: number, jobId: number) => api.post(`/projects/${projectId}/jobs/${jobId}/cancel`).then((r) => r.data),
  retry: (projectId: number, jobId: number) => api.post(`/projects/${projectId}/jobs/${jobId}/retry`).then((r) => r.data),
  dlq: (projectId: number) => api.get<Job[]>(`/projects/${projectId}/dead-letter-queue`).then((r) => r.data),
};

export const dashboard = {
  health: (projectId: number) => api.get<SystemHealth>(`/projects/${projectId}/dashboard/health`).then((r) => r.data),
  queueStats: (projectId: number) => api.get<QueueStats[]>(`/projects/${projectId}/dashboard/queues`).then((r) => r.data),
  workers: (projectId: number) => api.get<WorkerOut[]>(`/projects/${projectId}/dashboard/workers`).then((r) => r.data),
};

export interface ScheduledJob {
  id: number; name: string; job_type: string; cron_expression: string;
  timezone: string; is_active: boolean; next_run_at: string | null; last_run_at: string | null;
}

export const schedules = {
  list: (projectId: number, queueId: number) =>
    api.get<ScheduledJob[]>(`/projects/${projectId}/queues/${queueId}/scheduled-jobs`).then((r) => r.data),
  create: (projectId: number, queueId: number, body: Record<string, unknown>) =>
    api.post<ScheduledJob>(`/projects/${projectId}/queues/${queueId}/scheduled-jobs`, body).then((r) => r.data),
  pause: (projectId: number, id: number) =>
    api.post(`/projects/${projectId}/scheduled-jobs/${id}/pause`).then((r) => r.data),
};
