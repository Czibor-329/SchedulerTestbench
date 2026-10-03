/**
 * Node 回归测试入口。
 *
 * 浏览器生产入口只构建 ``config_editor.ts``。本入口仅导出回放和渲染测试需要的
 * 前端视图函数；MoveList 指标与测试组统计必须由服务端 `/api/analysis/*` 提供。
 */

export {
  buildWorkspaceSnapshot,
  createVisualizationWorkspace,
  decisionAtTime,
  decisionBoundaryTimes,
  decisionSpaceSignature,
  detectTerminalPlaybackDeadlock,
  detectDeviceTopologyLayout,
  detectTopologyLayout,
  groupedBottleneckResources,
  normalizeDecisionTrace,
  normalizeLoadPortReplenishments,
  normalizeMovePayload,
  normalizeReplayLogPayload,
  primitiveDecisionBoundaryTimes,
  renderEquipmentTopology,
  renderFrontSlotOverview,
  renderDecisionLens,
  renderSchedulePerformance,
  renderThroughputChart,
  simplifyThroughputPoints,
  renderWaferResidenceChart,
  snapshotWithFullDeviceModules,
} from "./workspace_visualizer";

export { configuredRobotArms, robotArmAnimation, robotSlotWafers, robotTransferReach, renderParallelRobotArms, robotArmGeometry } from "./topology_robot_mechanism";
export { atmosphereRailMotion } from "./topology_atmosphere_rail";
export { completedThroughputCount, updateReplayThroughput } from "./replay_throughput";
export { isAnalysisViewVisible, mountAnalysisWorkspace } from "./analysis_workspace";
export { mountReplayInspectorDock, setReplayDockExpanded, setReplayInspectorExpanded } from "./replay_inspector_dock";
export { projectTopologyTransfers } from "./topology_transfer_projection";
export { waferDispatchProgress, renderWaferDispatchProgress, updateWaferProgressPanel } from "./wafer_dispatch_progress";
export { testGroupSummaryCsv } from "./group_analysis_view";
export { movelistResultSourceUrl } from "./result_artifact_urls";
export { projectReplayWaferDestinations, replayMaterialInstanceKey, replayMoveMaterials, replayWaferWaitingSeconds } from "./replay_wafer_destinations";
export { replayObjectKey, replayObjectIsCurrent, replayObjectEvents, replayMoveMatchesObject, replayActionMatchesObject, replayObjectGanttUrl,
  projectReplayObjectDetails, renderReplayObjectDetails, applyReplayObjectSelection, ReplayObjectInspectorController } from "./replay_object_inspector";
export { annotateReplayTopology } from "./topology_scene_annotations";
export { replayLogContext, replayCommittedGenerations } from "./replay_log_context";
