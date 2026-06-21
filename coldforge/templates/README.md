# coldforge template pack

A small, opinionated set of cold-email templates for the situations that
actually earn replies. Every template is **plaintext, under ~120 words, one
CTA**, with front-matter documenting *who* sends it, *when* it wins, and the
deliverability traps to avoid.

These are content files. The engine loads them at runtime — list them with
`coldforge templates`, inspect one with `coldforge templates show <id>`.

## Format

```yaml
---
id: sales_pain_point
name: Sales — Pain-Point Opener
category: sales
persona: Who you are when you send this
use_case: When this template wins
deliverability_notes: |
  What to keep, what to swap, what trips spam filters.
subject: Noticed {{signal}} at {{company}}
variables: [first_name, company, signal]
---

Plaintext body with {{variables}} …
```

## Add your own

Drop any `.md` file (same format) into `$COLDFORGE_HOME/templates/`
(default `~/.coldforge/templates/`) and it appears alongside the built-ins —
no reinstall needed.

## Pack index

| id | category | one-liner |
|----|----------|-----------|
| `sales_pain_point` | sales | open on a concrete symptom of the problem you solve |
| `sales_peer_result` | sales | lead with a comparable customer's outcome, not a pitch |
| `sales_founder_direct` | sales | early-stage founder-to-buyer honesty trade |
| `recruit_passive` | recruiting | first touch to a happy, employed candidate |
| `recruit_founder_hire` | recruiting | founder reaching out for an early engineer/PM |
| `partner_integration` | partnership | scoped product-integration proposal |
| `intro_mutual` | warm-intro | a shared contact agreed to be named |
| `network_curiosity` | networking | no-ask first touch to someone you admire |
| `followup_bump` | follow-up | single soft bump on a non-replier |
