<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { removeHermesFact } from '$lib/apis/hermes/memory';
	import LightBulb from '$lib/components/icons/LightBulb.svelte';

	const i18n = getContext('i18n');

	interface RecalledFact {
		id: string;
		content_preview: string;
		score: number | null;
	}

	/** @type {Record<string, any> | null} */
	export let status = null;
	export let done = false;

	$: provider = status?.provider || '';
	$: contextPreview = status?.context_preview || '';
	$: tokenEstimate = status?.context_token_estimate || 0;
	$: isActive = (done || status?.done) === false;
	$: recalledFacts = (status?.recalled_facts ?? []) as RecalledFact[];
	$: hasProvenance = recalledFacts.length > 0;

	let expanded = false;
	// Local mutable copy so splices after Forget are instant in the UI
	let localFacts: RecalledFact[] = [];
	$: {
		// Sync from status when it changes (new turn), but don't clobber
		// in-progress deletes by resetting on every reactive run.
		// Guard: only reset when the array identity changes.
		localFacts = recalledFacts;
	}

	async function forgetFact(fact: RecalledFact) {
		const factIdNum = parseInt(fact.id, 10);
		if (isNaN(factIdNum)) {
			toast.error($i18n.t('Invalid fact id'));
			return;
		}
		try {
			const token = (localStorage as any).token ?? '';
			await removeHermesFact(token, factIdNum);
			// Splice immediately — don't wait for a server re-fetch
			localFacts = localFacts.filter((f) => f.id !== fact.id);
			toast.success($i18n.t('Fact forgotten'));
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
		}
	}
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
				{#if hasProvenance}
					<span class="text-xs text-gray-400 dark:text-gray-500 ml-1">
						({localFacts.length}
						{localFacts.length === 1 ? $i18n.t('fact') : $i18n.t('facts')})
						<span class="opacity-60">{expanded ? '▴' : '▾'}</span>
					</span>
				{/if}
			</div>

			<!-- Expanded provenance list (only when recalled_facts is non-empty) -->
			{#if expanded && hasProvenance}
				<ul class="mt-1.5 flex flex-col gap-1" data-testid="hermes-memory-recall-facts-list">
					{#each localFacts as fact (fact.id)}
						<li
							class="flex items-start gap-2 text-xs bg-amber-50 dark:bg-amber-950/30 rounded-lg px-2.5 py-1.5 border border-amber-200/50 dark:border-amber-800/30"
							data-testid="hermes-memory-recall-fact-item"
						>
							<div class="flex-1 min-w-0 break-words text-gray-700 dark:text-gray-300">
								{fact.content_preview}
								{#if fact.score !== null && fact.score !== undefined}
									<span class="ml-1 opacity-50 text-[0.6rem]">[{fact.score.toFixed(2)}]</span>
								{/if}
							</div>
							<button
								type="button"
								aria-label={$i18n.t('Forget this fact')}
								on:click|stopPropagation={() => forgetFact(fact)}
								class="flex-shrink-0 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
								data-testid="hermes-memory-recall-forget-button"
								title={$i18n.t('Forget')}
							>
								<!-- Trash/forget icon -->
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
									stroke-linecap="round"
									stroke-linejoin="round"
									class="size-3"
									aria-hidden="true"
								>
									<path d="M3 6h18" />
									<path d="M19 6l-1 14H6L5 6" />
									<path d="M8 6V4h8v2" />
								</svg>
							</button>
						</li>
					{/each}
				</ul>

				<!-- No-provenance fallback: show contextPreview inline as before -->
			{:else if expanded && contextPreview}
				<div
					class="mt-1 text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words bg-amber-50 dark:bg-amber-950/30 rounded-lg px-2.5 py-2 border border-amber-200/50 dark:border-amber-800/30"
				>
					{contextPreview}
				</div>
			{:else if !expanded && contextPreview && !hasProvenance}
				<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1 italic">
					{contextPreview}
				</div>
			{/if}
		</div>
	</button>
</div>
