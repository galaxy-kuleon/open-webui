<script lang="ts">
	import { getContext } from 'svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	const capabilityLabels = {
		vision: {
			label: $i18n.t('Vision'),
			description: $i18n.t('Model accepts image inputs')
		},
		file_upload: {
			label: $i18n.t('File Upload'),
			description: $i18n.t('Model accepts file inputs')
		},
		file_context: {
			label: $i18n.t('File Context'),
			description: $i18n.t('Inject file content into conversation context')
		},
		skip_rag: {
			label: $i18n.t('Skip RAG'),
			description: $i18n.t(
				'Skip all RAG processing (embedding, retrieval, knowledge base). Files are converted to Markdown and injected directly into the prompt.'
			)
		},
		delegated_orchestration: {
			label: $i18n.t('Delegated Orchestration'),
			description: $i18n.t(
				'Model handles its own skill routing, tool use and file context. Middleware will not run agent-skill keyword intercept and will not inject Skip RAG / RAG content for this model. Use for upstream agent pipes (e.g. Hermes) that already manage their own context.'
			)
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Model can search the web for information')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Model can generate images based on text prompts')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Model can execute code and perform calculations')
		},
		terminal: {
			label: $i18n.t('Terminal'),
			description: $i18n.t(
				'Model can access Open Terminal for command execution and file management'
			)
		},
		usage: {
			label: $i18n.t('Usage'),
			description: $i18n.t(
				'Sends `stream_options: { include_usage: true }` in the request.\nSupported providers will return token usage information in the response when set.'
			)
		},
		citations: {
			label: $i18n.t('Citations'),
			description: $i18n.t('Displays citations in the response')
		},
		status_updates: {
			label: $i18n.t('Status Updates'),
			description: $i18n.t('Displays status updates (e.g., web search progress) in the response')
		},
		builtin_tools: {
			label: $i18n.t('Builtin Tools'),
			description: $i18n.t(
				'Automatically inject system tools in native function calling mode (e.g., timestamps, memory, chat history, notes, etc.)'
			)
		}
	};

	export let capabilities: {
		file_context?: boolean;
		vision?: boolean;
		file_upload?: boolean;
		web_search?: boolean;
		image_generation?: boolean;
		code_interpreter?: boolean;
		terminal?: boolean;
		usage?: boolean;
		citations?: boolean;
		status_updates?: boolean;
		builtin_tools?: boolean;
		skip_rag?: boolean;
		delegated_orchestration?: boolean;
	} = {};

	// Hide file-dependent capabilities when file_upload is disabled
	$: visibleCapabilities = Object.keys(capabilityLabels).filter((cap) => {
		if ((cap === 'file_context' || cap === 'skip_rag') && !capabilities.file_upload) {
			return false;
		}
		return true;
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-xs font-medium text-gray-500">{$i18n.t('Capabilities')}</div>
	</div>
	<div class="flex items-center mt-2 flex-wrap">
		{#each visibleCapabilities as capability}
			<div class=" flex items-center gap-2 mr-3">
				<Checkbox
					state={capabilities[capability] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						capabilities[capability] = e.detail === 'checked';
					}}
				/>

				<div class=" py-0.5 text-sm capitalize">
					<Tooltip content={marked.parse(capabilityLabels[capability].description)}>
						{$i18n.t(capabilityLabels[capability].label)}
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>
</div>
