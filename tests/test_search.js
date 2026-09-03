const assert = require('node:assert/strict');
const { normalizeChineseSearch } = require('../static/search-normalizer.js');

const matches = (query, candidate) => normalizeChineseSearch(candidate).includes(normalizeChineseSearch(query));

assert.equal(normalizeChineseSearch('財政局'), normalizeChineseSearch('财政局'));
assert.equal(matches('公共建设局', '公共建設局'), true);
assert.equal(matches('医疗服务', '醫療服務公開招標'), true);
assert.equal(matches('衛生局', '卫生局'), true);
assert.equal(matches('不存在', '公共建設局'), false);
console.log('Simplified and Traditional Chinese search normalization tests passed.');
