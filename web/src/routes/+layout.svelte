<script>
	// B612 (Airbus cockpit typeface), self-hosted: regular and bold, Latin subset.
	import '@fontsource/b612/latin-400.css';
	import '@fontsource/b612/latin-700.css';
	import '../app.css';

	let { children } = $props();
</script>

{@render children()}
