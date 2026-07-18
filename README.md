# ollama

# Ollama Gateway

A secure Python/FastAPI gateway for a private Ollama service. The browser never sees or reaches Ollama directly. It includes persistent, ChatGPT-style conversations and message history.

## Architecture

```text
Frontend -> HTTPS reverse proxy -> FastAPI (JWT, Redis limits, audit) -> Ollama
                                      |-> PostgreSQL
```

Only Nginx publishes a port. PostgreSQL, Redis, Ollama, and FastAPI use the private Docker network.

## Start on the VPS

1. Install Docker Engine and the Compose plugin.
2. Copy configuration and generate strong secrets:

   ```bash
   cp .env.example .env
   # edit .env: set SECRET_KEY and POSTGRES_PASSWORD, then use the same password in DATABASE_URL
   docker compose up -d --build
   docker compose exec ollama ollama pull qwen2.5:3b
   ```

3. Visit `http://YOUR_SERVER/docs`. Put TLS in front of Nginx (Caddy, Certbot, Cloudflare, or your VPS load balancer) before public use.

## Frontend contract

Register once, then use the login token in browser memory (not localStorage if you can avoid it).

```bash
curl -X POST http://localhost/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"at-least-12-characters"}'

curl -X POST http://localhost/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"at-least-12-characters"}'
```

For a ChatGPT-style UI, use this flow after login:

1. `POST /api/v1/conversations` with `{ "model": "qwen2.5:3b" }`.
2. Render the returned conversation in the sidebar. `GET /api/v1/conversations` returns all sidebar items.
3. Send each prompt to `POST /api/v1/conversations/{id}/messages` with `{ "content": "Hello" }`.
4. Read the NDJSON stream and append each `message.content` fragment to the live assistant bubble.
5. Load a selected chat using `GET /api/v1/conversations/{id}`. Update its title or model with `PATCH`, and delete it with `DELETE` on that same URL.

Conversation CRUD examples:

```bash
# Create
curl -X POST "$API/api/v1/conversations" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"model":"qwen2.5:3b"}'

# Update title or model
curl -X PATCH "$API/api/v1/conversations/CONVERSATION_ID" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"VPS setup"}'

# Delete
curl -X DELETE "$API/api/v1/conversations/CONVERSATION_ID" -H "Authorization: Bearer $TOKEN"
```

The original stateless endpoint remains available. Call `POST /api/v1/chat` with `Authorization: Bearer TOKEN`:

```json
{
  "model": "qwen2.5:3b",
  "stream": true,
  "messages": [{"role": "user", "content": "Hello"}]
}
```

Streaming responses are newline-delimited JSON, directly compatible with Ollama's stream format. Set `stream` to `false` for one JSON response. Fetch available models from `GET /api/v1/chat/models`.

## Usage accounting

Ollama returns prompt and generated-token counts in the final streaming event. The gateway stores those values per user, model, and conversation in `usage_events`. A signed-in user can retrieve their recent events and all-time totals from `GET /api/v1/usage/me`.

## Private RAG knowledge base

The gateway supports per-user document retrieval for `.txt`, `.md`, and text-based `.pdf` documents. Documents are split into chunks, embedded locally with `nomic-embed-text`, stored in PostgreSQL with pgvector, and only the most relevant chunks are supplied to the chat model. The model is instructed to cite the filename and section, and to say when the documents do not contain an answer.

```bash
docker compose exec ollama ollama pull nomic-embed-text
```

Use authenticated endpoints:

- `POST /api/v1/knowledge/documents` — multipart form field `file`; uploads and indexes a document.
- `GET /api/v1/knowledge/documents` — lists the caller's documents.
- `DELETE /api/v1/knowledge/documents/{id}` — removes a document and its chunks.
- `POST /api/v1/knowledge/search` — tests retrieval with `{ "query": "..." }`.

The Compose database image includes pgvector. Existing PostgreSQL data is retained when Compose recreates the container, but back it up before infrastructure upgrades.

## Admin and API keys

Set `BOOTSTRAP_ADMIN_EMAIL` in `.env` to an existing registered email, then restart the API. That user becomes an administrator. Administrators can inspect all user token/request totals via `GET /api/v1/admin/overview`, create API keys with `POST /api/v1/admin/api-keys`, and revoke keys with `DELETE /api/v1/admin/api-keys/{id}`.

An API key is shown only once when created. API clients send it on each request:

```text
X-API-Key: ogw_...
```

## Production notes

- Terminate TLS and redirect HTTP to HTTPS; restrict firewall ingress to ports 80/443 and SSH.
- Set `ALLOWED_ORIGINS` to your exact frontend HTTPS origin.
- Back up the PostgreSQL volume; model files are intentionally stored separately in `ollama_data`.
- On 4 vCPU/16 GB RAM run one small Q3 model and keep context sizes conservative. The API rate limit is deliberately low by default.
- For schema changes, replace the bootstrap `create_all` approach with Alembic migrations before carrying real user data. If you started the previous schema already, create the new `conversations` and `messages` tables through a migration before deploying this version.
