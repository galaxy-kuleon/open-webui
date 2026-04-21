import JSZip from 'jszip';

/**
 * DOCX (OOXML) inspection helpers for E2E tests.
 *
 * Pure functions: Buffer/string in, data out. No side effects.
 * Shared by conversion-e2e.spec.ts and translation-e2e.spec.ts.
 */

/**
 * Check if a buffer (zip archive) contains a given filename in its entries.
 *
 * ZIP format stores filenames as plaintext in both local file headers and
 * the central directory. We search for the UTF-8 encoded filename bytes
 * in the raw buffer. This avoids adding a zip library dependency for a
 * simple presence check.
 *
 * Pure function: Buffer in, boolean out.
 */
export function zipContainsEntry(buffer: Buffer, entryName: string): boolean {
	const needle = Buffer.from(entryName, 'utf-8');
	// Search through the buffer for the entry name
	for (let i = 0; i <= buffer.length - needle.length; i++) {
		let found = true;
		for (let j = 0; j < needle.length; j++) {
			if (buffer[i + j] !== needle[j]) {
				found = false;
				break;
			}
		}
		if (found) return true;
	}
	return false;
}

/**
 * Extract raw text content from a DOCX buffer by reading word/document.xml.
 *
 * OOXML stores text in <w:t> tags within word/document.xml. We extract the
 * raw XML and strip tags to get plaintext. This is intentionally simple —
 * full XML parsing would be accidental complexity for a content-presence check.
 *
 * Pure function: Buffer in, string out (async due to JSZip).
 */
export async function extractDocxText(buffer: Buffer): Promise<string> {
	const zip = await JSZip.loadAsync(buffer);
	const documentXml = zip.file('word/document.xml');
	if (!documentXml) return '';
	const xmlContent = await documentXml.async('string');
	// Strip XML tags to get plaintext — sufficient for marker-based content checks
	return xmlContent.replace(/<[^>]+>/g, '');
}

/**
 * Count how many content markers are found in the given text.
 *
 * Pure function: (text, markers) -> { count, matched, missed }.
 * Case-insensitive search for robustness against OCR/VLM/translation casing variance.
 */
export function countMarkerMatches(
	text: string,
	markers: readonly string[]
): { count: number; matched: string[]; missed: string[] } {
	const lowerText = text.toLowerCase();
	const matched: string[] = [];
	const missed: string[] = [];
	for (const marker of markers) {
		if (lowerText.includes(marker.toLowerCase())) {
			matched.push(marker);
		} else {
			missed.push(marker);
		}
	}
	return { count: matched.length, matched, missed };
}
