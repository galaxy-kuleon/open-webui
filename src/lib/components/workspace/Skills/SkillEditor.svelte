<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import { user } from '$lib/stores';
	import { slugify, parseFrontmatter, formatSkillName } from '$lib/utils';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { updateSkillAccessGrants } from '$lib/apis/skills';
	import { goto } from '$app/navigation';

	export let onSubmit: Function;
	export let edit = false;
	export let skill = null;
	export let clone = false;
	export let disabled = false;

	const i18n = getContext('i18n');

	let loading = false;

	let name = '';
	let id = '';
	let description = '';
	let content = '';

	// Agent skill meta fields
	let metaType: string = '';
	let workDir: string = '';
	let diskPath: string = '';
	// Preserve fields that have no UI — they survive round-trips untouched.
	let extraMeta: Record<string, any> = {};

	let accessGrants = [];
	let showAccessControlModal = false;
	let hasManualEdit = false;
	let hasManualName = false;
	let hasManualDescription = false;
	let isFrontmatterDetected = false;

	// Auto-detect frontmatter and fill name/description in create mode
	$: if (!edit && content) {
		const fm = parseFrontmatter(content);
		if (fm.name) {
			isFrontmatterDetected = true;
			if (!hasManualName) {
				name = formatSkillName(fm.name);
			}
			if (!hasManualEdit) {
				id = fm.name;
			}
		} else {
			isFrontmatterDetected = false;
		}
		if (fm.description && !hasManualDescription) {
			description = fm.description;
		}
	} else if (!edit && !content) {
		isFrontmatterDetected = false;
	}

	$: if (!edit && !hasManualEdit && !isFrontmatterDetected) {
		id = name !== '' ? slugify(name) : '';
	}

	function handleIdInput(e: Event) {
		hasManualEdit = true;
	}

	function handleNameInput(e: Event) {
		hasManualName = true;
	}

	function handleDescriptionInput(e: Event) {
		hasManualDescription = true;
	}

	const submitHandler = async () => {
		if (disabled) {
			toast.error($i18n.t('You do not have permission to edit this skill.'));
			return;
		}
		loading = true;

		// Start from extraMeta so fields without UI (idle_timeout, tags, …)
		// survive the round-trip instead of being silently wiped.
		const meta: Record<string, any> = { ...extraMeta };
		if (!meta.tags) meta.tags = [];
		if (metaType) meta.type = metaType;
		else delete meta.type;
		if (workDir.trim()) meta.work_dir = workDir.trim();
		else delete meta.work_dir;
		if (diskPath.trim()) meta.disk_path = diskPath.trim();
		else delete meta.disk_path;

		await onSubmit({
			id,
			name,
			description,
			content,
			is_active: true,
			meta,
			access_grants: accessGrants
		});

		loading = false;
	};

	onMount(async () => {
		if (skill) {
			name = skill.name || '';
			await tick();
			id = skill.id || '';
			description = skill.description || '';
			content = skill.content || '';
			accessGrants = skill?.access_grants === undefined ? [] : skill?.access_grants;

			if (skill.meta) {
				metaType = skill.meta.type || '';
				workDir = skill.meta.work_dir || '';
				diskPath = skill.meta.disk_path || '';
				// Capture all meta fields so those without UI controls
				// (idle_timeout, tags, future additions) survive saves.
				const { type: _t, work_dir: _w, disk_path: _d, ...rest } = skill.meta;
				extraMeta = rest;
			}

			if (name) hasManualName = true;
			if (description) hasManualDescription = true;
			if (id) hasManualEdit = true;
		}
	});
</script>

<AccessControlModal
	bind:show={showAccessControlModal}
	bind:accessGrants
	accessRoles={['read', 'write']}
	share={$user?.permissions?.sharing?.skills || $user?.role === 'admin'}
	sharePublic={$user?.permissions?.sharing?.public_skills || $user?.role === 'admin'}
	shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) || $user?.role === 'admin'}
	onChange={async () => {
		if (edit && skill?.id) {
			try {
				await updateSkillAccessGrants(localStorage.token, skill.id, accessGrants);
				toast.success($i18n.t('Saved'));
			} catch (error) {
				toast.error(`${error}`);
			}
		}
	}}
/>

<div class=" flex flex-col justify-between w-full overflow-y-auto h-full">
	<div class="mx-auto w-full md:px-0 h-full">
		<form class=" flex flex-col max-h-[100dvh] h-full" on:submit|preventDefault={submitHandler}>
			<div class="flex flex-col flex-1 overflow-auto h-0 rounded-lg">
				<div class="w-full mb-2 flex flex-col gap-0.5">
					<div class="flex w-full items-center">
						<div class=" shrink-0 mr-2">
							<Tooltip content={$i18n.t('Back')}>
								<button
									class="w-full text-left text-sm py-1.5 px-1 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
									aria-label={$i18n.t('Back')}
									on:click={() => {
										goto('/workspace/skills');
									}}
									type="button"
								>
									<ChevronLeft strokeWidth="2.5" />
								</button>
							</Tooltip>
						</div>

						<div class="flex-1">
							<Tooltip content={$i18n.t('e.g. Code Review Guidelines')} placement="top-start">
								<input
									class="w-full text-2xl bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('Skill Name')}
									aria-label={$i18n.t('Skill Name')}
									bind:value={name}
									on:input={handleNameInput}
									required
									{disabled}
								/>
							</Tooltip>
						</div>

						<div class="self-center shrink-0">
							{#if !disabled}
								<button
									class="bg-gray-50 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
									type="button"
									on:click={() => (showAccessControlModal = true)}
								>
									<LockClosed strokeWidth="2.5" className="size-3.5" />

									<div class="text-sm font-medium shrink-0">
										{$i18n.t('Access')}
									</div>
								</button>
							{:else}
								<span
									class="text-xs text-gray-500 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded-full"
									>{$i18n.t('Read Only')}</span
								>
							{/if}
						</div>
					</div>

					<div class=" flex gap-2 px-1 items-center">
						{#if edit}
							<div class="text-sm text-gray-500 shrink-0">
								{id}
							</div>
						{:else}
							<Tooltip
								className="w-full"
								content={$i18n.t('e.g. code-review-guidelines')}
								placement="top-start"
							>
								<input
									class="w-full text-sm disabled:text-gray-500 bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('Skill ID')}
									aria-label={$i18n.t('Skill ID')}
									bind:value={id}
									on:input={handleIdInput}
									required
									disabled={edit}
								/>
							</Tooltip>
						{/if}

						<Tooltip
							className="w-full self-center items-center flex"
							content={$i18n.t('e.g. Step-by-step instructions for code reviews')}
							placement="top-start"
						>
							<input
								class="w-full text-sm bg-transparent outline-hidden"
								type="text"
								placeholder={$i18n.t('Skill Description')}
								aria-label={$i18n.t('Skill Description')}
								bind:value={description}
								on:input={handleDescriptionInput}
								{disabled}
							/>
						</Tooltip>
					</div>

					<!-- Execution type + agent skill settings -->
					<div class="mt-2 px-1 flex flex-col gap-2">
						<div class="flex items-center gap-3">
							<label class="text-xs text-gray-500 dark:text-gray-400 shrink-0 w-20"
								>{$i18n.t('Type')}</label
							>
							<select
								class="text-xs bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-lg px-2 py-1 outline-hidden"
								bind:value={metaType}
								{disabled}
							>
								<option value="">{$i18n.t('Markdown (default)')}</option>
								<option value="agent_skill">{$i18n.t('Agent Skill (OpenCode)')}</option>
							</select>
						</div>

						{#if metaType === 'agent_skill'}
							<div class="flex items-center gap-3">
								<label class="text-xs text-gray-500 dark:text-gray-400 shrink-0 w-20"
									>{$i18n.t('Work Dir')}</label
								>
								<Tooltip
									className="w-full"
									content={$i18n.t(
										'Absolute path to the project directory. OpenCode runs directly here (direct-dir mode). Takes precedence over Disk Path.'
									)}
									placement="top-start"
								>
									<input
										class="w-full text-xs bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-lg px-2 py-1 font-mono outline-hidden"
										type="text"
										placeholder="/path/to/project"
										aria-label={$i18n.t('Work Directory')}
										bind:value={workDir}
										{disabled}
									/>
								</Tooltip>
							</div>

							<div class="flex items-center gap-3">
								<label class="text-xs text-gray-500 dark:text-gray-400 shrink-0 w-20"
									>{$i18n.t('Disk Path')}</label
								>
								<Tooltip
									className="w-full"
									content={$i18n.t(
										'Absolute path to the skill files directory. Used in sandbox mode when Work Dir is not set.'
									)}
									placement="top-start"
								>
									<input
										class="w-full text-xs bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-lg px-2 py-1 font-mono outline-hidden"
										type="text"
										placeholder="/path/to/.claude/skills/my-skill"
										aria-label={$i18n.t('Disk Path')}
										bind:value={diskPath}
										{disabled}
									/>
								</Tooltip>
							</div>
						{/if}
					</div>
				</div>

				<div class="mb-2 flex-1 overflow-auto h-0 rounded-lg">
					<div class="h-full flex flex-col">
						<div
							class="bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-100/50 dark:border-gray-850/50 flex-1 min-h-0 overflow-hidden flex flex-col"
						>
							{#if disabled}
								<div class="px-4 py-3 overflow-y-auto flex-1">
									<pre class="text-xs whitespace-pre-wrap font-mono">{content}</pre>
								</div>
							{:else}
								<textarea
									class="w-full flex-1 text-xs bg-transparent outline-hidden resize-none font-mono px-4 py-3"
									bind:value={content}
									placeholder={$i18n.t('Enter skill instructions in markdown...')}
									aria-label={$i18n.t('Skill Instructions')}
									required
								/>
							{/if}
						</div>
					</div>
				</div>

				<div class="pb-3 flex justify-end">
					{#if !disabled}
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2 whitespace-nowrap"
							type="submit"
							disabled={loading}
						>
							{$i18n.t(edit ? 'Save' : 'Save & Create')}
							{#if loading}
								<span class="shrink-0">
									<Spinner />
								</span>
							{/if}
						</button>
					{/if}
				</div>
			</div>
		</form>
	</div>
</div>
