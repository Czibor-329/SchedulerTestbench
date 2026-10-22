/** 分析报告视图契约：指标各占一列，保留缺失状态与来自业务数据的安全文本。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { renderTestGroupAnalysis } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

/** 构造仅包含被选指标的最小报告，未计算指标不应产生空列。 */
function report(selectedMetricIds, overrides = {}) {
  return {
    selectedMetricIds,
    cases: [{ name: 'test1', throughputPerHour: 90.7, averageRecomputeTimeMs: 1246.9, ...overrides }],
  };
}

test('一个或两个指标仍各占独立表头和数值列', () => {
  for (const metrics of [['throughput'], ['throughput', 'average_recompute_time']]) {
    const html = renderTestGroupAnalysis(report(metrics), '公司示例组');
    const header = html.match(/<thead>([\s\S]*?)<\/thead>/)[1];
    const body = html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1];
    assert.equal((header.match(/<th scope="col">/g) || []).length, metrics.length + 1);
    assert.equal((body.match(/<td>/g) || []).length, metrics.length);
    assert.match(body, /90\.7 片\/h/);
    if (metrics.includes('average_recompute_time')) assert.match(body, /1\.25 s/);
  }
});

test('多指标报告保留缺失值、失败状态与测试名称的安全转义', () => {
  const html = renderTestGroupAnalysis(report(['throughput', 'average_recompute_time', 'validation'], {
    name: '<test>', throughputPerHour: null, averageRecomputeTimeMs: null,
    analysisStatus: 'failed', error: '<未计算>',
  }), '<测试组>');
  assert.match(html, /&lt;test&gt;/);
  assert.match(html, /&lt;测试组&gt;/);
  assert.match(html, /&lt;未计算&gt;/);
  assert.equal((html.match(/<td>—<\/td>/g) || []).length, 2);
  assert.match(html, /scope="row"/);
  assert.match(html, /aria-label="逐测试分析报告，可横向滚动"/);
});
