# Where to find what

Navigační mapa projektu: co se rozhodlo, proč, kde je to popsané a kde to žije v kódu a testech.
Cesty jsou relativní k repu. Nic zde není nové tvrzení, vše odkazuje na existující dokumenty a kód.

## 1. Kde začít (podle toho, co hledáš)

| Chci vědět… | Otevři |
|---|---|
| co systém je, kdo je za ním, jak ho spustit, CP1 | `README.md` |
| doménu, Project Frame, future pressure (Q), C01 review smyčku | `docs/intent-and-change.md` |
| přesné chování operací, pravidla BR-01..BR-04 | `docs/operations-specification.md` |
| use-case, state a activity diagramy | `docs/diagrams.md` |
| proč se v0.2 změnilo (schvalování) | `docs/c02-change-impact.md` |
| všechna architektonická rozhodnutí (ADR-001..008) + AS-IS trace | `docs/architecture-and-decisions.md` |
| drivery, alternativy, pohledy, TO-BE, delta | `docs/c03-architecture.md` |
| důkazy (spike, C02 běh, C03 ověření, commity/tagy) | `docs/evidence-and-evolution.md` |

## 2. Rozhodnutí (ADR) → kde jsou v kódu

Text všech ADR: `docs/architecture-and-decisions.md` (hledej `## ADR-00x`).

| ADR | Rozhodnutí | Kde to žije |
|---|---|---|
| 001 | Rezervuje se **místo (Seat)** na představení, ne sál | `src/cinema/schema.sql` (`seats`, `reservation_seats`) |
| 002 | Python 3.11 + FastAPI + SQLite | `src/cinema/main.py`, `db.py`, `requirements*.txt` |
| 003 | Expirace holdu se vyhodnocuje **líně**, bez background jobu | `reservations.py`: `is_expired`, `effective_state` |
| 004 | Dvojí rezervaci brání **DB constraint**, ne aplikační kód | `schema.sql`: index `uq_confirmed_seat_per_screening`; `reservations.py`: `_guard_br02`; testy `test_double_booking_spike.py` |
| 005 | `reservation_seats` duplikuje `screening_id` a `state` | `schema.sql`; synchronizace v `reservations.py`: `_change_state` |
| 006 | No-orphan pravidlo se hodnotí při create; okraje řady se počítají jako obsazené | `availability.py`: `has_isolated_free_seat`, `orphaned_row`; volá `reservations.create` |
| 007 | Žádný frontend v CP1 (frontend vznikl později, není v baseline) | `src/frontend/` |
| 008 | **Reservation Management** je jediný, kdo mění stav rezervace | `reservations.py`; hlídá `tests/test_architecture.py` |

## 3. Drivery (C03) → kde se řeší

Definice: `docs/c03-architecture.md`, sekce B.

| Driver | O co jde | Řešení v kódu | Test |
|---|---|---|---|
| D1 | souběžné potvrzení nesmí porušit BR-02 | unikátní index + `_guard_br02` překládá chybu na 409 | `test_c02_operations.py::TestConfirm` (souběh), `test_double_booking_spike.py` |
| D2 | jeden vlastník stavu a transakce, obě tabulky ve shodě | `reservations._change_state` (odmítne běžet mimo transakci), `db.write_tx` | `test_architecture.py`, `test_a_state_change_outside_a_transaction_is_refused` |
| D3 | `PENDING_APPROVAL` přežije request | stav v DB; `reservations.decide`, `needs_approval`, `effective_state` | `TestConfirmNeedingApproval`, `TestApprove` |
| D4 | selhání notifikace nesmí změnit výsledek | `notifications.notify` polyká chyby, volá se z API po commitu | `test_notifications.py::test_failed_notification_never_fails_the_reservation` |

Známé mezery (zapsané, neřešené): chybí role/autorizace (`/decision` může volat kdokoli, `403` z OP-05 není), ztracená notifikace při pádu mezi `COMMIT` a `notify`.

## 4. Operace / use-casy → endpoint → kód → testy

Spec: `docs/operations-specification.md` (sekce `## OP-0x`). Diagramy: `docs/diagrams.md`.

| Operace | Endpoint (`main.py`) | Logika | Testy (`tests/test_c02_operations.py`) |
|---|---|---|---|
| OP-01 Create → DRAFT | `POST /reservations` | `reservations.create` + `availability` | `TestCreate`; `test_api.py::test_walking_skeleton` |
| OP-02 Check Availability | `GET /screenings/{id}/availability` | `availability.occupied_seat_ids` | `TestAvailability` |
| OP-03 Confirm → CONFIRMED / PENDING_APPROVAL | `POST /reservations/{id}/confirm` | `reservations.confirm` | `TestConfirm`, `TestConfirmNeedingApproval` |
| OP-04 Cancel | `POST /reservations/{id}/cancel` | `reservations.cancel` | `TestCancel`, `TestCancelPending` |
| OP-05 Approve / Reject (v0.2) | `POST /reservations/{id}/decision` | `reservations.decide` | `TestApprove` |
| (vedlejší) notifikace | `POST /users/{id}/notifications/deliver` | `notifications.deliver` | `test_notifications.py` |

Pomocné endpointy mimo životní cyklus: `/auth/login`, `/movies`, `/screenings`, `/users/{id}/reservations`, `/health`.

## 5. Pravidla

| Pravidlo | Spec | Vynuceno v |
|---|---|---|
| BR-01 intervaly `[start, end)` | `operations-specification.md` `### BR-01` | `availability.py`, porovnání časů v `reservations.py` |
| BR-02 žádné dvě CONFIRMED na stejné místo | `### BR-02` | `schema.sql` index (poslední linie) + `_guard_br02` |
| BR-03 politika rušení | `### BR-03` | `reservations.cancel` |
| BR-04 žádné osamocené místo (no-orphan) | `### BR-04` | `availability.orphaned_row` (volá `create`) |

## 6. Moduly (`src/cinema/`)

| Soubor | Role | Smí |
|---|---|---|
| `main.py` | pouze HTTP: routy, mapování `ReservationError` → status kód | nesmí zapisovat stav rezervace |
| `reservations.py` | **Reservation Management**: create / confirm / cancel / decide, BR-02 guard | jediný zapisuje `reservations` a `reservation_seats` |
| `availability.py` | co je obsazené, no-orphan | jen čte |
| `db.py` | připojení, `write_tx`, `now`, `reset_db` | infrastruktura |
| `notifications.py` | `NotificationService` (Protocol), `BrowserNotifications` (outbox v DB), `notify`, `deliver` | volá se z API po commitu |
| `schema.sql`, `seed.sql`, `seed.py` | schéma a demo data; `seed.py` generuje náhodná promítání a obsazená místa od dneška do konce příštího měsíce (`seed.py` je jediná deklarovaná výjimka z pravidla vlastnictví) | |

## 7. Pohledy C03 (kde je který diagram)

Vše v `docs/c03-architecture.md`: doménový model C1, odpovědnosti C2, otázka D, alternativy E1–E3, rozhodnutí F, kontext G1, statická architektura G2, vlastnictví přechodů G3, runtime G4, sekvence H1, třídy H2, cross-view kontrola I, delta AS-IS → TO-BE J.
AS-IS stav před refaktoringem: `docs/architecture-and-decisions.md`, Část A (A1–A8), reprodukovatelný trace `docs/trace_confirm.py` (běží na dočasné DB, vývojovou nechá být).

## 8. Ověření a evidence

| Co | Kde |
|---|---|
| celý test suite (68 testů) | `python -m pytest -q` |
| architektonické pravidlo (jen `reservations.py` zapisuje stav) | `tests/test_architecture.py` |
| C01 spike (persistence / dvojí prodej) | `evidence-and-evolution.md` (C01), `tests/test_double_booking_spike.py` |
| C02: spec → běžící app, nalezené nesoulady | `evidence-and-evolution.md`, "Evidence C02" |
| C03: ověření, konformita, rizika, commit | `evidence-and-evolution.md`, "C03 — Architecture Evidence" |
| opravy po C03, před C04 (PR #11: trace skript, login na starší SQLite, confirm nezapisuje `EXPIRED`) | `evidence-and-evolution.md`, "Follow-up before C04" |
| Tagy: `c02-v0.2` = `36b7571`, `c03-architecture` = `817fdd2` | `git tag` |
