// Shapes of the FORGE API's read models (src/forge/api). Read-only views.

export type Uuid = string;

export interface EventView {
  id: Uuid;
  event_type: string;
  aggregate_type: string;
  aggregate_id: Uuid;
  sequence: number;
  payload: Record<string, unknown>;
  correlation_id: string | null;
  causation_id: Uuid | null;
  occurred_at: string;
}

export interface TaskView {
  id: Uuid;
  key: string;
  kind: string;
  status: string;
  agent_id: Uuid | null;
  dependencies: Uuid[];
  required_capabilities: string[];
  allowed_tools: string[];
  attempts: number;
  max_attempts: number;
}

export interface ExecutionView {
  id: Uuid;
  task_id: Uuid;
  status: string;
  attempt: number;
  agent_version_id: Uuid;
  model_provider: string;
  model_name: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  cost_micro_usd: number;
  cache_hit_rate: number;
  output: string | null;
  failure_kind: string | null;
  failure_detail: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface MissionView {
  id: Uuid;
  status: string;
  objective: string;
  budget_micro_usd: number;
  deadline: string | null;
  max_risk: string;
  correlation_id: string;
  created_at: string;
  updated_at: string;
  tasks: TaskView[];
  executions: ExecutionView[];
}

export interface MissionSummary {
  id: Uuid;
  objective: string;
  status: string;
  created_at: string;
  updated_at: string;
  cost_micro_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  tasks_total: number;
  tasks_completed: number;
}

export interface Finding {
  code: string;
  message: string;
  refs: string[];
}

export interface ResultView {
  scope: string;
  task_id: Uuid | null;
  agent_id: Uuid | null;
  execution_id: Uuid | null;
  dimension: string;
  score: number | null;
  confidence: number;
  evaluator: string;
  findings: Finding[];
}

export interface EvaluationView {
  id: Uuid;
  mission_id: Uuid;
  evaluator_set: string;
  benchmark_case: string | null;
  cost_micro_usd: number;
  created_at: string;
  results: ResultView[];
}

export interface CompetencyView {
  capability: string;
  estimated_score: number;
  uncertainty: number;
  sample_count: number;
  effective_samples: number;
  recent_trend: number | null;
  calibration: number | null;
  last_evaluated: string | null;
  supporting_evaluations: Uuid[];
}

export interface AgentSummary {
  id: Uuid;
  name: string;
  version: number;
  version_id: Uuid;
  role: string;
  model: string;
  fallback_models: string[];
  tools: string[];
  capabilities: string[];
  skills: string[];
  created_at: string;
}

export interface AgentDetail extends AgentSummary {
  instructions: string;
  competencies: CompetencyView[];
  reliability: {
    estimate: number;
    uncertainty: number;
    successes: number;
    failures: number;
    mean_cost_micro_usd: number | null;
    mean_latency_ms: number | null;
  };
}

export interface Count {
  key: string;
  count: number;
}

export interface ModelUsage {
  provider: string;
  model: string;
  calls: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  cache_hit_rate: number;
  cost_micro_usd: number;
  cache_savings_micro_usd: number;
}

export interface ToolUsage {
  tool: string;
  calls: number;
  errors: number;
  failures: number;
  denials: number;
  reinvocations: number;
}

export interface CostShare {
  key: string;
  cost_micro_usd: number;
  executions: number;
}

export interface Overview {
  since: string | null;
  total_cost_micro_usd: number;
  pending_approvals: number;
  missions_by_status: Count[];
  executions_by_status: Count[];
  failures_by_kind: Count[];
  models: ModelUsage[];
  tools: ToolUsage[];
  cost_by_agent: CostShare[];
  cost_by_day: CostShare[];
}

export interface AgentUsage {
  agent_id: Uuid;
  executions: number;
  cost_micro_usd: number;
  models: ModelUsage[];
  failures_by_kind: Count[];
  dimensions: { dimension: string; mean: number; samples: number }[];
  findings: Count[];
}

export interface Capability {
  name: string;
  description: string;
  parent: string | null;
  prerequisites: string[];
  related: string[];
  required_tools: string[];
}

export interface SkillView {
  name: string;
  version: number;
  capability: string;
  status: string;
  instructions: string;
  provenance: Record<string, unknown>;
  validation: Record<string, unknown>;
  created_at: string;
}

export interface CycleView {
  id: Uuid;
  agent_id: Uuid;
  capability: string;
  status: string;
  skill: string | null;
  learning_delta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BenchmarkView {
  name: string;
  version: number;
  capability: string;
  description: string;
  cases: { key: string; objective: string; difficulty: number; required_capabilities: string[] }[];
}

export interface RunView {
  id: Uuid;
  benchmark: string;
  version: number;
  agent_id: Uuid;
  status: string;
  summary: Record<string, unknown>;
  created_at: string;
  completed_at: string | null;
  missions: Record<string, Uuid>;
}

export interface Rule {
  effect: string;
  tools: string[];
  agents: string[];
  conditions: { argument: string; operator: string; value: unknown }[];
  reason: string;
}

export interface PolicyView {
  name: string;
  version: number;
  rules: Rule[];
  created_by: string;
  created_at: string;
}

export interface ApprovalView {
  id: Uuid;
  mission_id: Uuid;
  task_id: Uuid;
  execution_id: Uuid;
  requesting_agent_id: Uuid;
  tool: string;
  arguments: Record<string, unknown>;
  arguments_sha256: string;
  rationale: string;
  evidence: string[];
  affected_resources: string[];
  risk: string;
  status: string;
  expires_at: string;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string;
}

export interface AuditView {
  actor: string;
  actor_type: string;
  action: string;
  decision: string;
  occurred_at: string;
  agent_id: Uuid | null;
  mission_id: Uuid | null;
  tool: string | null;
  target: string | null;
  approval_id: Uuid | null;
  reason: string;
  arguments_sha256: string | null;
}

export interface ToolView {
  name: string;
  pinned_version: string;
  pinned_sha256: string;
  status: string;
  reason: string;
  updated_at: string;
}
