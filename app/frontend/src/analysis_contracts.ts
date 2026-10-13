/**
 * 服务端分析 API 的只读响应契约。
 *
 * 本文件只描述前端渲染需要的数据形状，不包含任何指标计算、数据持久化或业务规则。
 */

export type UnknownRecord = Record<string, unknown>;

export interface MoveRecord extends UnknownRecord {
  MoveID?: number;
  MoveType?: number;
  ModuleName?: string;
  StartTime?: number;
  EndTime?: number;
}

export interface DeviceDefinition {
  Stations?: Record<string, UnknownRecord>;
  Robots?: Record<string, UnknownRecord>;
}

export type PerformanceWindowMode = "steady" | "full";
export type ActivityCategory = "process" | "clean" | "door" | "transfer" | "environment" | "other";
export type ResourceKind = "robot" | "process" | "loadlock" | "loadport" | "auxiliary";
export type BottleneckConfidence = "high" | "medium" | "low";
export type BottleneckCandidateKind = "process-group" | "robot" | "loadlock-group";

export interface ScheduleAnalysisContext {
  processStages?: Array<{
    id: string;
    label?: string;
    pjobName?: string;
    stepId?: string | number;
    resourceNames: string[];
  }>;
}

export interface PerformanceWindow {
  mode: PerformanceWindowMode;
  method: "steady-overlap" | "middle-approximation" | "full";
  start: number;
  end: number;
  duration: number;
  scheduleStart: number;
  scheduleEnd: number;
  trimmedStart: number;
  trimmedEnd: number;
  label: string;
  detail: string;
}

export interface DurationMetricSummary {
  totalSeconds: number;
  meanSeconds: number;
  medianSeconds: number;
  maxSeconds: number;
  coefficientOfVariation: number;
  sampleCount: number;
}

export interface WaferResidenceTime {
  wafer: string;
  enteredAt: number;
  completedAt: number;
  duration: number;
  chamberDwellSeconds?: number;
  robotDwellSeconds?: number;
}

export interface ThroughputTimelinePoint {
  wafer: string;
  completedWaferIndex: number;
  completedAt: number;
  throughputPerHour: number;
}

export interface ResourcePerformance {
  name: string;
  type: string;
  kind: ResourceKind;
  utilization: number;
  busyTime: number;
  averageActivePeriod: number;
  longestActivePeriod: number;
  longestIdlePeriod: number;
  activePeriodCount: number;
  categoryTimes: Record<ActivityCategory, number>;
  isBottleneck: boolean;
  bottleneckCandidateRank: number | null;
}

export interface BottleneckCandidate {
  id: string;
  label: string;
  kind: BottleneckCandidateKind;
  resourceNames: string[];
  utilization: number;
  continuity: number;
  score: number;
  confidence: BottleneckConfidence;
  evidence: string[];
}

export interface SchedulePerformance {
  window: PerformanceWindow;
  resources: ResourcePerformance[];
  bottleneckCandidates: BottleneckCandidate[];
  primaryBottleneck: BottleneckCandidate | null;
  bottleneck: ResourcePerformance | null;
  completedWaferCount: number;
  throughputPerHour: number;
  throughputSampleCount: number;
  throughputReason: string;
  throughputTimeline?: {
    rollingWindowMinimum: number;
    rollingWindowMaximum: number;
    cumulative: ThroughputTimelinePoint[];
    rollingByWindow: Record<string, ThroughputTimelinePoint[]>;
  };
  cpuTimeMs: number | null;
  recomputeCount: number;
  averageRecomputeTimeMs: number | null;
  meanDepartureInterval: number;
  departureIntervalCv: number;
  processChamberDwellTime: DurationMetricSummary;
  robotWaferDwellTime: DurationMetricSummary;
  waferSystemResidenceTime: DurationMetricSummary;
  waferSystemResidenceTimes: WaferResidenceTime[];
  loadLockEfficiency: {
    cycleCount: number;
    waferCycleCount: number;
    wafersPerCycle: number;
    fullLoadCycleCount: number;
    emptyLoadCycleCount: number;
    fullLoadCycleRatio: number;
    emptyLoadCycleRatio: number;
  };
}

export interface BottleneckUtilizationSummary {
  resourceName: string;
  utilization: number;
  windowLabel: string;
  confidence: BottleneckConfidence;
  candidateCount: number;
  score: number;
}

export interface TestGroupPerformanceSummary {
  cases: Array<Record<string, any>>;
  totalCount: number;
  succeededCount: number;
  failedCount: number;
  metricsCount: number;
  selectedMetricGroups?: string[];
  selectedMetricIds?: string[];
  cacheHitCount?: number;
  timedOut?: boolean;
  validationPassedCount: number;
  validationPassRate: number;
  comparableCount: number;
  winCount: number;
  tieCount: number;
  regressionCount: number;
  weightedImprovementPercent: number | null;
  medianImprovementPercent: number | null;
  worstRegressionPercent: number | null;
  medianCpuTimeMs: number | null;
  medianAverageRecomputeTimeMs?: number | null;
  p90CpuTimeMs: number | null;
  totalCpuTimeMs: number;
  medianBottleneckUtilization: number | null;
  medianThroughputPerHour: number | null;
  throughputEligibleCount: number;
  medianDepartureIntervalCv: number | null;
  medianProcessChamberDwellMeanSeconds: number | null;
  medianRobotWaferDwellMeanSeconds: number | null;
  medianWaferSystemResidenceMeanSeconds: number | null;
  medianWaferSystemResidenceCv: number | null;
  medianLoadLockWafersPerCycle?: number | null;
  medianLoadLockFullCycleRatio?: number | null;
  medianLoadLockEmptyCycleRatio?: number | null;
  bottleneckFrequencies: Array<{
    resourceName: string;
    count: number;
    share: number;
    medianUtilization: number;
  }>;
  windowMethodCounts: Record<string, number>;
}

/**
 * 理论产能接口契约：只读展示所需的形状。
 *
 * 服务端固定使用无清洁的 Cycle 固定周期事件图；`status` 为 `unsupported` 或
 * `error` 时只提供稳定 `reason` 与中文 `detail`，不提供曲线。
 */
export type TheoryCapacityRouteStatus = "supported" | "unsupported" | "error";
export type TheoryCapacityLimitingKind = "resource" | "waiting-chain";
export type TheoryCapacityTargetKind = "test" | "route";

/** 路径类型、LoadLock 类型与清洁类型的稳定取值。 */
export const THEORY_ROUTE_KINDS = [
  "single-node", "parallel", "serial", "mixed", "reentrant",
] as const;
export const THEORY_LOCK_MODES = [
  "shared-bidirectional", "dedicated-entry-exit", "single-bidirectional",
] as const;
export const THEORY_CLEAN_TYPES = [
  "preclean", "postclean", "wacclean", "dummy", "dummywac", "none",
] as const;

export type TheoryCapacityRouteKind = typeof THEORY_ROUTE_KINDS[number];
export type TheoryCapacityLockMode = typeof THEORY_LOCK_MODES[number];
export type TheoryCapacityCleanType = typeof THEORY_CLEAN_TYPES[number];

export interface TheoryCapacityProcessNode {
  ordinal: number;
  alias: string;
  chambers: string[];
}

export interface TheoryCapacityFacts {
  route_name: string;
  lock_mode: string;
  route_kind: string;
  pressure_domains: string[];
  vacuum_robot: string;
  atmosphere_robot: string;
  process_nodes: TheoryCapacityProcessNode[];
  rounds_per_cycle: number;
  units_per_cycle: number;
  physical_members_per_unit: number;
  loadlock_batch_size: number;
  chamber_counts: number[];
  source_station: string;
  sink_station: string;
  inbound_relays: string[];
  outbound_relays: string[];
  assumptions?: string[];
}

/** 扫描曲线按列传输：同一字段的 31 个采样点共用一个数组。 */
export interface TheoryCapacityScanColumns {
  process_seconds: number[];
  period_seconds: number[];
  seconds_per_unit: number[];
  physical_wafers_per_hour: number[];
  limiting_kind: TheoryCapacityLimitingKind[];
}

export interface TheoryCapacityRouteEntry {
  route_name: string;
  group: string;
  status: TheoryCapacityRouteStatus;
  reason: string;
  detail: string;
  group_key: string;
  facts: TheoryCapacityFacts | null;
  points: Partial<TheoryCapacityScanColumns>;
}

/**
 * 等价路线分组：路径形态与设备计时都相同的路径模板合并为一张卡片。
 *
 * 例如单节点路线 ``PM1`` 与 ``PM3`` 的取片准备时间相同，合并展示；``PM2`` 因计时不同
 * 单独成组。分组键与扫描区间无关，只由形态与设备计时决定。
 */
export interface TheoryCapacityRouteGroup {
  group_key: string;
  route_kind: string;
  lock_mode: string;
  rounds_per_cycle: number | null;
  units_per_cycle: number | null;
  chamber_counts: number[];
  pressure_domains: string[];
  route_names: string[];
  representative: string;
  start_wafers_per_hour: number | null;
  end_wafers_per_hour: number | null;
  best_wafers_per_hour: number | null;
  matched_tests: number;
}

/** 一个测试换算出的曲线数据点；不可推导时只带稳定原因。 */
export interface TheoryCapacityTestPoint {
  id: string;
  name: string;
  group: string;
  clean_types: string[];
  route_name: string;
  group_key: string;
  status: TheoryCapacityRouteStatus;
  reason: string;
  detail: string;
  process_seconds: number[];
  key_process_seconds: number | null;
  period_seconds: number | null;
  seconds_per_unit: number | null;
  physical_wafers_per_hour: number | null;
  limiting_kind: TheoryCapacityLimitingKind | "";
  limiting_label: string;
}

/** 筛选器可选值与数量；由路线分类索引汇总。 */
export interface TheoryCapacityIndexCounts {
  value: string;
  count: number;
}

export interface TheoryCapacityRouteIndex {
  route_kind: TheoryCapacityIndexCounts[];
  lock_mode: TheoryCapacityIndexCounts[];
  route_total: number;
  route_supported: number;
}

export interface TheoryCapacityRouteIndexEntry {
  route_name: string;
  group: string;
  route_kind: string;
  lock_mode: string;
  status: TheoryCapacityRouteStatus;
  reason: string;
  detail: string;
}

export interface TheoryCapacityRouteIndexResponse {
  ok: boolean;
  device: { id: string; name: string; physical_members_per_unit: number };
  route_index: TheoryCapacityRouteIndex;
  routes: TheoryCapacityRouteIndexEntry[];
}

export interface TheoryCapacityScanResponse {
  ok: boolean;
  device: { id: string; name: string; physical_members_per_unit: number };
  scan: { first_seconds: number; last_seconds: number; step_seconds: number };
  filters: { kinds: string[]; locks: string[]; cleans: string[] };
  route_index: TheoryCapacityRouteIndex;
  routes: TheoryCapacityRouteEntry[];
  groups: TheoryCapacityRouteGroup[];
  tests: TheoryCapacityTestPoint[];
  test_total: number;
  test_truncated: boolean;
}

export interface TheoryCapacityServiceBreakdown {
  key: string;
  category: string;
  station: string;
  robot: string;
  seconds: number;
  detail: string;
  parts: Array<{ label: string; seconds: number }>;
}

export interface TheoryCapacityWaitingEdge {
  source: string;
  target: string;
  minimum_delay_seconds: number;
  cycle_shift: number;
  label: string;
  detail: string;
  parts: Array<{ label: string; seconds: number }>;
}

export interface TheoryCapacityDerivation {
  /** Cycle 批次结果独立于稳态周期图，包含完整启动与清洁收尾。 */
  model_kind?: "cycle-batch" | "cycle-periodic";
  /** 连续生产中段的重复窗口证据；Job 相位跨期时按 divisor 换算。 */
  period_timing?: {basis: "job" | "wac"; output_wafers: number; phase_count: number;
    start_seconds: number; end_seconds: number; full_period_seconds: number; divisor: number;
    phase_factors?: number[]; candidate_rounds?: number; physical_members_per_unit?: number;
    verified_repeats: number; reference: string; resource: string;
    segments: Array<{label: string; seconds: number; terms?: Array<{count: number; seconds: number}>}>};
  batch_wafers?: number;
  job_count?: number;
  wafers_per_job?: number;
  alternation?: "continuous" | "drain";
  baseline_wafers_per_hour?: number;
  baseline_seconds?: number;
  capacity_change_percent?: number;
  scope_note?: string;
  physical_validation_passed?: boolean;
  loadlock_batch_size?: number;
  cleaning_summary?: Array<{station: string; task: string; count: number; seconds: number}>;
    /** 按真实产品回盒事件拆分启动、续片和收尾；重复批次只用于逐批排空。 */
    batch_timing?: {first_return_seconds: number; remaining_returns_seconds: number; tail_seconds: number;
      repeat_count: number; return_intervals: Array<{seconds: number; count: number}>};
    cleaning_types?: string[];
  route_name: string;
  group: string;
  pjob_name: string;
  status: TheoryCapacityRouteStatus;
  reason: string;
  detail: string;
  process_seconds?: number[];
  facts?: TheoryCapacityFacts;
  model_name?: string;
  events?: string[];
  edges?: TheoryCapacityWaitingEdge[];
  services?: TheoryCapacityServiceBreakdown[];
  resource_work_seconds?: Record<string, number>;
  critical_chain?: Array<Omit<TheoryCapacityWaitingEdge, "parts">>;
  period_seconds?: number;
  seconds_per_unit?: number;
  physical_wafers_per_hour?: number;
  resource_period_lower_bound_seconds?: number;
  resource_upper_bound_wafers_per_hour?: number | null;
  limiting_kind?: TheoryCapacityLimitingKind;
  limiting_label?: string;
}

export interface TheoryCapacityDerivationResponse {
  ok: boolean;
  device: { id: string; name: string; physical_members_per_unit: number };
  target: { kind: TheoryCapacityTargetKind; id: string; name: string; group: string };
  derivations: TheoryCapacityDerivation[];
}
