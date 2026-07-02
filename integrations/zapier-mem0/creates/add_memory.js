'use strict';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 40; // ~60s

// Polls GET /v1/event/{id}/ until the async memory-addition event resolves.
const pollEvent = async (z, eventId) => {
	for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
		const res = await z.request({ url: `/v1/event/${eventId}/`, method: 'GET' });
		const status = res.data && res.data.status;
		if (status === 'SUCCEEDED') {
			return res.data;
		}
		if (status === 'FAILED') {
			const reason = (res.data && (res.data.error || res.data.message)) || 'unknown error';
			throw new z.errors.Error(`Mem0 memory event ${eventId} failed: ${reason}`, 'Mem0EventFailed', 400);
		}
		await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
	}
	throw new z.errors.Error(
		`Timed out waiting for memory event ${eventId} to complete`,
		'Mem0Timeout',
		408,
	);
};

const perform = async (z, bundle) => {
	// Zapier boolean fields can arrive as the strings 'true'/'false'; coerce
	// explicitly so "Infer = No" / "Wait = No" are honored.
	const infer = String(bundle.inputData.infer) !== 'false';
	const wait = String(bundle.inputData.waitForCompletion) !== 'false';

	const body = {
		messages: [{ role: bundle.inputData.role || 'user', content: bundle.inputData.content }],
		infer,
	};
	if (bundle.inputData.user_id) body.user_id = bundle.inputData.user_id;
	if (bundle.inputData.agent_id) body.agent_id = bundle.inputData.agent_id;
	if (bundle.inputData.run_id) body.run_id = bundle.inputData.run_id;
	if (bundle.inputData.metadata) {
		try {
			body.metadata =
				typeof bundle.inputData.metadata === 'string'
					? JSON.parse(bundle.inputData.metadata)
					: bundle.inputData.metadata;
		} catch (e) {
			throw new z.errors.Error('Metadata must be valid JSON.', 'InvalidInput', 400);
		}
	}

	const response = await z.request({
		url: '/v3/memories/add/',
		method: 'POST',
		body,
	});

	const data = response.data;

	// infer=true returns {event_id, status:PENDING|RUNNING}; optionally wait.
	if (wait && data.event_id && data.status !== 'SUCCEEDED' && data.status !== 'FAILED') {
		return pollEvent(z, data.event_id);
	}
	if (data.status === 'FAILED') {
		const reason = data.error || data.message || 'unknown error';
		throw new z.errors.Error(`Mem0 memory add failed: ${reason}`, 'Mem0EventFailed', 400);
	}
	return data;
};

module.exports = {
	key: 'add_memory',
	noun: 'Memory',
	display: {
		label: 'Add Memory',
		description: 'Extract and store memories from a message.',
	},
	operation: {
		perform,
		inputFields: [
			{
				key: 'content',
				label: 'Content',
				type: 'text',
				required: true,
				helpText: 'The message content to extract memories from.',
			},
			{
				key: 'role',
				label: 'Role',
				choices: { user: 'User', assistant: 'Assistant', system: 'System' },
				default: 'user',
			},
			{ key: 'user_id', label: 'User ID', type: 'string' },
			{ key: 'agent_id', label: 'Agent ID', type: 'string' },
			{ key: 'run_id', label: 'Run ID', type: 'string' },
			{ key: 'metadata', label: 'Metadata (JSON)', type: 'string' },
			{
				key: 'infer',
				label: 'Infer',
				type: 'boolean',
				default: true,
				helpText: 'Run LLM extraction (async). Turn off to store verbatim (synchronous).',
			},
			{
				key: 'waitForCompletion',
				label: 'Wait for Completion',
				type: 'boolean',
				default: true,
				helpText: 'Poll until extraction finishes and return the resulting memories.',
			},
		],
		sample: { status: 'SUCCEEDED', event_id: '00000000-0000-0000-0000-000000000000', results: [] },
	},
};
