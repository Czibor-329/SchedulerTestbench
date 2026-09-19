var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/route_editor_logic.ts
var route_editor_logic_exports = {};
__export(route_editor_logic_exports, {
  VISIT_SHARED_FIELDS: () => VISIT_SHARED_FIELDS,
  attachRoutePreviewCleanTokens: () => attachRoutePreviewCleanTokens,
  automaticRouteName: () => automaticRouteName,
  automaticTemplateName: () => automaticTemplateName,
  cloneVisitParameters: () => cloneVisitParameters,
  compactRoutePreviewPath: () => compactRoutePreviewPath,
  compareProfiles: () => compareProfiles,
  differenceFields: () => differenceFields,
  formatRoutePreviewCleanToken: () => formatRoutePreviewCleanToken,
  minimumResidencyConstraint: () => minimumResidencyConstraint,
  normalizeStageProcessRecipes: () => normalizeStageProcessRecipes,
  processProfile: () => processProfile,
  processRecipeName: () => processRecipeName,
  replaceCandidates: () => replaceCandidates,
  routeCleanSignature: () => routeCleanSignature,
  routeReferencedCleanNames: () => routeReferencedCleanNames,
  selectReferencedRoutes: () => selectReferencedRoutes,
  synchronizeVisits: () => synchronizeVisits
});
module.exports = __toCommonJS(route_editor_logic_exports);
var TRANSFER_ROBOT_NAME = /^(?:ATR|VTR|DBR|UBR|TM|VTM|EFEM)(?:[_-]?\d+)?$/i;
var LEFT_CLEAN_ORDER = ["dummy", "dummywac", "preclean"];
var VISIT_SHARED_FIELDS = [
  "processTime",
  "recipeTime",
  "processRecipe",
  "processType",
  "slotIds",
  "weight",
  "moveTimeOffset",
  "qTimeLimit",
  "residencyConstraint",
  "beforeCleanRefs",
  "afterCleanRefs"
];
function cloneValue(value) {
  return value === void 0 ? value : structuredClone(value);
}
function cloneVisitParameters(visit) {
  return Object.fromEntries(
    VISIT_SHARED_FIELDS.map((key) => [key, cloneValue(visit?.[key])])
  );
}
function processProfile(route) {
  const processStages = (route.stages || []).filter((stage) => stage.needProcess);
  const candidateGroups = processStages.map((stage) => [
    ...new Set((stage.visits || []).map((visit) => String(visit.stationName || "").trim()).filter(Boolean))
  ]);
  const counts = candidateGroups.map((candidates) => candidates.length);
  const candidatePath = candidateGroups.map(
    (candidates) => candidates.join("/") || "\u672A\u9009\u62E9\u8154\u5BA4"
  );
  const processTimes = processStages.map(
    (stage) => Number(stage.visits?.[0]?.processTime ?? stage.visits?.[0]?.recipeTime ?? 0)
  );
  const processCount = processStages.length;
  const candidateOccurrences = candidateGroups.flat();
  const isReentrant = new Set(candidateOccurrences).size < candidateOccurrences.length;
  return {
    processCount,
    counts,
    candidatePath,
    processTimes,
    isReentrant,
    processLabel: isReentrant ? "\u91CD\u5165\u7EC4" : processCount === 0 ? "\u65E0\u52A0\u5DE5\u5DE5\u5E8F" : `${processCount} \u9053\u5DE5\u5E8F`,
    label: isReentrant ? "\u91CD\u5165\u8DEF\u5F84" : processCount === 0 ? "(0)" : `(${counts.join(", ")})`,
    key: isReentrant ? "reentrant" : processCount === 0 ? "0:none" : `${processCount}:${counts.join(",")}`
  };
}
function formatSeconds(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Number.isInteger(number) ? number : number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}s` : "\u672A\u8BBE\u7F6E";
}
function cleanNames(value) {
  const rows = Array.isArray(value) ? value : value ? [value] : [];
  return [...new Set(rows.map((item) => String(item || "").trim()).filter(Boolean))];
}
function routeReferencedCleanNames(route) {
  return [.../* @__PURE__ */ new Set([
    ...cleanNames(route.prePJobCleanRefs),
    ...cleanNames(route.postPJobCleanRefs),
    ...cleanNames(route.postCJobCleanRefs),
    ...(route.stages || []).flatMap((stage) => (stage.visits || []).flatMap((visit) => [
      ...cleanNames(visit.beforeCleanRefs),
      ...cleanNames(visit.afterCleanRefs)
    ]))
  ])];
}
function routeLevelCleanNames(route) {
  return /* @__PURE__ */ new Set([
    ...cleanNames(route.prePJobCleanRefs),
    ...cleanNames(route.postPJobCleanRefs),
    ...cleanNames(route.postCJobCleanRefs)
  ]);
}
function stageVisitCleanNames(stage) {
  return new Set((stage.visits || []).flatMap((visit) => [
    ...cleanNames(visit.beforeCleanRefs),
    ...cleanNames(visit.afterCleanRefs)
  ]));
}
function previewCleanType(clean) {
  const value = String(clean.cleanType || "").toLowerCase().replace(/[-_\s]/g, "");
  if (value === "dummyclean") return "dummy";
  if (value === "dummywacclean") return "dummywac";
  if (value === "preclean" || value === "postclean" || value === "wacclean" || value === "dummy" || value === "dummywac") {
    return value;
  }
  return "";
}
function matchingCleanModules(clean, candidates) {
  const modules = cleanNames(clean.modules);
  return modules.filter((name) => candidates.includes(name));
}
function previewCleanAppliesToStage(clean, route, stage, stageIndex, candidates, firstProcessStageIndex2) {
  const name = String(clean.name || "").trim();
  if (!name) return false;
  if (stageVisitCleanNames(stage).has(name)) return true;
  if (!routeLevelCleanNames(route).has(name)) return false;
  const modules = cleanNames(clean.modules);
  if (modules.length) return matchingCleanModules(clean, candidates).length > 0;
  return Boolean(stage.needProcess) && stageIndex === firstProcessStageIndex2;
}
function previewCleanQualifier(clean, candidates) {
  const matching = matchingCleanModules(clean, candidates);
  if (!matching.length || matching.length >= candidates.length) return "";
  return matching.join("/");
}
function previewCleanLabel(type) {
  if (type === "preclean") return "pre";
  if (type === "postclean") return "post";
  if (type === "wacclean") return "wac";
  return type;
}
function formatRoutePreviewCleanToken(clean, qualifier = "") {
  const type = previewCleanType(clean);
  if (!type) return String(clean.name || "").trim();
  const label = qualifier ? `${previewCleanLabel(type)} ${qualifier}` : previewCleanLabel(type);
  if (clean.defined === false) return label;
  const duration = formatSeconds(Number(clean.recipeTime));
  const triggerCount = Number(clean.triggerCount) || 0;
  if (type === "dummywac") {
    return `${label} ${triggerCount}|${duration}|${formatSeconds(Number(clean.wacRecipeTime))}`;
  }
  if (type === "dummy" || type === "wacclean") return `${label} ${triggerCount}|${duration}`;
  return `${label} ${duration}`;
}
function sortPreviewCleans(left, right) {
  const leftType = previewCleanType(left);
  const rightType = previewCleanType(right);
  const leftRank = LEFT_CLEAN_ORDER.indexOf(leftType);
  const rightRank = LEFT_CLEAN_ORDER.indexOf(rightType);
  return (leftRank < 0 ? LEFT_CLEAN_ORDER.length : leftRank) - (rightRank < 0 ? LEFT_CLEAN_ORDER.length : rightRank);
}
function wrapPreviewTokens(tokens) {
  return tokens.length ? `[${tokens.join("+")}]` : "";
}
function attachRoutePreviewCleanTokens(node, candidates, cleans) {
  const decorated = cleans.map((clean) => ({
    clean,
    type: previewCleanType(clean),
    token: formatRoutePreviewCleanToken(clean, previewCleanQualifier(clean, candidates))
  })).filter((item) => item.token);
  const left = decorated.filter((item) => LEFT_CLEAN_ORDER.includes(item.type)).sort((leftItem, rightItem) => sortPreviewCleans(leftItem.clean, rightItem.clean)).map((item) => item.token);
  const wac = decorated.filter((item) => item.type === "wacclean").map((item) => item.token);
  const post = decorated.filter((item) => item.type === "postclean").map((item) => item.token);
  return `${wrapPreviewTokens(left)}${node}${wrapPreviewTokens(wac)}${wrapPreviewTokens(post)}`;
}
function stageCandidates(stage) {
  return [...new Set((stage.visits || []).map((visit) => String(visit.stationName || "").trim()).filter(Boolean))];
}
function isTransferOnlyStage(stage, robotNames) {
  const candidates = stageCandidates(stage);
  return stage.kind === "robot" || Boolean(candidates.length && candidates.every((name) => robotNames.includes(name) || /robot/i.test(name) || TRANSFER_ROBOT_NAME.test(name)));
}
function firstProcessStageIndex(route) {
  return (route.stages || []).findIndex((stage) => stage.needProcess);
}
function compactRoutePreviewPath(route, options = {}) {
  const includeTestParameters = options.includeTestParameters !== false;
  const robotNames = options.robotNames || [];
  const cleans = options.cleans || [];
  const stages = route.stages || [];
  const firstProcess = firstProcessStageIndex(route);
  return stages.map((stage, stageIndex) => {
    if (isTransferOnlyStage(stage, robotNames)) return "";
    const candidates = stageCandidates(stage);
    const fixedSource = stageIndex === 0 || stageIndex === stages.length - 1;
    let node = fixedSource ? stageIndex === 0 ? "Src" : "Sink" : candidates.join("/") || "\u672A\u9009\u8154\u5BA4";
    if (includeTestParameters && stage.needProcess) {
      node += `(${formatSeconds(Number(stage.visits?.[0]?.processTime ?? stage.visits?.[0]?.recipeTime ?? 0))})`;
    }
    if (!includeTestParameters) return node;
    const stageCleans = cleans.filter((clean) => previewCleanAppliesToStage(
      clean,
      route,
      stage,
      stageIndex,
      candidates,
      firstProcess
    ));
    return attachRoutePreviewCleanTokens(node, candidates, stageCleans);
  }).filter(Boolean).join("->") || "\u672A\u914D\u7F6E\u8DEF\u5F84";
}
function routeCleanSignature(route) {
  const parts = [];
  const append = (label, value) => {
    const names = cleanNames(value);
    if (names.length) parts.push(`${label}:${names.join("+")}`);
  };
  append("Pre", route.prePJobCleanRefs);
  append("Post", route.postPJobCleanRefs);
  append("CJob", route.postCJobCleanRefs);
  (route.stages || []).filter((stage) => stage.needProcess).forEach((stage, index) => {
    const before = [...new Set((stage.visits || []).flatMap((visit) => cleanNames(visit.beforeCleanRefs)))];
    const after = [...new Set((stage.visits || []).flatMap((visit) => cleanNames(visit.afterCleanRefs)))];
    append(`S${index + 1}\u524D`, before);
    append(`S${index + 1}\u540E`, after);
  });
  return parts.join(" \xB7 ");
}
function minimumResidencyConstraint(route) {
  const limits = (route.stages || []).filter((stage) => stage.needProcess).flatMap((stage) => stage.visits || []).map((visit) => Number(visit.residencyConstraint)).filter((limit) => Number.isFinite(limit) && limit >= 0);
  return limits.length ? Math.min(...limits) : null;
}
function automaticTemplateName(profile) {
  if (profile.processCount === 0) return "\u65E0\u52A0\u5DE5\u5DE5\u5E8F";
  return profile.candidatePath.join(" \u2192 ");
}
function automaticRouteName(profile, cleanSignature = "", minimumResidency = null) {
  const processName = profile.processCount === 0 ? "\u65E0\u52A0\u5DE5\u5DE5\u5E8F" : profile.candidatePath.map(
    (path, index) => `${path}(${formatSeconds(profile.processTimes[index])})`
  ).join(" \u2192 ");
  const suffixes = [
    cleanSignature,
    minimumResidency === null ? "" : `\u9A7B\u7559 ${formatSeconds(minimumResidency)}`
  ].filter(Boolean);
  return suffixes.length ? `${processName} \xB7 ${suffixes.join(" \xB7 ")}` : processName;
}
function compareProfiles(left, right) {
  if (left.processCount !== right.processCount) return left.processCount - right.processCount;
  for (let index = 0; index < Math.max(left.counts.length, right.counts.length); index += 1) {
    if ((left.counts[index] ?? -1) !== (right.counts[index] ?? -1)) {
      return (left.counts[index] ?? -1) - (right.counts[index] ?? -1);
    }
  }
  return 0;
}
function differenceFields(stage, normalizeVisit = (value) => value) {
  if ((stage.visits || []).length < 2) return [];
  const first = normalizeVisit(stage.visits[0]);
  return VISIT_SHARED_FIELDS.filter((key) => stage.visits.slice(1).some(
    (visit) => JSON.stringify(normalizeVisit(visit)[key]) !== JSON.stringify(first[key])
  ));
}
function synchronizeVisits(stage, normalizeVisit = (value) => value) {
  if (!(stage.visits || []).length) return;
  const parameters = cloneVisitParameters(normalizeVisit(stage.visits[0]));
  stage.visits.forEach((visit) => Object.assign(visit, structuredClone(parameters)));
}
function replaceCandidates(stage, names, makeVisit, normalizeVisit = (value) => value) {
  const selected = [...new Set((names || []).map((name) => String(name || "").trim()).filter(Boolean))];
  const prior = new Map((stage.visits || []).map((visit) => [visit.stationName, visit]));
  const template = stage.visits?.length ? cloneVisitParameters(normalizeVisit(stage.visits[0])) : cloneVisitParameters(normalizeVisit(makeVisit("")));
  stage.visits = selected.map(
    (name) => prior.get(name) || { stationName: name, ...structuredClone(template) }
  );
}
function selectReferencedRoutes(routes, rounds) {
  const referencedNames = new Set((rounds || []).flatMap((round) => (round.cjobs || []).flatMap((cjob) => (cjob.pjobs || []).map((pjob) => String(pjob.routeRef || "").trim()))));
  return (routes || []).filter((route) => referencedNames.has(String(route.name || "").trim()));
}
function processRecipeName(value, fallback) {
  const explicitName = String(value ?? "").trim();
  return explicitName || String(fallback ?? "").trim();
}
function normalizeStageProcessRecipes(stage, recipeName, normalizeVisit = (value) => value) {
  const needsProcess = stage.needProcess === true;
  let changed = false;
  for (const visit of stage.visits || []) {
    normalizeVisit(visit);
    const normalizedRecipe = needsProcess ? processRecipeName(visit.processRecipe, recipeName) : "";
    if (visit.processRecipe !== normalizedRecipe) {
      visit.processRecipe = normalizedRecipe;
      changed = true;
    }
    if (needsProcess) {
      const normalizedRecipeTime = Number(visit.processTime);
      if (visit.recipeTime !== normalizedRecipeTime) {
        visit.recipeTime = normalizedRecipeTime;
        changed = true;
      }
    }
  }
  return changed;
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  VISIT_SHARED_FIELDS,
  attachRoutePreviewCleanTokens,
  automaticRouteName,
  automaticTemplateName,
  cloneVisitParameters,
  compactRoutePreviewPath,
  compareProfiles,
  differenceFields,
  formatRoutePreviewCleanToken,
  minimumResidencyConstraint,
  normalizeStageProcessRecipes,
  processProfile,
  processRecipeName,
  replaceCandidates,
  routeCleanSignature,
  routeReferencedCleanNames,
  selectReferencedRoutes,
  synchronizeVisits
});
