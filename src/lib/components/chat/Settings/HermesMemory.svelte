<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { user } from '$lib/stores';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18nType } from 'i18next';
	import {
		getHermesProfile,
		addHermesFact,
		removeHermesFact,
		searchHermesMemory
	} from '$lib/apis/hermes/memory';

	const i18n = getContext<Writable<I18nType>>('i18n');

	interface Fact {
		id: number;
		content: string;
		category?: string;
		tags?: string;
		trust_score?: number;
	}

	let facts: Fact[] = [];
	let loading = false;
	let error = '';

	let newFactContent = '';
	let newFactCategory = 'user_pref';
	let submitting = false;

	let searchQuery = '';
	let searchResults: Fact[] = [];
	let searching = false;

	async function loadFacts() {
		loading = true;
		error = '';
		try {
			const token = localStorage.token ?? '';
			const resp = await getHermesProfile(token);
			const payload = resp?.result;
			facts = Array.isArray(payload) ? payload : (payload?.facts ?? []);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			facts = [];
		} finally {
			loading = false;
		}
	}

	async function addFact() {
		if (!newFactContent.trim()) return;
		submitting = true;
		try {
			const token = localStorage.token ?? '';
			await addHermesFact(token, newFactContent.trim(), { category: newFactCategory });
			toast.success($i18n.t('Fact saved'));
			newFactContent = '';
			await loadFacts();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
		} finally {
			submitting = false;
		}
	}

	async function removeFact(fact_id: number) {
		const prompt = $i18n.t('Delete this fact?');
		if (!confirm(prompt)) return;
		try {
			const token = localStorage.token ?? '';
			await removeHermesFact(token, fact_id);
			toast.success($i18n.t('Fact deleted'));
			await loadFacts();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
		}
	}

	async function runSearch() {
		if (!searchQuery.trim()) {
			searchResults = [];
			return;
		}
		searching = true;
		try {
			const token = localStorage.token ?? '';
			const resp = await searchHermesMemory(token, searchQuery.trim(), 10);
			const payload = resp?.result;
			searchResults = Array.isArray(payload) ? payload : (payload?.results ?? []);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
			searchResults = [];
		} finally {
			searching = false;
		}
	}

	onMount(() => {
		loadFacts();
	});
</script>

<div
	id="tab-hermes-memory"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	data-testid="hermes-memory-panel"
>
	<div class="py-1 overflow-y-scroll max-h-[28rem] md:max-h-full">
		<!-- Header -->
		<div class="mb-3">
			<div class="flex items-center gap-2 text-sm font-medium">
				{$i18n.t('Hermes memory')}
				<span
					class="text-[0.65rem] font-medium uppercase px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
					>{$i18n.t('Experimental')}</span
				>
			</div>
			<div class="text-xs text-gray-600 dark:text-gray-400 mt-1">
				{$i18n.t(
					'Facts the Hermes agent remembers about you. Only you can see these — other users on this tenant have their own separate memory.'
				)}
			</div>
		</div>

		<!-- Add new fact -->
		<div class="mb-4 rounded-lg border border-gray-200 dark:border-gray-800 p-3">
			<div class="text-xs font-medium mb-2">{$i18n.t('Add a fact')}</div>
			<textarea
				bind:value={newFactContent}
				placeholder={$i18n.t('e.g. I prefer concise responses')}
				rows={2}
				class="w-full text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded-md px-2 py-1 resize-none focus:outline-none focus:ring-1 focus:ring-gray-400"
				data-testid="hermes-memory-new-fact-input"
			></textarea>
			<div class="flex items-center justify-between mt-2">
				<select
					bind:value={newFactCategory}
					class="text-xs bg-transparent border border-gray-200 dark:border-gray-700 rounded-md px-2 py-0.5"
				>
					<option value="user_pref">{$i18n.t('Preference')}</option>
					<option value="project">{$i18n.t('Project')}</option>
					<option value="tool">{$i18n.t('Tool')}</option>
					<option value="general">{$i18n.t('General')}</option>
				</select>
				<button
					type="button"
					disabled={submitting || !newFactContent.trim()}
					on:click={addFact}
					class="px-3 py-1 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full disabled:opacity-40"
					data-testid="hermes-memory-add-button"
				>
					{submitting ? $i18n.t('Saving...') : $i18n.t('Add')}
				</button>
			</div>
		</div>

		<!-- Search -->
		<div class="mb-3 flex items-center gap-2">
			<input
				type="text"
				bind:value={searchQuery}
				placeholder={$i18n.t('Search your memory')}
				class="flex-1 text-xs bg-transparent border border-gray-200 dark:border-gray-700 rounded-md px-2 py-1"
				on:keydown={(e) => e.key === 'Enter' && runSearch()}
				data-testid="hermes-memory-search-input"
			/>
			<button
				type="button"
				on:click={runSearch}
				disabled={searching}
				class="px-2 py-1 text-xs border border-gray-300 dark:border-gray-700 rounded-md disabled:opacity-40"
			>
				{searching ? $i18n.t('Searching...') : $i18n.t('Search')}
			</button>
		</div>

		{#if searchResults.length}
			<div class="mb-4">
				<div class="text-[0.7rem] uppercase tracking-wide text-gray-500 mb-1">
					{$i18n.t('Search results')}
				</div>
				<ul class="space-y-1">
					{#each searchResults as r (r.id)}
						<li
							class="text-xs p-2 rounded border border-amber-200 dark:border-amber-900/50 bg-amber-50/50 dark:bg-amber-950/30"
						>
							<span class="opacity-70 mr-1">
								{#if r.trust_score != null}[{r.trust_score.toFixed(1)}]{/if}
							</span>
							{r.content}
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<!-- All facts -->
		<div>
			<div class="text-[0.7rem] uppercase tracking-wide text-gray-500 mb-1">
				{$i18n.t('All stored facts')} ({facts.length})
			</div>
			{#if loading}
				<div class="text-xs text-gray-500">{$i18n.t('Loading...')}</div>
			{:else if error}
				<div class="text-xs text-red-600">{error}</div>
			{:else if facts.length === 0}
				<div class="text-xs text-gray-500 italic">
					{$i18n.t('No facts yet. Add one above, or chat with Hermes and it will remember things for you automatically.')}
				</div>
			{:else}
				<ul class="space-y-1" data-testid="hermes-memory-fact-list">
					{#each facts as f (f.id)}
						<li
							class="text-xs p-2 rounded border border-gray-200 dark:border-gray-800 flex items-start justify-between gap-2"
						>
							<div class="flex-1 min-w-0 break-words">
								<span class="opacity-70 mr-1">
									{#if f.trust_score != null}[{f.trust_score.toFixed(1)}]{/if}
								</span>
								{f.content}
								{#if f.category}
									<span
										class="ml-1 text-[0.6rem] uppercase px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500"
										>{f.category}</span
									>
								{/if}
							</div>
							<button
								type="button"
								aria-label={$i18n.t('Delete fact')}
								on:click={() => removeFact(f.id)}
								class="text-gray-400 hover:text-red-600 flex-shrink-0"
								data-testid="hermes-memory-delete-button"
							>
								&times;
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</div>

	<div class="flex justify-end text-sm font-medium">
		<button
			type="button"
			on:click={loadFacts}
			class="px-3.5 py-1.5 text-sm font-medium border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-full"
		>
			{$i18n.t('Refresh')}
		</button>
	</div>
</div>
