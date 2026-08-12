<script lang="ts">
	// #17 slice-3 (frontend): non-blocking, ADDITIVE amber notice for a partial-materials turn —
	// some uploaded files may NOT have been delivered to the agent. Renders ONLY a counts banner +
	// a counts chip + a COPYABLE opaque trace id — never raw names/content (M3/M4). It does NOT
	// hide/replace the answer (no Retry; the answer is valid, just annotated). Counts/banner/trace
	// are produced by `$lib/utils/materials_warning` (canonical-kind + counts-only guarded).
	import Info from '$lib/components/icons/Info.svelte';

	export let banner: string;
	export let used: number;
	export let total: number;
	export let unused: number;
	export let skipped = 0;
	export let failed = 0;
	export let traceId: string;

	let copied = false;
	const copyTrace = async () => {
		try {
			await navigator.clipboard.writeText(traceId);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch (e) {
			// clipboard may be unavailable (insecure context) — the id is still selectable.
		}
	};
</script>

<div
	class="flex my-2 gap-2.5 border px-4 py-3 border-amber-500/20 bg-amber-500/10 rounded-lg"
	data-testid="materials-warning-notice"
>
	<div class="self-start mt-0.5">
		<Info className="size-5 text-amber-600 dark:text-amber-400" />
	</div>

	<div class="self-center text-sm w-full">
		<div data-testid="materials-warning-banner">{banner}</div>

		<div class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
			<span class="px-2 py-0.5 rounded-full bg-amber-500/10" data-testid="materials-warning-counts"
				>{used}/{total} delivered · {unused} not delivered{#if skipped}
					· {skipped} skipped{/if}{#if failed}
					· {failed} failed{/if}</span
			>
			<span>trace:</span>
			<code
				class="px-1 rounded bg-black/5 dark:bg-white/10 select-all"
				data-testid="materials-warning-trace">{traceId}</code
			>
			<button
				type="button"
				class="px-2 py-0.5 rounded border border-gray-300/40 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label="Copy trace id"
				on:click={copyTrace}
			>
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>
	</div>
</div>
