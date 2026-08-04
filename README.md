# Flight Error Fare Watcher

Surveillance de prix de vols orientée **détection d'erreurs de prix (error fares)**, avec architecture hybride :

- **Niveau 1 — baseline** : Travelpayouts/Aviasales Data API (cache ~48h), collecte toutes les 30 min pour construire les statistiques par route.
- **Niveau 2 — confirmation live** : Duffel API, interrogée uniquement quand une anomalie candidate est détectée (Phase 4).
- **Alertes** : email Gmail uniquement (Phase 2).

## État d'avancement

- ✅ **Phase 1** : squelette FastAPI + PostgreSQL + collecte Travelpayouts + stockage snapshots + dashboard minimal + bouton « Vérifier maintenant »
- ✅ **Phase 2** : baselines (médiane 30j, p10, p25, stddev) + détection étage 1 (seuil, record, chute) + alertes email Gmail avec dédup Redis 72h et cooldown 2h + template HTML/texte
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

1. Créer un projet Railway avec les plugins **PostgreSQL** et **Redis**.
2. Déployer ce repo (le `Dockerfile` est détecté automatiquement ; `start.sh` applique les migrations puis lance uvicorn sur `$PORT`).
3. Variables d'environnement à définir : `DATABASE_URL` (au format `postgresql+asyncpg://...`), `REDIS_URL`, `TRAVELPAYOUTS_TOKEN`, `ADMIN_TOKEN`, et pour les phases suivantes `DUFFEL_TOKEN`, `GMAIL_SMTP_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL_TO`.
4. Healthcheck Railway : `/health`.

## Mot de passe d'application Gmail (pour la Phase 2)

1. Compte Google → **Sécurité** → activer la **Validation en 2 étapes**.
2. Toujours dans Sécurité → **Mots de passe d'application** → créer un mot de passe pour « Mail ».
3. Mettre ce mot de passe (16 caractères) dans `GMAIL_APP_PASSWORD` — **jamais** le mot de passe du compte.

## Configuration de la surveillance

La config active vit dans la table `config` (JSON versionné, une ligne active). Une config par défaut est créée au premier démarrage (PAR → ICN/BKK/JFK, EUR, départ le mois prochain). La page d'admin complète arrive en Phase 3 ; en attendant, on peut modifier le JSON directement en base.

Champs de détection dans la config JSON : `threshold_pct` (défaut 40 — % sous la médiane 30j), `send_cache_only_alerts` (envoyer les alertes 📉 non confirmées live), `alert_email_to` (défaut : variable `ALERT_EMAIL_TO`).

## Détection & anti-spam

À chaque collecte, le meilleur prix de chaque route+devise est comparé à la baseline (calculée **hors** collecte courante) :

- **seuil** : prix sous `threshold_pct` % de la médiane glissante 30 jours
- **record** : nouveau minimum absolu sur la route+devise
- **chute** : baisse > 50 % vs le relevé précédent de moins de 6 h

Garde-fous : pas d'alerte seuil/record avant `MIN_BASELINE_SAMPLES` relevés (30) et 24 h d'historique. Anti-spam : dédup Redis 72 h sur (route + prix arrondi + type), cooldown 2 h par route contourné si le nouveau prix est plus bas, fail-open si Redis est indisponible. Toutes les alertes sont journalisées dans la table `alerts` (statut email visible sur le dashboard), y compris quand l'email est désactivé ou échoue.
