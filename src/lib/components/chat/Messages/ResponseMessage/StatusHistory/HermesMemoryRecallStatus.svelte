<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import LightBulb from '$lib/components/icons/LightBulb.svelte';

	/** @type {Record<string, any> | null} */
	export let status = null;
	export let done = false;

	$: provider = status?.provider || '';
	$: contextPreview = status?.context_preview || '';
	$: tokenEstimate = status?.context_token_estimate || 0;
	$: isActive = (done || status?.done) === false;

	let expanded = false;
</script>

<div class="flex flex-col w-full gap-0.5">
	<button
		class="flex items-start gap-2 w-full text-left"
		on:click={() => (expanded = !expanded)}
		aria-expanded={expanded}
		data-testid="hermes-memory-recall-chip"
	>
		<div class="flex-shrink-0 mt-0.5 text-amber-500 dark:text-amber-400">
			<LightBulb className="size-3.5" />
		</div>
		<div class="flex flex-col min-w-0 flex-1">
			<div class="{isActive ? 'shimmer' : ''} text-sm text-amber-600 dark:text-amber-400">
				{$i18n.t('Recalled from your memory')}
				{#if provider}
					<span class="text-xs text-gray-500 dark:text-gray-500"> · {provider}</span>
				{/if}
				{#if tokenEstimate > 0}
					<span class="text-xs text-gray-500 dark:text-gray-500"> · ~{tokenEstimate} tokens</span>
				{/if}
			</div>

			{#if expanded && contextPreview}
				<div
					class="mt-1 text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words bg-amber-50 dark:bg-amber-950/30 rounded-lg px-2.5 py-2 border border-amber-200/50 dark:border-amber-800/30"
				>
					{contextPreview}
				</div>
			{:else if contextPreview}
				<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1 italic">
					{contextPreview}
				</div>
			{/if}
		</div>
	</button>
</div>
