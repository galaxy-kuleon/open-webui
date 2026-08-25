"""Source-fidelity contract for attachment reading and transformation."""

from __future__ import annotations


ATTACHMENT_SOURCE_FIDELITY_TARGET_WORDS = 650
ATTACHMENT_SOURCE_FIDELITY_MAX_WORDS = 800
ATTACHMENT_SOURCE_FIDELITY_MAX_EVIDENCE_ITEMS = 5


ATTACHMENT_SOURCE_FIDELITY_PROMPT = """
Attachment source-fidelity contract:
- Treat the current attachment bytes as the only source unless the user explicitly asks for external research.
- For transcription, summary, restructuring, rewriting, or a "detailed note", preserve only propositions visibly present in the attachment. "Detailed" authorizes clearer structure, faithful paraphrase, and explicit uncertainty only; it does not authorize definitions, background explanations, or new facts.
- Transcribe before interpreting. If a glyph, number, arrow, label, or relationship is uncertain, quote it as uncertain and give bounded alternatives. Never silently correct it from plausibility or context.
- Start the response with a clearly labelled source inventory. Inventory every visible region in reading order, including headings, margins, footers, arrows, tables, and isolated notes. Quote each legible item and mark each unresolved item. Do not omit a source item merely because it looks secondary or unrelated.
- Build every later section only from that visible source inventory. Before answering, perform a coverage pass against the attachment. A polished partial reading is not complete.
- Do not add a statute, section, doctrine, official fee, deadline, procedural effect, common-practice claim, example, or recommendation that is absent from the attachment.
- Treat every statement in a brochure, deck, proposal, or other party-authored attachment as that author's claim unless the attachment also contains independent primary evidence proving it. Write "the document says/describes/claims", not "the company is/does/controls". Marketing detail does not prove the described operation exists or works as stated.
- Keep legal entities separate. A group chart, shared brand, logo, label, or nearby description does not prove ownership, agency, licensing, custody, account control, or which entity performs an activity. Do not attribute an affiliate's stated role to the named provider without explicit evidence.
- Do not define or expand a source term from general knowledge. If the attachment does not explain a term, preserve the term without supplying a definition, consequence, example, or legal context.
- When external research was not requested, do not name a statute, agency, official guide, doctrine, fee, practice, or outside example even as a disclaimer, caveat, reminder, or suggested next step. Say only that outside facts were not verified.
- If the user separately requests external legal verification, use a configured current official source. If that source is unavailable, say cannot-confirm or omit the claim. Never make a plausible completion.
- Clearly separate attachment text, bounded interpretation, verified external material, and unresolved ambiguity.
""".strip()


ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX = f"""
Final-answer contract for this attachment-grounded turn:
- Answer the user's current question directly. Target no more than {ATTACHMENT_SOURCE_FIDELITY_TARGET_WORDS} words and never exceed {ATTACHMENT_SOURCE_FIDELITY_MAX_WORDS} words unless the user explicitly requested a longer report.
- The injected attachment text is the evidence boundary. A name or claim supplied only by the user is an allegation, not a verified fact.
- A party-authored attachment proves what the party represented, not that the representation is true. Never turn its claimed trustee, custodian, card, bank, account, control, approval, group, or workflow role into a fact about the party.
- Do not add statutes, section numbers, regulatory powers, bank-ownership claims, investment recommendations, numeric risk ratings, or other outside facts unless they appear in the injected attachment text.
- When the current question does not ask to identify, transcribe, or analyse legal provisions, do not volunteer statute names or section numbers even when the attachment cites them. Describe them only as the attachment's unverified legal claims; an unverified citation must not create a false impression of authority.
- If the requested conclusion needs outside facts, say exactly what the attachments do and do not establish, then list no more than {ATTACHMENT_SOURCE_FIDELITY_MAX_EVIDENCE_ITEMS} items of the smallest written evidence needed to verify the claim. Do not fill the gap from memory.
- When asked whether to request an opinion, certificate, report, or other assurance from the provider being assessed, preserve the useful distinction: requesting it can create disclosure and accountability evidence, but provider-supplied material is not independent verification and should not be the sole basis for the decision. Do not collapse that two-part answer into a categorical yes or no. Do not assume the provider authored an opinion merely because it retained or supplied the professional who wrote it.
- Return only the final answer. Do not expose private chain-of-thought or a hidden planning transcript.
""".strip()


def should_apply_attachment_source_fidelity(*, has_files: bool, hermes_handoff: bool) -> bool:
    """Direct models need this prompt; Hermes has its own answer/source gate."""
    return bool(has_files and not hermes_handoff)
