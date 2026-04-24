<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import CommandLine from '$lib/components/icons/CommandLine.svelte';
	import LightBulb from '$lib/components/icons/LightBulb.svelte';
	import Bolt from '$lib/components/icons/Bolt.svelte';

	/** @type {Record<string, any> | null} */
	export let status = null;
	export let done = false;

	$: subAction = status?.sub_action || '';
	$: isActive = (done || status?.done) === false;
	$: toolName = status?.tool_name || '';
	$: toolInput = status?.tool_input || '';
	$: description = status?.description || '';

	let expanded = false;
</script>

<div class="flex flex-col w-full gap-0.5">
	{#if subAction === 'start'}
		<!-- Starting agent skill -->
		<div class="flex items-center gap-2">
			<div class="flex-shrink-0 text-blue-500 dark:text-blue-400">
				<Sparkles className="size-3.5" />
			</div>
			<div
				class="{isActive ? 'shimmer' : ''} text-base font-medium text-blue-600 dark:text-blue-400"
			>
				{status?.skill_name
					? $i18n.t('Running agent skill: {{name}}', { name: status.skill_name })
					: $i18n.t('Running agent skill')}
			</div>
		</div>
	{:else if subAction === 'thinking'}
		<!-- Thinking -->
		<button class="flex items-start gap-2 w-full text-left" on:click={() => (expanded = !expanded)}>
			<div class="flex-shrink-0 mt-0.5 text-purple-500 dark:text-purple-400">
				<LightBulb className="size-3.5" />
			</div>
			<div class="flex flex-col min-w-0 flex-1">
				<div class="{isActive ? 'shimmer' : ''} text-sm text-purple-600 dark:text-purple-400">
					{$i18n.t('Thinking')}
				</div>
				{#if expanded && description}
					<div
						class="mt-1 text-xs text-gray-500 dark:text-gray-400 font-mono whitespace-pre-wrap break-all bg-purple-50 dark:bg-purple-950/30 rounded-lg px-2.5 py-2 border border-purple-200/50 dark:border-purple-800/30"
					>
						{description}
					</div>
				{:else if description}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1 italic">
						{description}
					</div>
				{/if}
			</div>
		</button>
	{:else if subAction === 'tool_use'}
		<!-- Tool usage -->
		<button class="flex items-start gap-2 w-full text-left" on:click={() => (expanded = !expanded)}>
			<div class="flex-shrink-0 mt-0.5 text-amber-500 dark:text-amber-400">
				<CommandLine className="size-3.5" />
			</div>
			<div class="flex flex-col min-w-0 flex-1">
				<div class="flex items-center gap-1.5">
					<div class="{isActive ? 'shimmer' : ''} text-sm text-amber-600 dark:text-amber-400">
						{toolName || $i18n.t('Tool')}
					</div>
				</div>
				{#if expanded && toolInput}
					<div
						class="mt-1 text-xs font-mono whitespace-pre-wrap break-all bg-gray-50 dark:bg-gray-850 rounded-lg px-2.5 py-2 border border-gray-200/50 dark:border-gray-700/50 text-gray-600 dark:text-gray-300"
					>
						{toolInput}
					</div>
				{:else if toolInput}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1 font-mono">
						{toolInput}
					</div>
				{/if}
			</div>
		</button>
	{:else if subAction === 'output'}
		<!-- Text output -->
		<button class="flex items-start gap-2 w-full text-left" on:click={() => (expanded = !expanded)}>
			<div class="flex-shrink-0 mt-0.5 text-green-500 dark:text-green-400">
				<Bolt className="size-3.5" />
			</div>
			<div class="flex flex-col min-w-0 flex-1">
				<div class="{isActive ? 'shimmer' : ''} text-sm text-green-600 dark:text-green-400">
					{$i18n.t('Output')}
				</div>
				{#if expanded && description}
					<div
						class="mt-1 text-xs whitespace-pre-wrap break-all bg-green-50 dark:bg-green-950/30 rounded-lg px-2.5 py-2 border border-green-200/50 dark:border-green-800/30 text-gray-600 dark:text-gray-300"
					>
						{description}
					</div>
				{:else if description}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
						{description}
					</div>
				{/if}
			</div>
		</button>
	{:else if subAction === 'error'}
		<!-- Error -->
		<div class="flex items-start gap-2">
			<div class="flex-shrink-0 mt-0.5 text-red-500 dark:text-red-400">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="1.5"
					stroke="currentColor"
					class="size-3.5"
					aria-hidden="true"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
					/>
				</svg>
			</div>
			<div class="text-sm text-red-600 dark:text-red-400">
				{description}
			</div>
		</div>
	{:else if subAction === 'complete'}
		<!-- Completion -->
		<div class="flex items-center gap-2">
			<div class="flex-shrink-0 text-green-500 dark:text-green-400">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="2"
					stroke="currentColor"
					class="size-3.5"
					aria-hidden="true"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
					/>
				</svg>
			</div>
			<div class="text-sm font-medium text-green-600 dark:text-green-400">
				{$i18n.t('Agent skill completed')}
			</div>
		</div>
	{:else}
		<!-- Fallback -->
		<div class="flex items-center gap-2">
			<div class="flex-shrink-0 text-gray-500 dark:text-gray-400">
				<Sparkles className="size-3.5" />
			</div>
			<div
				class="{isActive ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1"
			>
				{description}
			</div>
		</div>
	{/if}
</div>
