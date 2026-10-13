# Deploying Emudoc

How to run the app on a server. The README's "Run it" is the development path, one `uvicorn`
on one machine; this is what changes for a deployment. There is no Dockerfile yet, so the
processes below run from a checkout with `uv`, as in development.

## The pieces

- **Postgres 17 with pgvector**, named in `DATABASE_URL`. The schema is applied as a release
  step, before the new version starts: `uv run alembic upgrade head` in `backend/`. A process
  that finds the database behind its migrations refuses to serve and says so.
- **Storage** for uploads and page images, named in `STORAGE_URL`: a directory the API and
  every worker can reach, or an S3-compatible bucket with the `AWS_*` variables.
- **The API**: `uvicorn app.main:app` in `backend/`, serving `/api` and the built frontend
  (`pnpm build` in `frontend/` before it starts). It listens on the loopback address and a
  reverse proxy in front of it ends TLS, below.
- **The worker**: `uv run python -m app.worker` in `backend/`, as many as the backlog needs. It
  reads the same settings as the API and refuses the same misconfigurations.
- **A reverse proxy** that holds the certificate and forwards to the API: Caddy or nginx, below.
- **An SMTP relay** in `SMTP_URL` for the sign-in links, and an embeddings endpoint in
  `EMBEDDING_BASE_URL` for search by meaning.

The settings arrive through the process environment or a secret manager, never a `.env` file:
`ENVIRONMENT=production` makes the app copy nothing from one, and makes it refuse to start with
development's conveniences (the compose database's password, `LOCAL_ACCOUNT=true`, no
`STORAGE_URL`, `APP_URL` or `SMTP_URL`, no key for the model), naming each. `.env.example`
documents every setting; the README's "Configuration" says what the ones that matter do.

## TLS at a reverse proxy

Nothing in the app speaks TLS. A reverse proxy holds the certificate, answers the browsers on
443, and forwards each request as plain HTTP to uvicorn on `127.0.0.1:8000`, which nothing
else can reach. The app reads three things from a request that the proxy, not the browser,
decides:

- **The scheme**, for the `Secure` flag on the session cookie and the OAuth handshake cookie.
  In production the flag is set whatever the scheme, since a deployment is always behind TLS;
  a request that still arrives as plain HTTP is logged as the misconfiguration it is, with
  what to fix.
- **The client's address**, kept on each session and shown on the account page beside the
  browser. Without the proxy's say, every session comes from the proxy.
- **The host**, for the links in emails and the OAuth redirect URI. Production takes it from
  `APP_URL`, which it requires, so no proxy header is needed for this one.

uvicorn fills the first two from the proxy's `X-Forwarded-Proto` and `X-Forwarded-For`
headers, and only when the connection comes from an address it was told to believe:
`--proxy-headers` is on by default, and `--forwarded-allow-ips` defaults to the
`FORWARDED_ALLOW_IPS` environment variable, or `127.0.0.1,::1` without it. A proxy on the same
machine therefore needs nothing more than the default command:

```bash
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

A proxy that connects from another address, such as another container, needs that address
named: `FORWARDED_ALLOW_IPS=10.0.0.5`, or a network as `10.0.0.0/24`, beside the app's own
variables (it is uvicorn's, not a setting of the app, so it does not go in `.env`). `*` believes
every connection, which is right only when nothing but the proxy can reach the port at all:
anyone else who can would choose the address their session shows and claim to be on HTTPS.
The proxy must also pass the browser's `Host` header on unchanged, which both examples below
do, so that the frontend's requests are served under the name the user opened.

### Caddy

Caddy obtains and renews the certificate itself, sets the two headers and passes `Host`
through, and streams an answer's events as they come. This is the whole configuration, as
`/etc/caddy/Caddyfile`:

```caddyfile
emudoc.example.com {
    reverse_proxy 127.0.0.1:8000
    # What one upload request may carry: MAX_UPLOAD_REQUEST_MB, 500 by default. The proxy
    # refuses a larger body before the app has read it.
    request_body {
        max_size 500MB
    }
}
```

### nginx

nginx needs the headers set by hand, the body limit, and a read timeout longer than its default
minute: an answer streams as server-sent events, and one request to the model can take longer
than that. The app sends `X-Accel-Buffering: no` on that stream, which nginx honours, so the
events are not buffered.

```nginx
server {
    listen 443 ssl;
    server_name emudoc.example.com;
    ssl_certificate     /etc/letsencrypt/live/emudoc.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/emudoc.example.com/privkey.pem;

    # MAX_UPLOAD_REQUEST_MB, 500 by default.
    client_max_body_size 500m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # An answer's event stream is silent while the model thinks.
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name emudoc.example.com;
    return 301 https://$host$request_uri;
}
```

`$proxy_add_x_forwarded_for` appends the client to any `X-Forwarded-For` the browser sent, and
uvicorn takes the rightmost address it was not told to believe, so a header forged by the
browser changes nothing.

## Checking it

1. `curl -sS https://emudoc.example.com/api/health` answers `{"status":"ok"}` and the browser
   shows the certificate. Plain `http://` redirects to it.
2. Sign in. The account page lists the session with your own address, not the proxy's, and
   the API's log has no line saying a request "reached the app as plain HTTP": that line
   means the proxy did not send `X-Forwarded-Proto`, or uvicorn was not told to believe it.
3. Upload a document and ask a question about it: the upload goes through the proxy's body
   limit, and the answer appears as it is written rather than all at once.
