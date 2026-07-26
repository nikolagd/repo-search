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
  received_records?: number | null;
  parsed_records?: number | null;
  skipped_records?: number | null;
  deleted_records?: number | null;
  pages_processed?: number | null;
  message: string;
}

export interface AdminRepositoryResponse extends RepositoryResponse {
  harvest_job: HarvestJob | null;
}

export interface EmbeddingStatusResponse {
  missing_embeddings: number;
  embedding_job: HarvestJob | null;
}

export type ModelObservabilityWindow = "15m" | "1h" | "6h" | "24h" | "7d" | "15d";

export interface ModelObservabilityCard {
  label: string;
  value: number;
  unit: "count" | "percent" | "seconds";
}

export interface RetrievalStageLatency {
  stage: string;
  p95_seconds: number;
}

export interface ParserModeCount {
  parser_mode: string;
  count: number;
}

export interface RepositoryIndexStats {
  repository: string;
  publications: number;
  publications_with_embeddings: number;
  missing_embeddings: number;
}

export interface ModelObservabilityResponse {
  window: ModelObservabilityWindow;
  generated_at: string;
  model_config: {
    llm_provider?: string;
    llm_model?: string;
    llm_url?: string;
    llm_timeout_seconds?: number;
    embedding_model?: string;
    embedding_model_revision?: string;
    embedding_template_version?: string;
    embedding_device?: string;
    embedding_dimension?: number | null;
    ranking_config?: Record<string, number>;
  };
  index: {
    indexed_publications: number;
    publications_with_embeddings: number;
    missing_embeddings: number;
    embedding_coverage_ratio: number;
    repositories: RepositoryIndexStats[];
  };
  cards: ModelObservabilityCard[];
  retrieval_output: {
    avg_result_count: number;
    avg_candidates: number;
    avg_embedding_queries: number;
    avg_top_score: number;
    avg_score: number;
  };
  stage_latency: RetrievalStageLatency[];
  parser_modes: ParserModeCount[];
}
