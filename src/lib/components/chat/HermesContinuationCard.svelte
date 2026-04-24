<script lang="ts">
	import { fade } from 'svelte/transition';
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	/** Summary sentence of the in-flight task from last session. */
	export let taskSummary: string;

	/** Confidence tier reported by hermes: low | medium | high. */
	export let confidence: 'low' | 'medium' | 'high' = 'low';

	/** Age of the last session in hours, or null if unknown. */
	export let lastSessionAgeHours: number | null = null;

	/**
	 * Called when the user clicks "Resume" — the caller fills the message
	 * input with taskSummary so the user can review/edit before sending.
	 */
	export let onResume: (summary: string) => void = () => {};

	/**
	 * Called when the user dismisses the card.
	 */
	export let onDismiss: () => void = () => {};

	function formatAge(hours: number | null): string {
		if (hours === null) return '';
		if (hours < 1) return $i18n.t('less than an hour ago');
		if (hours < 24) {
			const h = Math.round(hours);
			return $i18n.t('{{count}} hour ago', { count: h, defaultValue: `${h}h ago` });
		}
		const d = Math.round(hours / 24);
		return $i18n.t('{{count}} day ago', { count: d, defaultValue: `${d}d ago` });
	}
</script>

<!-- data-testid is the dedup anchor — Chat.svelte checks for this before rendering. -->
<div
	data-testid="hermes-continuation-card"
	class="w-full max-w-2xl mx-auto mb-3 px-2"
	in:fade={{ duration: 150 }}
>
	<div
		class="flex items-start gap-3 rounded-xl border border-gray-200 dark:border-gray-700
		       bg-white dark:bg-gray-850 shadow-sm px-4 py-3 text-sm"
	>
		<!-- Indicator icon -->
		<div class="mt-0.5 shrink-0 text-blue-500 dark:text-blue-400">
			<svg
				xmlns="http://www.w3.org/2000/svg"
				class="size-5"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"
			>
				<!-- clock / history icon -->
				<circle cx="12" cy="12" r="10" />
				<polyline points="12 6 12 12 16 14" />
			</svg>
		</div>

		<!-- Text body -->
		<div class="flex-1 min-w-0">
			<p class="font-medium text-gray-800 dark:text-gray-100 leading-snug">
				{$i18n.t('Pick up where you left off?')}
			</p>
			<p class="mt-0.5 text-gray-500 dark:text-gray-400 line-clamp-2">
				{taskSummary}
			</p>
			{#if lastSessionAgeHours !== null}
				<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">
					{formatAge(lastSessionAgeHours)}
				</p>
			{/if}
		</div>

		<!-- Action buttons -->
		<div class="flex items-center gap-2 shrink-0 ml-2">
			<button
				type="button"
				class="px-3 py-1.5 rounded-lg text-xs font-medium
				       bg-blue-500 hover:bg-blue-600 text-white
				       transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-blue-400"
				on:click={() => onResume(taskSummary)}
			>
				{$i18n.t('Resume')}
			</button>
			<button
				type="button"
				class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300
				       hover:bg-gray-100 dark:hover:bg-gray-700
				       transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-gray-400"
				aria-label={$i18n.t('Dismiss')}
				on:click={onDismiss}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					class="size-4"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
				>
					<line x1="18" y1="6" x2="6" y2="18" />
					<line x1="6" y1="6" x2="18" y2="18" />
				</svg>
			</button>
		</div>
	</div>
</div>
