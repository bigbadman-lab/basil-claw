# Safety & Constraint Model

## Design Principle

Basil is **retrieval-grounded** and **persona-bound**. It is not an open-ended chatbot. All factual or policy-related content in replies is intended to be grounded in retrieved source chunks and in the canon; the model is instructed to follow the canon strictly and not to invent facts. The system is designed so that auditability is possible: sources and chunks can be traced, and reply generation is constrained by explicit prompt rules and by whether retrieval returned any context.

---

## Hallucination Mitigation

- **Retrieval-first grounding:** For intents `policy_question` and `other`, the pipeline embeds the user message, retrieves top-k chunks (subject to thresholds and diversity), and passes them to the reply generator as a **context block**. The system prompt includes this block (or "[no retrieved context]" when empty). The model is instructed to use the canon and the provided context; it is not given a general knowledge mandate.

- **Threshold rejection:** Retrieval uses **BEST_MATCH_MAX** and **KEEP_MATCH_MAX**. If the best adjusted distance among selected chunks exceeds BEST_MATCH_MAX, retrieval returns an empty list and no context is passed. Chunks with adjusted distance above KEEP_MATCH_MAX are dropped from the returned set. This reduces the chance of grounding on weak or irrelevant matches that could encourage confabulation.

- **Context block inclusion:** The assembled context (chunk_id, source_title, chunk_text per chunk) is injected into the user-side prompt so the model sees exactly what was retrieved. There is no separate “hidden” grounding layer; the same context the model sees is what the system considers the factual basis.

- **“No invented facts” rule:** The system prompt states: “Do not invent facts.” This is a direct instruction to the model. Compliance is not programmatically enforced; the constraint is contractual in the prompt and is reinforced by supplying retrieval context only when it exists and when it passed distance thresholds.

---

## Public Figure Commentary

- **Persona stance files are author-defined:** Content about public figures (e.g. “Elon Musk”, “Keir Starmer”) is sourced from markdown stance files authored and ingested by the operator. The system does not pull real-time or third-party claims about figures; it only retrieves from the ingested corpus.

- **No allegations of criminality without retrieved source:** The design intent is that factual claims about illegal conduct should not appear in replies unless they are present in the retrieved context. The model is instructed not to invent facts; it is not given a separate “no unsubstantiated criminal allegations” rule. Risk is mitigated by retrieval grounding and by the author’s control of stance content.

- **No speculation about mental state or private character:** The intent is to keep commentary ideological and policy-oriented rather than personal or clinical. Stance files and canon can be written to avoid speculation about mental health or private character; the model is not given an explicit rule forbidding it. Operator-authored content is the primary control.

- **Criticism framed ideologically, not as factual accusation:** Stance files are intended to express positions and criticism in policy/ideological terms. The system does not add a separate “frame as opinion not fact” instruction; the canon and style rules (dry wit, confident, no invented facts) are the main levers.

---

## Named-Entity Anchoring Safeguards

- **Lexical anchor limits candidate pool:** When the query contains specific entity terms (e.g. “elon”, “musk”), retrieval adds a SQL predicate so that only chunks whose source title or chunk text contains at least one of those terms (via ILIKE) are considered. This **restricts the candidate pool** to chunks that lexically mention the entity.

- **Prevents irrelevant stance bleed:** Without the lexical gate, a generic “what do you think about X?” query could retrieve high-similarity chunks about other figures or topics. The anchor ensures that only chunks that mention the entity can be returned, reducing the chance of attributing one figure’s stance to another.

- **Reduces cross-figure contamination:** By requiring the entity string to appear in the source title or chunk text, the system avoids returning chunks from unrelated stance files that happen to be close in embedding space. The entity bonus (subtracting a fixed value from adjusted distance when both query and chunk mention the entity) further promotes the correct stance document in ranking.

---

## Intent Classification Guardrails

The pipeline uses a simple regex/keyword **intent classifier** with four outcomes:

| Intent | Trigger (examples) | Effect on retrieval | Effect on reply |
|--------|--------------------|---------------------|-----------------|
| **casual** | Greetings, “gm”, “how are you”, etc. | No retrieval. `retrieved` is empty. | Canon + style only. Optional mission-hook hint in prompt. No context block. |
| **policy_question** | Policy keywords (“tax”, “immigration”, “nhs”, …), “how do we”, “what would you” | Embed query, run retrieval (with thresholds, diversity, optional entity anchor). | Context block included. Reply grounded in retrieved chunks + canon. |
| **abuse_bait** | Keywords such as “idiot”, “stupid”, “moron”, “kill”, “die”, “traitor” | No retrieval. | Reply uses canon + style; no context. Model can be prompted to disengage or pivot (canon-dependent). |
| **other** | Default when none of the above match | Same as policy_question: retrieval runs. | Context block included. |

Retrieval runs **only** for `policy_question` and `other`. For `casual` and `abuse_bait`, the context block is empty and the model receives “[no retrieved context]”. So factual grounding via retrieval is only attempted when the intent is classified as policy-like or other; casual and abuse_bait replies rely on canon and style rules only.

---

## Style Constraints

These are enforced via the system prompt, not by post-processing:

- **1–2 sentences (max 240 characters):** The prompt specifies short replies and a character cap. The model is not truncated programmatically; adherence is prompt-based.
- **No hashtags:** Explicit rule in the prompt.
- **No links unless asked:** Explicit rule; “unless asked” leaves room for link inclusion when the user requests it.
- **Canon voice adherence:** The full canon is injected into the system prompt and the model is told to “Follow the canon below strictly.” Voice and persona are defined in the canon (e.g. Victorian parliamentary, dry wit, lobster motifs).
- **No bullet points in replies:** Explicit rule in the prompt.

Violations (e.g. over-length, hashtags) are not stripped or corrected in code; the only enforcement is the prompt and model behaviour.

---

## Automated Reply Loop (Planned)

- The schema includes **x_mentions**, **x_replies**, and **x_cursor** for an automated X (Twitter) mention-response loop. Mentions would be read, processed (intent, retrieval, reply generation), and replies logged to **x_replies** with fields such as mention_tweet_id, reply_text, decision, model, rag_topk, citations_json, moderation_json.
- **Cursor tracking** (e.g. `x_cursor.since_id`) is intended to support incremental fetch of mentions so that each mention is processed once.
- A **rate limiting and moderation layer** is not yet implemented. Before enabling automated posting, the design assumes addition of rate limits, optional human-in-the-loop or approval gates, and possibly content filters or pre-post checks. The current code is suitable for single-query testing (e.g. `test_reply.py`), not for unattended production posting.

---

## Known Risk Areas

1. **Threshold miscalibration:** If BEST_MATCH_MAX or KEEP_MATCH_MAX are too low, valid matches can be rejected and replies will have no context. If too high, weak or off-topic chunks can be used for grounding and increase hallucination or irrelevance risk.
2. **Poorly written stance files:** Stance content is author-defined. Inaccurate, libellous, or overly strong wording in markdown will be retrievable and can be reflected in replies. Quality and legal review of ingested stance files is the operator’s responsibility.
3. **Incomplete entity keyword coverage:** Named-entity anchoring depends on the query and stored text sharing the same keywords. Alternate spellings, nicknames, or titles that do not appear in the lexical gate will not trigger the filter; vector search alone may then return the wrong figure’s stance or generic content.
4. **Embedding drift:** Changing the embedding model or version without re-ingesting can make distances incomparable. Model filtering (storing and filtering by model name) mitigates this when the embeddings table has a model column and retrieval uses it.
5. **Over-broad lexical anchors:** If the same keyword appears in multiple stance files (e.g. a common word mistaken for an entity term), the lexical gate can include too many candidates and dilute ranking. Entity terms should be chosen to be specific to the intended figure or topic.

---

## Affiliation Disclosure Principle

- Basil may express alignment with Restore Britain.
- Basil must not incite hostility, harassment, or unlawful action.
- Political commentary must remain civil and grounded.
- Retrieval-first grounding remains mandatory.
