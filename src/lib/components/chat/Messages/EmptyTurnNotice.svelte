<script lang="ts">
	// #16 runtime slice (frontend): in-place notice for a silent empty / interrupted
	// assistant turn. Renders ONLY a plain-language banner + a bucketed cause label + a
	// COPYABLE opaque trace id + an explicit Retry — never raw content (M3/M4). The
	// cause/trace/banner are produced by `$lib/utils/empty_turn` (canonical-cause guarded).
	import Info from '$lib/components/icons/Info.svelte';

	export let cause: string;
	export let traceId: string;
	export let banner: string;
	export let onRetry: () => void = () => {};
	export let readOnly = false;
	// A5: optional existing artifacts/exports for the turn (links only — never raw content).
	export let artifacts: Array<{ name?: string; url?: string }> = [];

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
	class="flex my-2 gap-2.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-lg"
	data-testid="empty-turn-notice"
>
	<div class="self-start mt-0.5">
		<Info className="size-5 text-red-700 dark:text-red-400" />
	</div>

	<div class="self-center text-sm w-full">
		<div data-testid="empty-turn-banner">{banner}</div>

		<div class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
			<span class="px-2 py-0.5 rounded-full bg-red-600/10" data-testid="empty-turn-cause">cause: {cause}</span>
			<span>trace:</span>
			<code class="px-1 rounded bg-black/5 dark:bg-white/10 select-all" data-testid="empty-turn-trace">{traceId}</code>
			<button
				type="button"
				class="px-2 py-0.5 rounded border border-gray-300/40 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label="Copy trace id"
				on:click={copyTrace}
			>
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>

		{#if artifacts && artifacts.length}
			<div class="mt-1.5 text-xs">
				<span class="text-gray-600 dark:text-gray-400">Partial output / artifacts:</span>
				{#each artifacts as a}
					{#if a?.url}
						<a class="underline ml-1" href={a.url} target="_blank" rel="noopener noreferrer"
							>{a?.name ?? 'download'}</a
						>
					{/if}
				{/each}
			</div>
		{/if}

		{#if !readOnly}
			<div class="mt-2">
				<button
					type="button"
					class="px-3 py-1 rounded-lg text-xs font-medium bg-red-600/10 hover:bg-red-600/20 text-red-700 dark:text-red-300"
					on:click={onRetry}
				>
					Retry
				</button>
			</div>
		{/if}
	</div>
</div>
