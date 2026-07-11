# Decision Contract — <BLOCK_ID>: <SHORT TITLE>

<!--
HOW TO USE THIS TEMPLATE

This file is filled in and COMMITTED **BEFORE** any implementation code is
written. That is the whole point. A decision contract written after the code is
a reconstruction, not a contract — see the provenance note on
M8_RF_decision_contract.md for what that looks like and why we stopped doing it.

Workflow:
  1. Copy to docs/validation/<BLOCK_ID>_decision_contract.md
  2. Fill §1-§7. Leave §8 sign-off UNSIGNED.
  3. Review. Iterate until every open question is closed.
  4. Sign §8. Commit. THIS COMMIT PRECEDES THE FIRST LINE OF BLOCK CODE.
  5. Only then implement.
  6. Close the block with <BLOCK_ID>_completion.md, which references this
     contract's commit hash as the approved plan.

If a decision changes mid-block, do NOT silently edit this file. Append to §9
(Amendments) with the reason, and re-sign. The contract is an audit artifact;
its edit history is part of the evidence.

Delete these HTML comments when filling the template.
-->

**Block ID:** <BLOCK_ID>
**Prior sealed state:** <PRIOR_BLOCK> — commit `<HASH>`
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Machine of record:** `olaberry`
**Contract written:** <YYYY-MM-DD>
**Status:** `DRAFT` → `DECISIONS CONFIRMED` (set only at §8 sign-off, before code)

> **Provenance:** This contract was written and committed **before**
> implementation of this block began. Corresponding evidence:
> `docs/validation/<BLOCK_ID>_completion.md`.

---

## 1. Objective

<!-- One paragraph. What is this block for, and what does it mature? Name the TRL
movement explicitly (e.g. "TRL 3 -> 4 maturation"), not a TRL achievement. -->

<OBJECTIVE>

This block begins **new, separately gated work**. It does not modify, rewrite, or
re-gate any sealed evidence or prior `docs/validation/` record.

---

## 2. Scope boundaries (binding)

<!-- Be explicit about what is OUT of scope. The out-of-scope list is what
prevents overclaiming later. Every "no X claim" here becomes a thing the
completion record is forbidden from asserting. -->

**In scope:**
- <ITEM>

**Explicitly out of scope — no claim of any kind may be made in these areas:**
- <ITEM>

**Data handling:** <e.g. "X is treated as civilian-derived sensitive data throughout.">

**Claim language (honesty constraint):** this block closes as
**"<EXACT PERMITTED CLAIM STRING>"**. It does **not** claim <FORBIDDEN STRONGER CLAIM>.

<!-- Write the permitted claim string verbatim. The completion record must use
this wording. "TRL 3 -> 4 maturation evidence" is not "TRL 4 achieved." -->

---

## 3. Decisions (confirmed before code)

<!-- One subsection per decision. For EACH: state the decision, then the
reasoning, then what was CONSIDERED AND REJECTED and why. The rejected
alternatives are the most valuable part of this document six months from now. -->

### D1 — <DECISION NAME>

**Decision: <the decision, stated flatly>**

<Reasoning.>

**Considered and rejected:** <alternative> — <why not>.

<!-- If a decision touches sealed code or a core contract, say so explicitly and
state whether it is approved as a logged core change or refused. Never allow a
core change to happen implicitly. -->

### D2 — <DECISION NAME>

**Decision: <...>**

### D3 — <DECISION NAME>

**Decision: <...>**

### D4 — <DECISION NAME>

**Decision: <...>**

<!-- Add/remove decisions as the block requires. Four is not a magic number. -->

---

## 4. Contract alignment (read the sealed code — do NOT reconstruct from memory)

<!-- MANDATORY GATE. Before writing code, READ the sealed files this block
touches. Paste the findings here. This section exists because reconstructing a
contract from memory is how contract-drift bugs get in.

List the exact files read. Then table what the sealed reality IS versus what this
block will do. Any mismatch between what you assumed and what the code actually
says goes here, loudly. -->

**Files read:**
- `<path>` — <what it defines>

**Findings:**

| Concern | Sealed reality | This block's action |
|---|---|---|
| <e.g. schema version> | <what the code actually does> | <what we do> |
| <core module> | <...> | **untouched** / **changed (logged, see Dn)** |

**Consequence:** <e.g. "the entire integration is achievable with zero core
changes. Total new surface: ...">

**Assumptions corrected by reading the code:** <list any. If none, say "none" —
but if you found none, double-check you actually read the files.>

---

## 5. Design / algorithm decisions (approved before code)

<!-- If the block introduces logic that makes a claim about the world (a mapper,
a detector, a scorer, a threshold), specify it HERE and get it approved BEFORE
coding. Include the guardrails as a table with concrete values.

State which properties are STRUCTURAL (impossible by construction) versus TUNED
(true only for current parameter values). Structural guarantees survive
refactoring; tuned ones do not. -->

<SPECIFICATION>

**Guardrails:**

| guardrail | value | purpose |
|---|---|---|
| <name> | <value> | <what it prevents> |

**Structural guarantees** (true by construction, cannot be tuned away):
- <e.g. "X is never a key the mapper writes, so no X mass can appear.">

**Provisional values** (NOT calibrated; no quantitative claim may rest on these):
- <name> — <why provisional, what would calibrate it>

<!-- Anything not fitted/calibrated against real data is PROVISIONAL and must be
declared here and in the completion record. A number that came from a guess is
not evidence. -->

---

## 6. Required validation (acceptance criteria agreed in advance)

<!-- Write the tests BEFORE the code. If you cannot state what would prove this
block works, you are not ready to build it. Every claim the completion record
will make needs a line here. -->

**Unit/property tests, mandatory:**

1. <test> — <what it proves>
2. <test> — <what it proves>

**Block-level validation:**
- <live/replayed evidence required>
- <lineage preserved through: A -> B -> C>
- <degraded / dropout / absent-input behaviour>
- <privacy & audit controls confirmed>

**Gate condition:** live end-to-end evidence on `olaberry`. Partner-machine
evidence is excluded.

---

## 7. Standing constraints

- Do **not** modify or re-gate sealed evidence or prior `docs/validation/` records.
- Do **not** claim <the forbidden claims from §2>.
- Do **not** alter core/sealed modules unless a decision in §3 explicitly
  approves it **and** a test first proves the additive path cannot work.
- Preserve existing contracts unless a versioned schema change is genuinely
  required.
- **Decisions-before-code.** This contract is signed and committed before
  implementation.
- Every new claim backed by a **test, log, or validation artifact**.
- Negative findings are reported **verbatim**. No threshold gaming.
- Close the block with `docs/validation/<BLOCK_ID>_completion.md` referencing
  `<PRIOR_BLOCK> <HASH>` as prior state and **this contract's commit** as the
  approved plan.

---

## 8. Sign-off

<!-- Do not sign until every open question in §1-§7 is closed. An unsigned
contract means implementation has not been authorised. -->

Decisions <D1-Dn>, the contract-alignment findings (§4), the design
specification and guardrails (§5), and the acceptance criteria (§6) are reviewed
and **approved. Implementation is authorised.**

**Approved refinements to the original proposal:**
- <refinement> — <rationale>

**Signed:** <NAME> — <YYYY-MM-DD>
**Contract commit:** `<filled in after commit; the completion record cites this>`

---

## 9. Amendments

<!-- Reality intrudes. When a decision must change mid-block, append here rather
than editing §3 in place. Each amendment states: what changed, why, what evidence
forced the change, and whether it widens or narrows the block's claims. Then
re-sign. -->

*None.*

<!--
| # | Date | Decision amended | Change | Why / what forced it | Claim impact |
|---|---|---|---|---|---|
| A1 | <date> | D2 | <what> | <evidence> | <widens/narrows/neutral> |
-->
