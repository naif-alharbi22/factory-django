# Deploying to a VPS

The application ships as a single container image. Three deployment paths are
described below — pick whichever matches your host. The first one deploys on
its own after the initial setup; the other two are manual.

---

## Path A — Automatic, on every push (recommended)

Pushing to `main` is the entire deployment. Nothing is built on the VPS and no
SSH key is stored anywhere.

- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs the
  checks and publishes the image. On a **pull request** it runs the test suite,
  verifies no migration is missing, and builds the image without pushing it —
  so a change is proven before it is merged. On a **push to main** it does the
  same and then publishes to GHCR as
  `ghcr.io/naif-alharbi22/factory-django:latest`. Tests gate the build either
  way, so a commit that breaks them is never published.
- [`compose.prod.yml`](compose.prod.yml) runs the app on the VPS from that
  image, alongside a Watchtower container that checks the tag every two minutes
  and replaces the running container when the digest changes. Migrations run on
  the new container's start, as they always do.

### 1. Push the workflow

```bash
git push origin main
```

Watch the run under the repository's **Actions** tab. The first one takes a few
minutes; later runs reuse the build cache.

### 2. Make the package public

The first push creates the GHCR package as **private**. On the repository page
open **Packages → factory-django → Package settings → Change visibility →
Public**, so the VPS can pull without credentials.

Keeping it private also works, but then the VPS needs `docker login ghcr.io`
with a token that has `read:packages`.

### 3. Start the stack on the VPS, once

```bash
cd /docker/factory-django
```

```bash
git pull
```

```bash
docker compose -f compose.prod.yml up -d
```

The `.env` file must sit next to it — the container will not start without the
Supabase connection details.

### 4. Make every compose command target this file

Two compose files sit in the directory, and a bare `docker compose ...` picks
`compose.yaml` — the one that builds from source and does not know about the
Caddy and Watchtower containers. Running `docker compose down` that way removes
the web container and then fails with *Network ... Resource is still in use*,
because the containers it does not know about are still attached.

Add this line to `.env` once, and every plain command targets the right file:

```
COMPOSE_FILE=compose.prod.yml
```

### 5. Confirm it is watching

```bash
docker logs factory-watchtower
```

A line reporting that it is scanning one container means the label matched. From
here on, every push to `main` reaches the server within a couple of minutes.

If instead it repeats:

```
Error response from daemon: client version 1.25 is too old.
Minimum supported API version is 1.40
```

then Watchtower cannot reach Docker at all and nothing will ever update. It
still defaults to API version 1.25, which daemons from Engine 29 on reject;
`DOCKER_API_VERSION` in `compose.prod.yml` pins a version they accept. Make
sure you are running the current file (`git pull`).

To follow a deployment as it lands:

```bash
docker logs -f factory-web
```

### Rolling back

Every build is also tagged with its commit SHA. To pin the previous one, replace
the `image:` line in `compose.prod.yml` with that tag and run
`docker compose -f compose.prod.yml up -d`. Remember to put `:latest` back
afterwards, or Watchtower will have nothing to follow.

### Tuning

`WATCHTOWER_INTERVAL` in `.env` changes the polling period in seconds (default
`120`). Watchtower only touches containers carrying the
`com.centurylinklabs.watchtower.enable` label, so anything else on the host is
left alone.

---

## Path B — Panel-based Docker managers

Use this when you would rather drive deployments from a control panel than
from GitHub.

Most VPS control panels (Hostinger's Docker Manager, Portainer stacks, Coolify
and similar) deploy from a **`docker-compose.yml` file only**. They run
containers on the server, but your source code is not present there — so a
compose file containing:

```yaml
build:
  context: .
```

cannot work: there is no build context on the server.

The fix is to **build the image yourself and push it to a registry**, then
deploy a compose file that references it with `image:`. That file is
[`docker-compose.deploy.yml`](docker-compose.deploy.yml).

### 1. Log in to a container registry

Using GitHub Container Registry:

```bash
echo "$REGISTRY_TOKEN" | docker login ghcr.io -u YOUR_USERNAME --password-stdin
```

The token needs the `write:packages` scope. Docker Hub works the same way with
`docker login` and no registry hostname.

### 2. Build and push

```bash
docker buildx build --platform linux/amd64 -t ghcr.io/YOUR_USERNAME/YOUR_IMAGE:latest --push .
```

`--platform linux/amd64` matters when your machine's architecture differs from
the server's (for example an Apple Silicon Mac pushing to an x86 VPS).

### 3. Make the image reachable

Either publish the package publicly, or run `docker login` on the server once so
it can pull a private image.

### 4. Deploy the compose file

Paste the contents of `docker-compose.deploy.yml` into your panel, after
replacing every value marked `CHANGE_ME`:

- `image` — the tag you pushed in step 2
- `DATABASE_URL` — the Supabase connection string (*Project Settings →
  Database → Connection string*, transaction pooler, port `6543`). Instead of
  a URL you may set `SUPABASE_PROJECT_REF`, `SUPABASE_DB_REGION` and
  `SUPABASE_DB_PASSWORD` and let the app build it. The container will not
  start without one of the two.
- `DJANGO_SECRET_KEY` — generate with:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(64))"
  ```
- `DJANGO_ALLOWED_HOSTS` — your domain and/or server IP

Database migrations are applied automatically on startup.

### 5. Open the port

Allow port `8000` in your provider's firewall, or `80`/`443` if you put a
reverse proxy in front.

### Updating

Rebuild and push the image, then redeploy from the panel, or over SSH:

```bash
docker compose pull && docker compose up -d
```

---

## Path C — Build on the server over SSH

Simpler if you would rather not use a registry. Containers started this way
still appear in your panel's Docker view.

```bash
git clone YOUR_REPOSITORY_URL /opt/app && cd /opt/app
```

```bash
cp .env.example .env && nano .env
```

Fill in the Supabase connection details (`DATABASE_URL`, or
`SUPABASE_PROJECT_REF` + `SUPABASE_DB_PASSWORD`), `DJANGO_SECRET_KEY` and
`DJANGO_ALLOWED_HOSTS`, then:

```bash
docker compose up -d --build
```

> Building the image needs roughly 1 GB of free memory. On small VPS plans the
> build may be killed — use Path A or B instead and build elsewhere.

Make sure every change is committed and pushed before deploying this way, or the
server will build an outdated version.

---

## HTTPS and a custom domain

`compose.prod.yml` includes a Caddy service that terminates TLS and forwards to
the app over the internal network. It obtains and renews a Let's Encrypt
certificate on its own — no certbot, no manual renewal.

1. Point the domain's A record at the server, and open ports **80 and 443** in
   the provider's firewall. Port 80 is not optional: the certificate is issued
   over it.
2. In `.env`:
   ```
   SITE_DOMAIN=your-domain.example
   ACME_EMAIL=you@example.com
   DJANGO_ALLOWED_HOSTS=your-domain.example
   DJANGO_BEHIND_PROXY=1
   DJANGO_SECURE_COOKIES=1
   ```
3. Start it:
   ```bash
   docker compose -f compose.prod.yml up -d
   ```
4. Watch the certificate being issued:
   ```bash
   docker logs -f factory-caddy
   ```
   It takes a few seconds to a couple of minutes. `certificate obtained
   successfully` means it worked.
5. Once HTTPS serves the site, close the direct port by adding `WEB_BIND=127.0.0.1`
   to `.env` and running the `up -d` command again, so the app is reachable
   through TLS only.

`DJANGO_BEHIND_PROXY=1` makes Django trust Caddy's `X-Forwarded-Proto`, so it
knows the request arrived over HTTPS. Without it every form submission fails
CSRF validation, because the browser sends `Origin: https://...` while Django
believes the request is plain HTTP.

**Do not set `DJANGO_SECURE_COOKIES=1` while browsing over plain HTTP** (for
example directly on `:8000`). The session and CSRF cookies are then marked
`Secure`, the browser never sends them back, and every form submission fails
with a CSRF 403 — with no hint as to why.

CSRF trusted origins are derived from `DJANGO_ALLOWED_HOSTS`, so the domain only
needs adding in one place. Set `DJANGO_CSRF_TRUSTED` explicitly only when the
public URL differs from the hostname the container sees — behind a proxy that
rewrites the `Host` header, for example. Values there must include the scheme.

If a domain is missing from `DJANGO_ALLOWED_HOSTS`, requests to it return
**HTTP 400** rather than a Django error page — check that first when a new
domain does not work.

---

## Operating the deployment

Follow the logs:

```bash
docker compose logs -f web
```

Create the first administrator account:

```bash
docker compose exec web python manage.py createsuperuser
```

Run migrations manually (when `RUN_MIGRATIONS=0`):

```bash
docker compose exec web python manage.py migrate
```

Open a shell inside the container:

```bash
docker compose exec web sh
```

---

## Notes

- Migrations run on every container start by default. If you scale to more than
  one replica, set `RUN_MIGRATIONS=0` and run migrations once as a separate
  step, so concurrent containers do not race each other.
- Application data lives in Supabase — back it up there (*Project Settings →
  Database → Backups*). Uploaded media lives in a named Docker volume and needs
  backing up separately.
- The container refuses to start when the Supabase connection details are
  missing, and `entrypoint.sh` waits up to `DB_WAIT_SECONDS` (60 by default)
  for the database to answer before giving up. Both cases are visible in
  `docker compose logs web`.
- The container exposes a health check on the login page; panels that read
  Docker health status will show the service as unhealthy if the app stops
  responding.
