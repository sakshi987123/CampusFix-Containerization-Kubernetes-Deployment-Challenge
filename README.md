# CampusFix

CampusFix is a Flask and SQLite campus maintenance portal. Students can report an issue, receive a ticket ID, verify and track requests, and view public resolution updates. Authenticated administrators can review reports, assign staff, update status, and keep private notes.

## Features

- Database-driven dashboard statistics, recent requests, and category totals.
- Validated complaint form with a ticket ID and optional JPG, PNG, or WebP image.
- Private student request lookup using a ticket ID plus the reporting email/student ID, or all requests matching that identifier.
- Search and filters for student and administrator complaint lists.
- Admin-only complaint updates, staff assignment, private internal notes, and public resolution comments.
- Password-hashed administrator credentials, CSRF-protected forms, and gated image attachments.
- SQLite schema creation and additive migration of the earlier `campusfix.db` schema.
- `/health` endpoint, Gunicorn entry point, Docker, Compose, and Kubernetes manifests.

## Architecture

The application is a single Flask process serving server-rendered Jinja templates and static assets. `app.py` owns routes and request validation, `database.py` owns SQLite connections and schema initialization, and `config.py` reads environment-based settings. SQLite and image files are stored separately from the application code. The test suite uses a per-test temporary database.

```text
Browser -> Flask / Gunicorn -> SQLite database
                       `----> private image uploads
```

## Technology

- Python 3.12+, Flask, Flask-WTF, SQLite, Pillow, Gunicorn
- HTML, CSS, Bootstrap 5, Bootstrap Icons, and vanilla JavaScript
- Docker, Docker Compose, Kubernetes manifests

Bootstrap, Bootstrap Icons, and Google Fonts are loaded from CDNs, so those visual assets require network access in the browser. Core server-side functions do not depend on those CDNs.

## Prerequisites

- Python 3.12 or newer and Git
- Docker Desktop for container work
- A Kubernetes local cluster such as Minikube or Docker Desktop Kubernetes for the optional cluster phase
- PowerShell commands below are written for Windows; adjust activation and environment-variable syntax for another shell.

## VS Code setup and local run

Open the CampusFix folder in VS Code. In the integrated PowerShell terminal:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that terminal, or invoke `.\.venv\Scripts\python.exe` directly. The project has a local-only development secret fallback. Set a private secret when running outside a demo:

```powershell
$secretBytes = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($secretBytes)
$env:SECRET_KEY = [Convert]::ToBase64String($secretBytes)
$random.Dispose()
$env:CAMPUSFIX_DB = "$PWD\instance\campusfix.db"
$env:UPLOAD_FOLDER = "$PWD\instance\uploads"
```

Create the first administrator. The password is prompted without echo and must be at least 12 characters; it is stored as a password hash.

```powershell
python -m flask --app app create-admin
```

Start the local development server:

```powershell
python -m flask --app app run --debug
```

Open <http://127.0.0.1:5000>. The homepage and `/health` initialize the database automatically. Use `/admin/login` for the admin workspace. Local Flask debug mode is only for development; Docker uses Gunicorn.

The legacy root-level `campusfix.db` remains the default when `CAMPUSFIX_DB` is unset. Its schema is migrated additively; existing complaint rows are not deleted. Set `CAMPUSFIX_DB` to a persistent mounted path in containers.

## Tests

```powershell
python -m pytest -q
```

The tests create temporary databases and uploads. They cover page rendering and counts, input validation, submission and ticket generation, verified tracking, filters, invalid tickets, admin login and authorization, updates and history, note privacy, CSRF, health, and persistence. Only test outcomes from an actual run should be reported; the project report identifies any checks not executed in your environment.

## Docker

Create a local Compose environment file and replace the example secret with a fresh random value. Do not commit `.env`.

```powershell
Copy-Item .env.example .env
$secretBytes = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($secretBytes)
$secret = [Convert]::ToBase64String($secretBytes)
$random.Dispose()
(Get-Content .env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$secret" | Set-Content .env
```

Build and start the app with persistent named storage:

```powershell
docker build -t campusfix:1.0 .
docker run -d --name campusfix -p 5000:5000 --env-file .env -e APP_ENV=production -e CAMPUSFIX_DB=/data/campusfix.db -e UPLOAD_FOLDER=/data/uploads -e SESSION_COOKIE_SECURE=false -v campusfix-data:/data campusfix:1.0
```

Or run the Compose service:

```powershell
docker compose up --build -d
```

Create an administrator in the running container, then visit <http://localhost:5000>:

```powershell
docker exec -it campusfix flask --app app create-admin
```

For Compose, replace the `docker exec` command with `docker compose exec campusfix flask --app app create-admin`.

Useful lifecycle and diagnostic commands:

```powershell
docker logs -f campusfix
docker stop campusfix
docker rm campusfix
docker compose logs -f campusfix
docker compose down
docker compose down -v   # Also deletes the persistent database and uploaded files.
```

The Docker image runs Gunicorn as UID 10001, exposes port 5000, and stores the database and uploads under `/data`. The volume must remain mounted to preserve submitted data. `SESSION_COOKIE_SECURE=false` is needed for the plain-HTTP local demo; set it to `true` behind HTTPS.

## Kubernetes local deployment

Build the image using the required tag:

```powershell
docker build -t campusfix:1.0 .
```

For Minikube, load the image into its node runtime. With Docker Desktop Kubernetes, use the local Docker image directly and ensure the current Kubernetes context is Docker Desktop.

```powershell
minikube image load campusfix:1.0
kubectl config current-context
```

Create a secret from a new random value; do not apply `k8s/secret.example.yaml` as-is:

```powershell
$secretBytes = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($secretBytes)
$secret = [Convert]::ToBase64String($secretBytes)
$random.Dispose()
kubectl create secret generic campusfix-secrets --from-literal=SECRET_KEY=$secret
```

Apply storage, app, and service resources:

```powershell
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl rollout status deployment/campusfix
kubectl get pods,svc,pvc
```

Forward the service to localhost and open <http://localhost:5000>:

```powershell
kubectl port-forward service/campusfix-service 5000:80
```

In another terminal, create the first administrator in the single app pod:

```powershell
$pod = kubectl get pods -l app=campusfix -o jsonpath='{.items[0].metadata.name}'
kubectl exec -it $pod -- flask --app app create-admin
```

Inspect logs and clean up:

```powershell
kubectl logs deployment/campusfix
kubectl delete -f k8s/service.yaml
kubectl delete -f k8s/deployment.yaml
kubectl delete -f k8s/pvc.yaml
kubectl delete secret campusfix-secrets
```

PVC deletion behavior depends on the cluster's storage reclaim policy. Back up data before deleting persistent storage.

### SQLite scaling limitation

The Kubernetes Deployment intentionally uses **one replica** and a `ReadWriteOnce` PVC. SQLite is a local-file database; two independent replicas cannot safely write separate pod-local files, and a shared network filesystem is not assumed to provide reliable SQLite locking. The rolling update strategy avoids overlapping pods, but may cause brief downtime during updates. For multi-replica production, move to a shared database such as PostgreSQL, configure backups and migrations, and then increase the replica count. Do not scale this SQLite deployment to two replicas.

## GitHub upload

Review the staged files and confirm `.env`, databases, uploads, virtual environments, and secrets are excluded before publishing:

```powershell
git init
git status --short
git add .
git status --short
git commit -m "Build CampusFix maintenance portal"
git branch -M main
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin main
```

Create the empty GitHub repository first. Replace the example remote with your repository URL. Never commit a real `.env`, administrator password, Kubernetes Secret value, or database containing student records.

## Screenshots to capture

Capture your own screenshots after starting the app; no screenshots are included or represented as already captured.

- `screenshots/dashboard.png`: dashboard with real sample counts.
- `screenshots/new-complaint.png`: validated complaint submission form.
- `screenshots/tracking.png`: verified request and its status history.
- `screenshots/admin-workspace.png`: signed-in administrator complaint queue.
- `screenshots/docker-running.png`: container or Compose status and browser at localhost.
- `screenshots/kubernetes-running.png`: pods/service and browser after port-forward.

Use fictitious student information in demo screenshots.

## Troubleshooting

- `ModuleNotFoundError`: activate `.venv` and run `pip install -r requirements.txt`.
- `SECRET_KEY` production error: set a random value of at least 32 characters in `.env` or the Kubernetes Secret.
- Port 5000 already in use: stop the other process or map another host port, such as `-p 5001:5000`.
- Container database resets: verify the named volume or PVC is mounted at `/data` and the environment points `CAMPUSFIX_DB` there.
- Kubernetes image pull failure: load `campusfix:1.0` into Minikube or use Docker Desktop Kubernetes with the local image.
- Pod pending: inspect `kubectl describe pod`, PVC status, and whether the cluster has a default StorageClass.
- Admin login unavailable: run the interactive `create-admin` command against the same persistent database used by the app.

## Project tree

```text
CampusFix/
├── app.py
├── config.py
├── database.py
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── gunicorn.conf.py
├── README.md
├── templates/
├── static/css/style.css
├── static/js/app.js
├── static/uploads/
├── instance/
├── k8s/
├── tests/test_app.py
├── screenshots/
└── report/technical-report.md
```