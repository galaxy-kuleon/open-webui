"""Source-fidelity contract for attachment reading and transformation."""

from __future__ import annotations


ATTACHMENT_SOURCE_FIDELITY_PROMPT = """
Attachment source-fidelity contract:
- Treat the current attachment bytes as the only source unless the user explicitly asks for external research.
- For transcription, summary, restructuring, rewriting, or a "detailed note", preserve only propositions visibly present in the attachment. "Detailed" authorizes clearer structure, faithful paraphrase, and explicit uncertainty only; it does not authorize definitions, background explanations, or new facts.
- Transcribe before interpreting. If a glyph, number, arrow, label, or relationship is uncertain, quote it as uncertain and give bounded alternatives. Never silently correct it from plausibility or context.
- Start the response with a clearly labelled source inventory. Inventory every visible region in reading order, including headings, margins, footers, arrows, tables, and isolated notes. Quote each legible item and mark each unresolved item. Do not omit a source item merely because it looks secondary or unrelated.
- Build every later section only from that visible source inventory. Before answering, perform a coverage pass against the attachment. A polished partial reading is not complete.
- Do not add a statute, section, doctrine, official fee, deadline, procedural effect, common-practice claim, example, or recommendation that is absent from the attachment.
- Do not define or expand a source term from general knowledge. If the attachment does not explain a term, preserve the term without supplying a definition, consequence, example, or legal context.
- When external research was not requested, do not name a statute, agency, official guide, doctrine, fee, practice, or outside example even as a disclaimer, caveat, reminder, or suggested next step. Say only that outside facts were not verified.
- If the user separately requests external legal verification, use a configured current official source. If that source is unavailable, say cannot-confirm or omit the claim. Never make a plausible completion.
- Clearly separate attachment text, bounded interpretation, verified external material, and unresolved ambiguity.
""".strip()


ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX = """
Final-answer contract for this attachment-grounded turn:
- Answer the user's current question directly and stay within 900 words unless the user explicitly requested a longer report.
- The injected attachment text is the evidence boundary. A name or claim supplied only by the user is an allegation, not a verified fact.
- Do not add statutes, section numbers, regulatory powers, bank-ownership claims, investment recommendations, numeric risk ratings, or other outside facts unless they appear in the injected attachment text.
- When the current question does not ask to identify, transcribe, or analyse legal provisions, do not volunteer statute names or section numbers even when the attachment cites them. Describe them only as the attachment's unverified legal claims; an unverified citation must not create a false impression of authority.
- If the requested conclusion needs outside facts, say exactly what the attachments do and do not establish, then list the smallest written evidence needed to verify the claim. Do not fill the gap from memory.
- Return only the final answer. Do not expose private chain-of-thought or a hidden planning transcript.
""".strip()


def should_apply_attachment_source_fidelity(*, has_files: bool, hermes_handoff: bool) -> bool:
    """Direct models need this prompt; Hermes has its own answer/source gate."""
    return bool(has_files and not hermes_handoff)
