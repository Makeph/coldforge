---
id: fr_agence_derniere_porte
name: FR — Dernier message (breakup)
category: sales-fr
persona: Le même expéditeur, une semaine après la relance restée sans réponse
use_case: >
  Le message de clôture : on annonce qu'on arrête d'écrire, on laisse une trace
  utile (le résultat en une phrase) et une porte ouverte sans pression. Taux de
  réponse étonnamment élevé parce qu'il ne demande plus rien.
deliverability_notes: |
  Toujours dans le même fil. C'est le DERNIER message : après lui, silence
  définitif — tenir cette promesse est ce qui rend le message crédible. Aucun
  lien, aucune pièce jointe, ton factuel.
subject: "Re: {{original_subject}}"
variables: [first_name, outcome, sender_name, original_subject]
---

Bonjour {{first_name}},

Pas de réponse — j'en conclus que ce n'est pas un sujet pour vous en ce moment,
et j'arrête donc de vous écrire.

Je laisse juste ceci au cas où le contexte change : {{outcome}}.

Si un jour ça redevient d'actualité, ce fil suffit — répondez ici et je
reprendrai le dossier. Bonne continuation.

— {{sender_name}}
