/**
 * 运行制品的浏览器读取地址。
 *
 * 甘特图只需要已经合并的 MoveList，因此同源结果 API 使用轻量视图；复现日志、
 * 外部文件及其他地址保持原语义。本模块只选择 HTTP 视图，不读取或修改制品。
 */

/**
 * 为甘特图选择轻量结果地址，保留其他查询参数和片段。
 *
 * source 为用户指定的文件或 API 地址，pageUrl 为当前页面绝对地址。返回可用于
 * fetch 的地址；只有同源单结果 API 改用 view=movelist，其他来源原样返回。
 */
export function movelistResultSourceUrl(source: string, pageUrl: string): string {
  const page = new URL(pageUrl);
  const result = new URL(source, page);
  if (result.origin !== page.origin || !/^\/api\/results\/[^/]+$/.test(result.pathname)) {
    return source;
  }
  result.searchParams.set("view", "movelist");
  return source.startsWith("/") && !source.startsWith("//")
    ? `${result.pathname}${result.search}${result.hash}`
    : result.href;
}
