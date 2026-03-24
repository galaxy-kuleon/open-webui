# Server Log 30-Round Observation

## Date: 2026-03-24

**Status:** COMPLETED

## Method

- Observed `server.log` in 30 rounds
- Each round sampled the last 100 lines
- Slept 60 seconds between rounds

## What Happened

- Early rounds were mostly quiet after restart; organizer baseline looked healthy.
- KG1 processing for a large PDF ramped up, with `149 pages detected` and a computed timeout of `4470s`.
- The dominant problem during the observation window was repeated OCR / Ollama timeouts to `127.0.0.1:11434`.
- The most important failure was a document index generation timeout, not an OpenCode organizer failure.
- The patched organizer then ran successfully, returned a JSON move plan, and moved `doc-0001` into `healthcare/accreditation`.
- After the organizer success, no new severe errors appeared; later rounds mostly repeated the same KG1 timeout noise.

## Key Evidence

- `server.log:2118` - KG1 detected `149 pages`, timeout `4470s`
- `server.log:2129` - document index generation started for the large PDF
- `server.log:2132` onward - repeated `OCR API request error` timeouts against `127.0.0.1:11434`
- `server.log:2168` - `Document index generation failed ... TimeoutError`
- `server.log:2192` - patched organizer run step started
- `server.log:2200` - organizer emitted JSON move plan
- `server.log:2202` - organizer moved `doc-0001` to `healthcare/accreditation`
- `server.log:2203` - organizer reported `organization plan applied successfully`

## Conclusion

- The current hot path problem is KG1 / OCR backend timeout behavior, not OpenCode organizer behavior.
- The organizer patch appears to be working: it planned, moved the file correctly, and did not loop on CJK filenames.
- If the user still sees instability, the next place to inspect is the OCR service behind `127.0.0.1:11434` and the document-index pipeline.

## Expansion Log

- Ran 30 x 60-second log observations using last-100-line snapshots
- Checked final round via targeted tool-output inspection
- Correlated the observation with `server.log` line references
