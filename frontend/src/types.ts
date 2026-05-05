export type ViewMode = "search" | "admin";

export type AuthMode = "login" | "register";

export type JobStatus = "running" | "succeeded" | "failed";

export interface HealthResponse {
  status: string;
  database: string;
}

export interface RepositoryResponse {
  id: number;
  name: string;
  oai_endpoint: string;
  last_harvest: string | null;
  refresh_interval: number | null;
}

export interface RepositoryWritePayload {
  name: string;
  oai_endpoint: string;
  refresh_interval: number | null;
}

export interface StatsResponse {
  repositories: number;
  publications: number;
  publications_with_embeddings: number;
  last_harvest: string | null;
}

export interface SearchPlan {
  embedding_queries: string[];
  semantic_query: string;
  topic_phrases: string[];
  year_from: number | null;
  year_to: number | null;
  ranking_phrases: string[];
  interpreted_query: string;
  used_fallback: boolean;
}

export interface SearchResult {
  id: number;
  title: string | null;
  abstract: string | null;
  source_url: string | null;
  date: string | null;
  cosine_distance: number;
  cosine_similarity: number;
  topic_boost: number;
  ranking_boost: number;
  coverage_boost: number;
  score: number;
  repository: string | null;
  authors: string[];
  matched_query: string;
  matched_queries: string[];
  best_rank: number;
}

export interface SearchResponse {
  query: string;
  limit: number;
  plan: SearchPlan;
  results: SearchResult[];
  total: number;
}

export interface AdminUser {
  id: number;
  username: string;
}

export interface AuthResponse {
  expires_in: number;
  admin: AdminUser;
}

export interface HarvestJob {
  id?: number | null;
  job_type?: "repository_harvest" | "embedding_backfill" | null;
  repository_id?: number | null;
  status: JobStatus;
  started_at: string | null;
  finished_at: string | null;
  processed_records: number | null;
  message: string;
}

export interface AdminRepositoryResponse extends RepositoryResponse {
  harvest_job: HarvestJob | null;
}

export interface EmbeddingStatusResponse {
  missing_embeddings: number;
  embedding_job: HarvestJob | null;
}
