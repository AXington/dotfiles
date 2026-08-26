---
name: team-directory
description: >
  Use this skill when Ali needs to find a point of contact, team owner, or who
  to talk to about a specific system, service, alert, or domain. Covers SRE,
  DevEx, Integration, Data Science/Engineering, product engineering teams
  (CAT, UM, Introspect, Retrospect, Code Audit, Interact), Security, and IT.
  Sourced from GitLab groups, PagerDuty escalation policies, and on-call data.
---

# Team Directory

> Last updated: 2026-04-24
> Sources: GitLab (gl.iodinesoftware.com), PagerDuty escalation policies and on-call
> Jira project leads not included (tachi jira has no `projects list` command)

---

## SRE

PD policy: **SRE** (PZ86TH1) -- escalates SRE Primary -> SRE Secondary -> Chase Hoffman
Kafka incidents also page SRE (Kafka Team policy PLQ0GVS uses SRE schedules).

| Username | Name | Role |
|---|---|---|
| cheng | Cheng Zhou | GL Owner; heads SRE and Infrastructure groups; IT on-call |
| choffman | Chase Hoffman | GL Maintainer; SRE Primary/Secondary on-call; escalation target |
| cadams | Clinton Adams | GL Maintainer; on-call |
| dsolanke | Dam Solanke | GL Maintainer |
| jhearn | Joshua Hearn | GL Maintainer |
| mwhite | Mason White | PD on-call (Page Mason and Thomas-ep) |
| thomas | Thomas Valdez | DevEx Owner; Incident Management on-call; Page Thomas-ep |
| bohearn | Brint O'Hearn | Contractor Developer |
| cbeason | Christopher Beason | Contractor Developer |
| jprough | Jonathan Prough | Contractor Developer |
| rsepulvedaayala | Rodrigo Sepulveda Ayala | Contractor Developer |
| wwilliams | Will Williams | Contractor Developer |

**Kafka ownership note:** SRE owns Kafka operationally. Stakeholders for retention/config
changes: paul (Paul Sjoberg), cheng (Cheng Zhou), drinkevich (Debora Rinkevich).

---

## DevEx (Developer Experience)

PD policies: **DEVEX - DevTools** (PWTK7QP), **DevEx - Observability** (PQPQBHH)
On-call: Drew Richardson (Primary), Brendan Laws (Secondary)

| Username | Name | Role |
|---|---|---|
| blaws | Brendan Laws | GL Owner; DevEx Secondary on-call; owns UM group too |
| drichardson | Drew Richardson | GL Owner; DevEx Primary on-call; owns Introspect, Platform groups |
| paul | Paul Sjoberg | GL Owner; owns Interact, Data Platform groups |
| alangman | Q (Quincy) Langman | GL Owner; Introspect escalation target; Page Q Langman-ep |
| thomas | Thomas Valdez | GL Owner; Incident Management on-call |

**Consul:** DevEx owns (Consul - DEVTOOLS-ep, PJKF8R4; Debbie Richardson is primary).

---

## Integration

PD policy: **Integration** (PQ2MU4L)
On-call: Prasnav Naik (Primary), Daryl Ferreras (Secondary)
Escalation targets: Pras Naik, Sonny Huynh, Daryl Ferreras

| Username | Name | Role |
|---|---|---|
| gjiang | Gordon Jiang | GL Owner; team lead |
| dferreras | Daryl Ferreras | GL Maintainer; Secondary on-call |
| pnaik | Prasnav Naik | GL Maintainer; Primary on-call |
| shuynh | Sonny Huynh | PD escalation target (sonny@...) |

GitLab subgroups: `integration/interfaces`, `integration/tooling`

---

## Data Science

GL group: `iodine/data-science` (364)
PD policy: **Data Science Apps** (PNNMWYY) -- tjain/jwestfall primary, Victor Chau/ilaw secondary

| Username | Name | Role |
|---|---|---|
| ndavis | Nicholas Davis | GL Owner |
| yhendrix | Yulia Hendrix | GL Owner |
| swang | San Wang | GL Maintainer |
| sgraeber | Sawyer Graeber | GL Maintainer |
| aluke | Adam Luke | Developer |
| anooli | Ajay Nooli | Developer |
| araykhel | Alexis Raykhel | Developer |
| aahmed | Amna Ahmed | Developer |
| greg | Greg Hennigan | Developer |
| hshah | Hinal Shah | Developer |
| kprakash | Komal Prakash | Developer |
| ssharma | Sachin Sharma | Developer |
| wkulp | William Kulp | Developer |
| jwestfall | Jake Westfall | Developer; Data Eng on-call |
| glubian | George Lubian | Developer |

---

## Data Engineering

PD policy: **Data Engineering** (PVRHAYA)
On-call: Tabassum Jain (Primary), Jake Westfall (Secondary); escalation: Jon Matthews

| Username | Name | Role |
|---|---|---|
| tjain | Tabassum Jain | ML-Platform Maintainer; Data Eng Primary on-call |
| jwestfall | Jake Westfall | Data Science Developer; Data Eng Secondary on-call |
| jon | Jon Matthews | PD escalation target; NLP Secondary on-call (jon@...) |

---

## NLP

PD policy: **NLP** (P6A3MTV) -- uses Data Engineering schedules + NLP Primary/Secondary

| Username | Name | Role |
|---|---|---|
| ilaw | Isaac Law | ML-Platform Maintainer; NLP Primary on-call; Data Science Apps Secondary |
| jon | Jon Matthews | NLP Secondary on-call |

---

## ML-Platform

GL group: `iodine/product-development/ML-platform` (152)

| Username | Name | Role |
|---|---|---|
| kyao | Kelly Yao | GL Owner; also owns Introspect group |
| ilaw | Isaac Law | Maintainer |
| jwestfall | Jake Westfall | Maintainer |
| jrule | Jordan Rule | Maintainer; Page Jordan Rule-ep |
| rpowell | Rick Powell | Maintainer; Page Rick Powell-ep |
| tjain | Tabassum Jain | Maintainer |
| vkuz | Vlad Kuz | Maintainer |
| wbaskin | Wiley Baskin | Maintainer; Page Wiley-ep |

---

## Concurrent (CAT -- Core Awesome Team)

PD policy: **Core Awesome Team** (P08GB23)
On-call: Jennifer Rogers (Primary), Neal Siebert (Secondary)
Escalation: Jake Lieman, Bryan Horne

| Username | Name | Role |
|---|---|---|
| bryan | Bryan Horne | GL Owner; UM and CAT lead; Generative Text on-call |
| jlieman | Jacob Lieman | GL Owner; Code Audit/Interact/CAT escalation target; Page Jake Lieman-ep |
| mallory | Mallory Payne | GL Owner; Page Mallory-ep |
| nsiebert | Neal Siebert | GL Owner; CAT Secondary on-call |
| jrogers | Jennifer Rogers | GL Maintainer; CAT Primary on-call |
| dkrishna | Deepa Krishna | Maintainer |

---

## Utilization Management

PD policy: **Utilization Management** (P5URHEO)
On-call: UM Primary/Secondary schedules; escalation: Nick Jones, Bryan Horne, Deekshita Reddy, Daniel McCloskey

| Username | Name | Role |
|---|---|---|
| blaws | Brendan Laws | GL Owner |
| bryan | Bryan Horne | GL Owner; escalation target |
| njones | Nick Jones | GL Owner; escalation target; Page Nick Jones (njones@...) |
| dmccloskey | Daniel McCloskey | Developer; escalation target |
| dreddy | Deekshita Reddy | Developer; UM Secondary on-call; escalation target |
| nzhang | Ning Zhang | Developer; Page Ning Zhang-ep |
| tarbouzova | Tatyana Arbouzova | Developer |
| tgardiner | Timothy Gardiner | Developer |
| dledoux | Dillon LeDoux | Developer |
| ecoombes | Edward Coombes | Developer |
| jgiroux | Jon Giroux | Developer |
| mfan | Michael Fan | Developer (also Retrospect owner) |

---

## Introspect (SSOT)

PD policy: **Introspect** (PO3DD66), **Introspect - QA** (PG6SBJ6)
On-call: Introspect Primary schedule; escalation: Debora Rinkevich, Q Langman

| Username | Name | Role |
|---|---|---|
| drinkevich | Debora Rinkevich | GL Owner; escalation target; Page Debbie R-ep; Kafka stakeholder |
| drichardson | Drew Richardson | GL Owner (DevEx crossover) |
| kyao | Kelly Yao | GL Owner |
| amomeni | Ali Momeni | Maintainer; Introspect Primary on-call; Page Ali (amomeni@...) |
| scallahan | Sean Callahan | Developer |

---

## Retrospect

PD policy: **Retrospect - Prod+UAT** (PFVEAZK), **Retrospect - QA** (P2L44SW)
Escalation: Michael Fan, Daniel Santoro

| Username | Name | Role |
|---|---|---|
| dsantoro | Daniel Santoro | GL Owner; escalation target |
| mfan | Michael Fan | GL Owner; escalation target; Page Michael Fan-ep |
| alangman | Q Langman | GL Owner (DevEx crossover) |
| mvaithianathan | Muthukumar Vaithianathan | GL Owner (Contractor) |
| vveeramani | Vijay Veeramani | GL Owner (Contractor) |

---

## Code Audit (Prebill)

PD policy: **Code Audit Team** (PQZVU20)
On-call: Prebill Primary (Joey Chapline), Prebill Secondary (Jake Lieman escalation)

| Username | Name | Role |
|---|---|---|
| jlieman | Jacob Lieman | GL Owner; ultimate escalation target |
| jballing | Jamie Balling | GL Owner; also owns Data Platform |
| jchapline | Joey Chapline | GL Owner; Prebill Primary on-call |
| jgiroux | Jon Giroux | GL Owner |
| crafuse | Christopher Rafuse | Developer |

---

## Interact

PD policy: **Interact Escalation Policy** (PBSCI0Y)
On-call: Interact Primary/Secondary schedules; escalation: Jake Lieman

| Username | Name | Role |
|---|---|---|
| jlieman | Jacob Lieman | GL Owner; escalation target; Page Jake Lieman-ep |
| paul | Paul Sjoberg | GL Owner (DevEx crossover) |
| jcarmichael | James Carmichael | Developer |

---

## Security

PD policy: **Security** (P1NGOZT)
On-call: James Kleckner (Primary), Bill Hyden (Secondary)

| Username | Name | Role |
|---|---|---|
| bhyden | Bill Hyden | Developer; Security Secondary on-call |
| jkleckner | James Kleckner | Contractor Developer; Security Primary on-call |
| gbadgi | Girish Badgi | Developer |
| cheng | Cheng Zhou | DevOps/infra security crossover |

---

## Insights

GL group: `iodine/insights` (268)

| Username | Name | Role |
|---|---|---|
| josh | Joshua Toub | GL Owner |
| isaac | Isaac Neely | GL Maintainer |
| mseverson | Matt Severson | GL Maintainer |
| gsaucier | Genevieve Saucier | Developer |
| mbesmer | Morgan Besmer | Developer |

---

## Infrastructure / IT

GL group: `iodine/infrastructure` (689) -- subgroups: `3rd-party`, `terraform`

| Username | Name | Role |
|---|---|---|
| cheng | Cheng Zhou | GL Owner; sole Infrastructure group owner; IT on-call (Always Cheng) |

---

## Incident Management

PD policy: **Incident Management** (PE8EVP6)
On-call: Incident Management Primary (spratt), Secondary (jwarren)

| Username | Name | Role |
|---|---|---|
| thomas | Thomas Valdez | Escalation target (level 3) |
| jwarren | (jwarren@...) | Secondary on-call |
| spratt | (spratt@...) | Primary on-call |

---

## System / Service Ownership

| System / Service | Owning Team | Primary Contact(s) |
|---|---|---|
| AWX | SRE | choffman, cadams |
| Kafka | SRE (ops); stakeholders: paul, cheng, drinkevich | choffman (on-call) |
| ArgoCD | SRE/DevEx | choffman, drichardson |
| Consul | DevEx | drichardson (primary on-call) |
| Alertmanager / Observability | DevEx | drichardson, blaws |
| Airflow | Data Engineering | tjain, jwestfall |
| ClearML | ML-Platform / Data Science | kyao, ilaw |
| Introspect (SSOT) | Introspect team | drinkevich, kyao, amomeni |
| Retrospect | Retrospect team | mfan, dsantoro |
| Utilization Management app | UM team | njones, bryan, dmccloskey |
| CAT services | CAT / Concurrent | jrogers, nsiebert, jlieman |
| Code Audit / Prebill | Code Audit | jchapline, jlieman |
| Interact | Interact team | jlieman, paul |
| NLP services | NLP / Data Eng | ilaw, jon, tjain |
| Security scanning | Security | jkleckner, bhyden |
| Terraform / cloud infra | Infrastructure | cheng |
| GitLab / dev tooling | DevEx | drichardson, blaws |
| PagerDuty / tachi | SRE/DevEx | cadams, drichardson |
| Integration pipelines | Integration | gjiang, pnaik, dferreras |

---

## Quick Reference: PD Escalation Policy IDs

| Policy | ID |
|---|---|
| SRE | PZ86TH1 |
| Kafka Team | PLQ0GVS |
| Integration | PQ2MU4L |
| DEVEX - DevTools | PWTK7QP |
| DevEx - Observability | PQPQBHH |
| Data Engineering | PVRHAYA |
| Data Science Apps | PNNMWYY |
| NLP | P6A3MTV |
| Introspect | PO3DD66 |
| Retrospect - Prod+UAT | PFVEAZK |
| Core Awesome Team | P08GB23 |
| Utilization Management | P5URHEO |
| Code Audit Team | PQZVU20 |
| Interact | PBSCI0Y |
| Security | P1NGOZT |
| Incident Management | PE8EVP6 |
| IT | PZ6LX18 |
