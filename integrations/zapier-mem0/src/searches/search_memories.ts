import type { ZObject, Bundle, Memory } from '../types';

const perform = async (z: ZObject, bundle: Bundle): Promise<Memory[]> => {
	const body: Record<string, unknown> = {
		query: bundle.inputData.query,
		output_format: 'v1.1',
		top_k: Math.max(1, Math.floor(Number(bundle.inputData.limit) || 50)),
	};
	const speakers = [
		bundle.inputData.user_id && { user_id: bundle.inputData.user_id },
		bundle.inputData.agent_id && { agent_id: bundle.inputData.agent_id },
	].filter(Boolean);
	if (speakers.length > 0) body.filters = speakers.length === 1 ? speakers[0] : { OR: speakers };

	const response = await z.request({
		url: '/v3/memories/search/',
		method: 'POST',
		body,
	});
	// Searches must return an array; guard against a null/empty body.
	const data = response.data as Memory[] | { results?: Memory[] } | null;
	return Array.isArray(data) ? data : data?.results ?? [];
};

export default {
	key: 'search_memories',
	noun: 'Memory',
	display: {
		label: 'Find Memories',
		description: 'Finds memories matching a semantic query.',
	},
	operation: {
		perform,
		inputFields: [
			{ key: 'query', label: 'Query', type: 'string', required: true },
			{ key: 'user_id', label: 'User ID', type: 'string', required: true },
			{ key: 'agent_id', label: 'Agent ID', type: 'string' },
			{ key: 'limit', label: 'Limit', type: 'integer', default: '50' },
		],
		sample: { id: '00000000-0000-0000-0000-000000000000', memory: 'User loves hiking' },
	},
};
