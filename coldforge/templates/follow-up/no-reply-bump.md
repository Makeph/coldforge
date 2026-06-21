---
id: followup_bump
name: Follow-up — No-Reply Bump
category: follow-up
persona: Anyone following up once on a first touch that got no reply
use_case: A single, soft bump on a non-replier. Sent as a reply in the same thread 3–4 days later. Gives them an easy out instead of pressure.
deliverability_notes: |
  Send as a reply to the original (same subject, keeps the thread) — a new thread
  resets context and reads as a fresh blast. One bump only; a third touch on silence
  hurts your domain reputation more than it helps. Offer the "no" explicitly.
subject: "Re: {{original_subject}}"
variables: [first_name, original_hook, sender_name, original_subject]
---

Hi {{first_name}},

Floating this back up in case it slipped — totally understand if it's not the right time.

The short version again: {{original_hook}}.

If it's a no, just say the word and I'll close the loop and stop emailing. If it's a "later", tell me roughly when and I'll check back then instead.

— {{sender_name}}
