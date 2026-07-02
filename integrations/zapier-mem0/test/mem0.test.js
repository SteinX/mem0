'use strict';

/* global describe, it, expect, beforeAll */

const zapier = require('zapier-platform-core');
const App = require('../index');

const appTester = zapier.createAppTester(App);

const authData = {
	apiKey: process.env.MEM0_API_KEY,
	baseUrl: process.env.MEM0_BASE_URL || 'https://api.mem0.ai',
};

const userId = `zapier-e2e-${Date.now()}`;

describe('Mem0 Zapier integration (E2E)', () => {
	beforeAll(() => {
		if (!authData.apiKey) throw new Error('MEM0_API_KEY env var is required for E2E tests');
	});

	it('authentication.test succeeds', async () => {
		const res = await appTester(App.authentication.test, { authData });
		expect(res.status).toBe(200);
	});

	it('adds, searches, lists, and deletes a memory', async () => {
		// Add (async, wait for completion)
		const added = await appTester(App.creates.add_memory.operation.perform, {
			authData,
			inputData: {
				content: 'I love hiking in the Alps and my favorite food is sushi',
				user_id: userId,
				waitForCompletion: true,
			},
		});
		expect(added.status).toBe('SUCCEEDED');

		// Search
		const found = await appTester(App.searches.search_memories.operation.perform, {
			authData,
			inputData: { query: 'outdoor activities', user_id: userId, limit: 5 },
		});
		expect(Array.isArray(found)).toBe(true);
		expect(found.length).toBeGreaterThan(0);

		// Get all
		const all = await appTester(App.searches.get_memories.operation.perform, {
			authData,
			inputData: { user_id: userId },
		});
		expect(Array.isArray(all)).toBe(true);
		expect(all.length).toBeGreaterThan(0);

		// Cleanup: delete every memory we created
		for (const mem of all) {
			await appTester(App.creates.delete_memory.operation.perform, {
				authData,
				inputData: { memory_id: mem.id },
			});
		}

		const afterDelete = await appTester(App.searches.get_memories.operation.perform, {
			authData,
			inputData: { user_id: userId },
		});
		expect(afterDelete.length).toBe(0);
	});

	it('surfaces API errors instead of returning an empty array (search needs a filter)', async () => {
		// filters is required by the API; omitting it must throw, not return [].
		await expect(
			appTester(App.searches.search_memories.operation.perform, {
				authData,
				inputData: { query: 'anything' },
			}),
		).rejects.toThrow();
	});
});
