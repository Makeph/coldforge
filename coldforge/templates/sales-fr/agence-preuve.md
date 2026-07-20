---
id: fr_agence_preuve
name: FR — Agences, ouverture sur la valeur invisible
category: sales-fr
persona: Fondateur SaaS ou indépendant qui vend un outil/service aux agences (SEO, Ads, growth, web)
use_case: >
  Premier contact en français avec un patron d'agence ou un freelance. Ouvre sur
  le symptôme que toute agence connaît — le client ne voit pas le travail — puis
  relie ce symptôme au résultat concret que vous vendez. Une seule question en CTA.
deliverability_notes: |
  En France le B2B à froid est légal si l'objet du message est en rapport avec la
  fonction du destinataire et qu'un moyen d'opposition simple existe (CNIL). Donc :
  adresse pro nominative, proposition pertinente pour son métier, et la porte de
  sortie explicite en fin de message. Pas de lien dans le premier envoi, pas de
  pièce jointe, moins de 120 mots.
subject: "{{company}} — {{pain}} ?"
variables: [first_name, company, observation, pain, outcome, sender_name]
---

Bonjour {{first_name}},

{{observation}} — et dans la plupart des agences que je croise, ça veut dire que {{pain}}.

C'est exactement ce qu'on enlève : {{outcome}}. Sans changer vos outils, sans projet de migration.

Est-ce que c'est un sujet chez {{company}} en ce moment ? Si ce n'est pas le bon
moment, un simple « non merci » me suffit et je ne reviendrai pas.

— {{sender_name}}
