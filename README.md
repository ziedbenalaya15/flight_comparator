# Flight Error Fare Watcher

Surveillance de prix de vols orientée **détection d'erreurs de prix (error fares)**, avec architecture hybride :

- **Niveau 1 — baseline** : Travelpayouts/Aviasales Data API (cache ~48h), collecte toutes les 30 min pour construire les statistiques par route.
- **Niveau 2 — confirmation live** : Duffel API, interrogée quand une anomalie candidate est détectée ou à la demande sur une offre du dashboard.
- **Alertes** : Resend via HTTPS (recommandé sur Railway), avec Gmail SMTP en secours local.

## État d'avancement

- ✅ **Phase 1** : squelette FastAPI + PostgreSQL + collecte Travelpayouts + stockage snapshots + dashboard minimal + bouton « Vérifier maintenant »
- ✅ **Phase 2** : baselines (médiane 30j, p10, p25, stddev) + détection étage 1 (seuil, record, chute) + alertes email Resend/Gmail avec dédup Redis 72h et cooldown 2h + template HTML/texte
- ✅ **Phase 3** : page Config complète (8 critères, zone de départ par pays, budgets par cabine) + API REST (`/api/config`, pause/reprise)
- ✅ **Phase 4** : module Duffel isolé — confirmation live des anomalies (🔥 `CONFIRME_LIVE`), bouton « Prix cabines avant » (`/api/premium`), circuit breaker
- ✅ **Phase 5** : critère cross-devise (taux ECB frankfurter.app), digest quotidien 8h, métriques Prometheus `/metrics`, export CSV, tests pytest

## Setup local

```bash
cp .env.example .env
# Renseigner au minimum TRAVELPAYOUTS_TOKEN et ADMIN_TOKEN
docker compose up --build
```

Puis ouvrir <http://localhost:8000/> et entrer le `ADMIN_TOKEN`.
Les migrations Alembic sont appliquées automatiquement au démarrage (`start.sh`).

- Dashboard : `GET /` — Config : `GET /config` (token en query param `?token=...`, en header `X-Admin-Token` ou cookie)
- Healthcheck : `GET /health` — Métriques Prometheus : `GET /metrics`
- Collecte immédiate : `POST /api/check` — état : `GET /api/status`
- Config : `GET|PUT /api/config`, `POST /api/config/pause|resume`
- Cabines avant (Duffel) : `POST /api/premium`
- Vérification live d'une offre éco : `POST /api/offers/{snapshot_id}/verify`
- État / test des notifications : `GET /api/notifications/status`, `POST /api/notifications/test`
- Export CSV : `GET /api/export?dataset=snapshots|alerts&days=30`
- Digest manuel : `POST /api/digest/send`

### Tests

```bash
docker compose up -d
docker compose exec app pytest
```

Les tests unitaires (schémas, rendu email, conversion FX) sont autonomes ; les tests d'intégration (détection, dédup, API config) utilisent le Postgres et le Redis du compose.

### Sans Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Postgres et Redis accessibles via DATABASE_URL / REDIS_URL dans .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Déploiement Railway

1. Créer un projet Railway avec les services **PostgreSQL** et **Redis**.
2. Déployer ce repo (le `Dockerfile` est détecté automatiquement ; `start.sh` attend PostgreSQL, applique les migrations puis lance uvicorn sur `$PORT`).
3. Dans l'onglet **Variables du service applicatif** (et non dans le service PostgreSQL), ajouter les références :
   - `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   - `REDIS_URL=${{Redis.REDIS_URL}}`
   Les noms `Postgres` et `Redis` doivent correspondre aux noms réels des services Railway. Les URL `postgres://...` et `postgresql://...` de Railway sont automatiquement adaptées pour `asyncpg`.
4. Ajouter dans ce même onglet `TRAVELPAYOUTS_TOKEN`, `ADMIN_TOKEN`, et si utilisés `DUFFEL_TOKEN`, `ALERT_EMAIL_TO`.
5. Pour les notifications Railway, ajouter `RESEND_API_KEY` et éventuellement `RESEND_FROM_EMAIL`. Sans domaine vérifié, laisser `Flight Alerts <onboarding@resend.dev>` et utiliser comme destinataire l'adresse du compte Resend. Gmail SMTP reste disponible en secours local via `GMAIL_SMTP_USER` et `GMAIL_APP_PASSWORD`, mais les plans Railway Trial/Hobby/Free bloquent SMTP.
6. Déployer les changements de variables, puis définir le healthcheck Railway sur `/health` et générer un domaine public dans **Settings → Networking**.

## Notifications email

Resend est prioritaire dès que `RESEND_API_KEY` est défini. `RESEND_FROM_EMAIL` vaut par défaut `Flight Alerts <onboarding@resend.dev>` ; cette adresse de test est limitée au destinataire du compte Resend. Pour envoyer vers d'autres adresses, vérifier un domaine dans Resend puis utiliser une adresse de ce domaine.

### Secours Gmail SMTP (local ou plan autorisant SMTP)

1. Compte Google → **Sécurité** → activer la **Validation en 2 étapes**.
2. Toujours dans Sécurité → **Mots de passe d'application** → créer un mot de passe pour « Mail ».
3. Mettre ce mot de passe (16 caractères) dans `GMAIL_APP_PASSWORD` — **jamais** le mot de passe du compte.

## Configuration de la surveillance

La config active vit dans la table `config` (JSON versionné, une ligne active). Une config par défaut est créée au premier démarrage (PAR → ICN/BKK/JFK, EUR, départ le mois prochain). Elle se modifie depuis `/config`. Les pays `LB`, `EG`, `TN`, `IR`, `BR`, `MX` et `DO` sont notamment développés automatiquement vers leurs principaux aéroports.

Champs de détection dans la config JSON : `threshold_pct` (défaut 40 — % sous la médiane 30j), `send_cache_only_alerts` (envoyer les alertes 📉 non confirmées live), `alert_email_to` (défaut : variable `ALERT_EMAIL_TO`).

## Détection & anti-spam

À chaque collecte, le meilleur prix de chaque route+devise est comparé à la baseline (calculée **hors** collecte courante) :

- **seuil** : prix sous `threshold_pct` % de la médiane glissante 30 jours
- **record** : nouveau minimum absolu sur la route+devise
- **chute** : baisse > 50 % vs le relevé précédent de moins de 6 h

Garde-fous : pas d'alerte seuil/record avant `MIN_BASELINE_SAMPLES` relevés (30) et 24 h d'historique. Anti-spam : dédup Redis 72 h sur (route + prix arrondi + type), cooldown 2 h par route contourné si le nouveau prix est plus bas, fail-open si Redis est indisponible. Si l'email échoue ou est désactivé, la réservation anti-spam est libérée afin que la collecte suivante puisse retenter. Toutes les alertes sont journalisées dans la table `alerts` (statut email visible sur le dashboard).

Les prix Travelpayouts sont des prix indicatifs issus du cache Aviasales (recherches vues au cours des 48 dernières heures) et peuvent avoir changé au clic. Le bouton « Vérifier live » interroge Duffel sur la route et les dates exactes et affiche aussi si le token Duffel est en mode réel ou en mode test.
