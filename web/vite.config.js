import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// SvelteKit 3 takes its configuration here, not in svelte.config.js.
// Built as a static single-page app and served by the FastAPI container.
// All data comes from /api, so there is no server-side rendering.
export default defineConfig({
	plugins: [
		sveltekit({
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		// For local development only: forward API calls to a running API.
		proxy: {
			'/api': 'http://127.0.0.1:8095',
			'/health': 'http://127.0.0.1:8095'
		}
	}
});
