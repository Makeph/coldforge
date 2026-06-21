---
id: recruit_passive
name: Recruiting — Passive Candidate
category: recruiting
persona: Hiring manager or in-house recruiter contacting a happy, employed person
use_case: First touch to someone who isn't looking. Designed to respect their time and earn a reply even from a "not actively searching" state.
deliverability_notes: |
  Lead with one specific thing from their work — generic "I came across your
  profile" reads as bulk mail. Disclose the comp range up front; omitting it is the
  #1 reason passive candidates ignore recruiter mail. Under 120 words.
subject: "{{role_title}} at {{company}} — worth 20 minutes?"
variables: [first_name, candidate_signal, role_title, company, team_focus, comp_range, sender_name]
---

Hi {{first_name}},

{{candidate_signal}} is why I'm writing — that's the exact shape of work we're hiring for, so this isn't a mass mail.

We're looking for a {{role_title}} at {{company}}. Small team, ships weekly, focused on {{team_focus}}. Comp range {{comp_range}} plus equity.

Even if you're happy where you are, I'd value 20 minutes to swap notes — worst case you walk away with context for whenever the timing's right.

Reply with a day that works and I'll send a couple of times.

— {{sender_name}}
