<script lang="ts">
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import { getContext } from 'svelte';

	import ProfileImage from '../Messages/ProfileImage.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Heart from '$lib/components/icons/Heart.svelte';

	import { causeLabel, hasStructuredEmptyError } from '$lib/utils/empty_turn';

	const i18n = getContext('i18n');

	type $$Props = NodeProps;
	export let data: $$Props['data'];

	// #16: this node printed `error.content` verbatim, and the STORED backend banner
	// embeds the raw cause CODE. So Controls -> Overview was a second screen handing
	// a routing key to a person, while the message bubble beside it showed words.
	// Adversarial review found it on live data — 29 of 64 stored error rows carry the
	// literal `cause: ` — and it refutes the claim that the browser always rebuilds
	// the sentence. It rebuilds it in ONE of the two places a person can look.
	//
	// A legacy error (a plain {content} with no cause) still shows its own text: that
	// IS the message someone needs, and it is not a #16 turn.
	$: errorText = hasStructuredEmptyError(data?.message)
		? `This turn ended without a final answer (${causeLabel(data.message.error.cause)}).`
		: data?.message?.error?.content;
</script>

<div
	class="px-4 py-3 shadow-md rounded-xl dark:bg-black bg-white border border-gray-100 dark:border-gray-900 w-60 h-20 group"
>
	<Tooltip
		content={data?.message?.error ? errorText : data.message.content}
		class="w-full"
		allowHTML={false}
	>
		{#if data.message.role === 'user'}
			<div class="flex w-full">
				<ProfileImage
					src={`${WEBUI_API_BASE_URL}/users/${data.user.id}/profile/image`}
					className={'size-5 -translate-y-[1px] flex-shrink-0'}
				/>
				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{data?.user?.name ?? 'User'}
						</div>
					</div>

					{#if data?.message?.error}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">{errorText}</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{data.message.content}</div>
					{/if}
				</div>
			</div>
		{:else}
			<div class="flex w-full">
				<ProfileImage
					src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${data.model?.id ?? data.message.model}&lang=${$i18n.language}`}
					className={'size-5 -translate-y-[1px] flex-shrink-0'}
				/>

				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{data?.model?.name ?? data?.message?.model ?? 'Assistant'}
						</div>

						<button
							class={data?.message?.favorite ? '' : 'invisible group-hover:visible'}
							aria-label={data?.message?.favorite
								? $i18n.t('Remove from favorites')
								: $i18n.t('Add to favorites')}
							on:click={() => {
								data.message.favorite = !(data?.message?.favorite ?? false);
							}}
						>
							<Heart
								className="size-3 {data?.message?.favorite
									? 'fill-red-500 stroke-red-500'
									: 'hover:fill-red-500 hover:stroke-red-500'} "
								strokeWidth="2.5"
							/>
						</button>
					</div>

					{#if data?.message?.error}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">
							{errorText}
						</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{data.message.content}</div>
					{/if}
				</div>
			</div>
		{/if}
	</Tooltip>
	<Handle type="target" position={Position.Top} class="w-2 rounded-full dark:bg-gray-900" />
	<Handle type="source" position={Position.Bottom} class="w-2 rounded-full dark:bg-gray-900" />
</div>
