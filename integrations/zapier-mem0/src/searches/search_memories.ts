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
	const clauses: unknown[] = speakers.length > 1 ? [{ OR: speakers }] : speakers;
	if (bundle.inputData.app_id) clauses.push({ app_id: bundle.inputData.app_id });
	if (bundle.inputData.run_id) clauses.push({ run_id: bundle.inputData.run_id });
	if (clauses.length > 0) body.filters = clauses.length === 1 ? clauses[0] : { AND: clauses };

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
			{ key: 'app_id', label: 'App ID', type: 'string' },
			{ key: 'run_id', label: 'Run ID', type: 'string' },
			{ key: 'limit', label: 'Limit', type: 'integer', default: '50' },
		],
		sample: { id: '00000000-0000-0000-0000-000000000000', memory: 'User loves hiking' },
	},
};
