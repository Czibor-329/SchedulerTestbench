var ResultArtifactUrls = (() => {
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

  // src/result_artifact_urls.ts
  var result_artifact_urls_exports = {};
  __export(result_artifact_urls_exports, {
    movelistResultSourceUrl: () => movelistResultSourceUrl
  });
  function movelistResultSourceUrl(source, pageUrl) {
    const page = new URL(pageUrl);
    const result = new URL(source, page);
    if (result.origin !== page.origin || !/^\/api\/results\/[^/]+$/.test(result.pathname)) {
      return source;
    }
    result.searchParams.set("view", "movelist");
    return source.startsWith("/") && !source.startsWith("//") ? `${result.pathname}${result.search}${result.hash}` : result.href;
  }
  return __toCommonJS(result_artifact_urls_exports);
})();
