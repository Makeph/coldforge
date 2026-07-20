---
id: fr_agence_relance
name: FR — Relance douce (sans réponse)
category: sales-fr
persona: Le même expéditeur, 3 à 4 jours après un premier message resté sans réponse
use_case: >
  Une seule relance, envoyée en réponse dans le même fil. Reformule la promesse
  en une ligne et offre explicitement la sortie — c'est ce qui déclenche les
  « non merci » propres… et une bonne part des « ah oui, pardon, ça m'intéresse ».
deliverability_notes: |
  Répondre au premier message (même objet, préfixe « Re: ») pour garder le fil.
  Une seule relance sur silence : au-delà, on abîme la réputation du domaine et
  la marque. Toujours proposer le « non » en toutes lettres.
subject: "Re: {{original_subject}}"
variables: [first_name, original_hook, sender_name, original_subject]
---

Bonjour {{first_name}},

Je me permets de faire remonter mon message — je sais ce que c'est, une boîte
de réception d'agence.

En une ligne : {{original_hook}}.

Si c'est non, dites-le simplement et je clos le sujet. Si c'est « plus tard »,
dites-moi quand et je reviendrai à ce moment-là.

— {{sender_name}}
