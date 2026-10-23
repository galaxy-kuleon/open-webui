type UploadModel = {
	id: string;
	info?: { meta?: { capabilities?: Record<string, boolean> } };
};

export const usesRawFileUploads = (ids: string[], models: UploadModel[]) =>
	ids.some(
		(id) => models.find((model) => model.id === id)?.info?.meta?.capabilities?.raw_file_uploads
	);

export const shouldProcessFileUpload = (ids: string[], models: UploadModel[]) =>
	ids.length === 0 ||
	ids.some((id) => {
		const capabilities = models.find((model) => model.id === id)?.info?.meta?.capabilities ?? {};
		return (
			!capabilities.skip_rag &&
			capabilities.file_context !== false &&
			!capabilities.raw_file_uploads
		);
	});
