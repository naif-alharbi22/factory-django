# Deploying to a VPS

The application ships as a single container image. Two deployment paths are
described below — pick whichever matches your host.

---

## Path A — Panel-based Docker managers (recommended)

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
- `DATABASE_URL` — your database connection URL, or leave empty for SQLite
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

## Path B — Build on the server over SSH

Simpler if you would rather not use a registry. Containers started this way
still appear in your panel's Docker view.

```bash
git clone YOUR_REPOSITORY_URL /opt/app && cd /opt/app
```

```bash
cp .env.example .env && nano .env
```

Fill in `DATABASE_URL`, `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`, then:

```bash
docker compose up -d --build
```

> Building the image needs roughly 1 GB of free memory. On small VPS plans the
> build may be killed — use Path A instead and build on your own machine.

Make sure every change is committed and pushed before deploying this way, or the
server will build an outdated version.

---

## HTTPS and a custom domain

Once a reverse proxy (Nginx, Caddy, or your provider's) terminates TLS:

1. Bind the container to localhost only, so it is not reachable directly:
   ```yaml
   ports:
     - "127.0.0.1:8000:8000"
   ```
2. Enable the proxy-aware settings:
   ```yaml
   DJANGO_BEHIND_PROXY: "1"
   DJANGO_SECURE_COOKIES: "1"
   DJANGO_CSRF_TRUSTED: "https://your-domain.example"
   ```

Skipping `DJANGO_CSRF_TRUSTED` will cause form submissions to be rejected.

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
- Uploaded media and the SQLite fallback database live in named volumes. Back
  these up along with your database.
- The container exposes a health check on the login page; panels that read
  Docker health status will show the service as unhealthy if the app stops
  responding.
