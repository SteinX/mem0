'use strict';

// Prepend the configured base URL and inject the auth header on every request.
const includeApiKey = (request, z, bundle) => {
	if (bundle.authData && bundle.authData.apiKey) {
		request.headers = request.headers || {};
		request.headers.Authorization = `Token ${bundle.authData.apiKey}`;
	}
	// Resolve relative URLs against the configured base URL.
	if (request.url && request.url.startsWith('/')) {
		const base = (bundle.authData && bundle.authData.baseUrl) || 'https://api.mem0.ai';
		request.url = `${base.replace(/\/$/, '')}${request.url}`;
	}
	return request;
};

// Surface HTTP failures as errors. z.request does NOT throw on non-2xx by
// default, so without this a 4xx/5xx would flow downstream as a fake success
// (empty search results / error body returned as a created memory).
const handleBadResponses = (response, z, _bundle) => {
	if (response.status === 401 || response.status === 403) {
		throw new z.errors.Error(
			'The Mem0 API key you supplied is invalid or lacks access.',
			'AuthenticationError',
			response.status,
		);
	}
	if (response.status >= 400) {
		const data = response.data || {};
		const detail = data.detail || data.error || data.message || response.content || 'unknown error';
		throw new z.errors.Error(
			`Mem0 API request failed (HTTP ${response.status}): ${detail}`,
			'Mem0ApiError',
			response.status,
		);
	}
	return response;
};

module.exports = { includeApiKey, handleBadResponses };
