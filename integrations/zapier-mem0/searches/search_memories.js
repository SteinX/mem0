'use strict';

const perform = async (z, bundle) => {
	const body = {
		query: bundle.inputData.query,
		output_format: 'v1.1',
		top_k: Math.max(1, Math.floor(Number(bundle.inputData.limit) || 10)),
	};
	if (bundle.inputData.user_id) body.filters = { user_id: bundle.inputData.user_id };

	const response = await z.request({
		url: '/v3/memories/search/',
		method: 'POST',
		body,
	});
	// Searches must return an array.
	const data = response.data;
	return Array.isArray(data) ? data : data.results || [];
};

module.exports = {
	key: 'search_memories',
	noun: 'Memory',
	display: {
		label: 'Search Memories',
		description: 'Semantic search over stored memories.',
	},
	operation: {
		perform,
		inputFields: [
			{ key: 'query', label: 'Query', type: 'string', required: true },
			{ key: 'user_id', label: 'User ID', type: 'string', required: true },
			{ key: 'limit', label: 'Limit', type: 'integer', default: '10' },
		],
		sample: { id: '00000000-0000-0000-0000-000000000000', memory: 'User loves hiking' },
	},
};
