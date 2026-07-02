'use strict';

const perform = async (z, bundle) => {
	const body = {};
	if (bundle.inputData.user_id) body.filters = { user_id: bundle.inputData.user_id };

	const response = await z.request({
		url: '/v3/memories/',
		method: 'POST',
		params: { page: 1, page_size: Math.max(1, Math.floor(Number(bundle.inputData.limit) || 50)) },
		body,
	});
	const data = response.data;
	return Array.isArray(data) ? data : data.results || [];
};

module.exports = {
	key: 'get_memories',
	noun: 'Memory',
	display: {
		label: 'Get Memories',
		description: 'List stored memories for a user.',
	},
	operation: {
		perform,
		inputFields: [
			{ key: 'user_id', label: 'User ID', type: 'string', required: true },
			{ key: 'limit', label: 'Limit', type: 'integer', default: 50 },
		],
		sample: { id: '00000000-0000-0000-0000-000000000000', memory: 'User loves hiking' },
	},
};
