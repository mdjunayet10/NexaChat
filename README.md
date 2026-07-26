# NexaChat DRF Backend — Phase 6

NexaChat is an authenticated Django REST Framework chatbot backend. It provides
JWT accounts, profiles, user-owned chat sessions and messages, secure password
reset, internal AI usage accounting, an offline deterministic provider and an
optional OpenRouter-backed complete chat flow. The original Phase 1 project
structure and Phase 2 database schema remain intact.

## Architecture and technology

```text
API client
   │  JWT Bearer token
   ▼
Django REST Framework views and serializers
   │
   ├── accounts services ── password-reset digest/email/JWT revocation
   │
   └── chat orchestration
          ├── bounded conversation context
          ├── provider factory ── mock or OpenRouter
          └── short database transaction
                 ├── assistant ChatMessage
                 └── AIUsage through record_ai_usage()
```

The stack is Python 3, Django 6, Django REST Framework, SimpleJWT with refresh
blacklisting, drf-yasg Swagger/OpenAPI, SQLite, Pillow and HTTPX. No frontend,
background worker or AI orchestration framework is included.

## Phase 1 completed

- Fresh Django project with `config` settings package
- Separate `accounts` and `chats` apps
- Django REST Framework setup
- JWT authentication settings prepared with access and refresh-token lifetimes
- SQLite database configuration
- Swagger, ReDoc and raw OpenAPI schema routes
- Public health-check endpoint
- Bangladesh timezone and local media configuration
- Basic automated test for the project foundation
- Environment example, requirements and Git ignore files

## Phase 2 completed

- `UserProfile` preferences and optional avatar data, created automatically for
  every new Django user
- Hashed `PasswordResetToken` records with expiry, usage and validity helpers
- User-owned UUID `ChatSession` records with archive and activity metadata
- Ordered UUID `ChatMessage` records that update session activity timestamps
- `AIUsage` records with session/message validation, exact decimal cost storage
  and calculated token totals
- Database indexes for common session and message queries
- Django admin configuration for all Phase 2 models
- Initial app migrations and focused model tests
- Simple JWT refresh-token blacklist database support

### Model relationships

```text
User ──1:1── UserProfile
User ──1:N── PasswordResetToken
User ──1:N── ChatSession ──1:N── ChatMessage
                      └──1:N── AIUsage ──N:1── ChatMessage
```

Deleting a user cascades through the related profile, reset tokens and chat
data. Deleting a chat session also removes its messages and usage records.
Feature APIs were intentionally out of scope for Phase 2.

## Phase 3 completed

- JWT registration with Django password validation and automatic profiles
- JWT login with safe credential errors
- SimpleJWT access-token refresh with the configured refresh-token rotation
- Authenticated logout with refresh-token ownership checks and blacklisting
- Authenticated retrieval and atomic update of the current user's account and
  profile fields
- Case-insensitive duplicate-email validation for registration and profile
  updates
- Swagger/OpenAPI request and response documentation
- Focused API, security, routing and generated-schema tests

No database models or Phase 2 relationships were changed in Phase 3.

## Phase 4 completed

- Authenticated chat-session list, create, retrieve, update and delete APIs
- Authenticated user-message create, list, retrieve and delete APIs
- Queryset-level owner filtering for every session and message operation
- UUID route converters and documented UUID OpenAPI parameters
- Server-controlled session ownership, message session, role, status and
  metadata
- Existing session-activity signal and cascade behavior preserved
- Focused API, ownership, validation, cascade and generated-schema tests

No database model or migration changes were needed for Phase 4.

## Phase 5 completed

- Internal-only AI usage recording with relationship, role, token and
  Decimal-cost validation
- Authenticated user and owned-session usage history
- Database-aggregated per-user token and cost summary
- Password-reset request, token verification and password confirmation APIs
- SHA-256 reset-token digest storage, 30-minute default expiry, single use and
  invalidation of older tokens
- Django email delivery with a safe local console backend
- Django password validation and refresh-token revocation after reset
- Swagger/OpenAPI documentation and focused security/regression tests

No database model or migration changes were needed for Phase 5.

## Phase 6 completed

- Provider-independent typed AI interface and safe exception hierarchy
- Deterministic offline mock provider with simulated tokens and zero cost
- Optional OpenRouter chat-completions adapter using HTTPX and explicit timeout
- Bounded chronological conversation context with a separate server-owned
  system prompt
- Authenticated complete chat endpoint that creates user and assistant messages
- Automatic `AIUsage` recording through the existing Phase 5 service
- Safe `502`, `503` and `504` provider-failure responses
- Swagger/OpenAPI coverage, offline automated tests and importable Postman files

No database model or migration changes were needed for Phase 6.

## Project structure

```text
NexaChat/
├── accounts/
│   ├── migrations/
│   ├── serializers.py
│   ├── services.py
│   └── signals.py
├── chats/
│   ├── ai/
│   │   ├── base.py
│   │   ├── context.py
│   │   ├── exceptions.py
│   │   ├── factory.py
│   │   ├── mock.py
│   │   └── openrouter.py
│   ├── migrations/
│   ├── serializers.py
│   ├── services.py
│   ├── signals.py
│   └── test_phase6.py
├── config/
├── postman/
│   ├── NexaChat.local.postman_environment.json
│   └── NexaChat.postman_collection.json
├── .env.example
├── .gitignore
├── CODEX_PHASE_2_PROMPT.md
├── README.md
├── manage.py
└── requirements.txt
```

## Phase 3 API

### Authentication endpoints

| Method | Endpoint | Access | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/accounts/register/` | Public | Register and receive JWT tokens |
| `POST` | `/api/accounts/login/` | Public | Log in and receive JWT tokens |
| `POST` | `/api/accounts/token/refresh/` | Public | Rotate a refresh token and receive new tokens |
| `POST` | `/api/accounts/logout/` | Bearer JWT | Blacklist a refresh token |

### Profile endpoints

| Method | Endpoint | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/accounts/profile/` | Bearer JWT | Retrieve the current user's profile |
| `PUT` | `/api/accounts/profile/` | Bearer JWT | Replace editable current-user/profile fields |
| `PATCH` | `/api/accounts/profile/` | Bearer JWT | Partially update current-user/profile fields |

Protected requests use the access token:

```http
Authorization: Bearer <access_token>
```

The profile route always uses the authenticated user. It does not accept a user
ID, expose passwords, or permit password changes.

### Register

```http
POST /api/accounts/register/
Content-Type: application/json
```

```json
{
  "username": "junayet",
  "email": "junayet@example.com",
  "password": "StrongPass123!",
  "password_confirm": "StrongPass123!",
  "first_name": "Md",
  "last_name": "Junayet"
}
```

A successful request returns HTTP `201` with the safe user summary plus
`access` and `refresh` tokens.

### Login

```http
POST /api/accounts/login/
Content-Type: application/json
```

```json
{
  "username": "junayet",
  "password": "StrongPass123!"
}
```

A successful request returns HTTP `200` with the safe user summary and both
JWT tokens. Invalid, unknown or inactive-user credentials return the same safe
HTTP `401` response.

### Refresh

```http
POST /api/accounts/token/refresh/
Content-Type: application/json
```

```json
{
  "refresh": "<refresh_token>"
}
```

Refresh-token rotation and blacklisting are enabled. The response contains a
new `access` token and a rotated `refresh` token; the submitted refresh token
can no longer be reused.

### Logout

```http
POST /api/accounts/logout/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "refresh": "<refresh_token>"
}
```

A successful request returns:

```json
{
  "message": "Logout successful."
}
```

### Retrieve and update the profile

```http
GET /api/accounts/profile/
Authorization: Bearer <access_token>
```

```json
{
  "id": 1,
  "username": "junayet",
  "email": "junayet@example.com",
  "first_name": "Md",
  "last_name": "Junayet",
  "profile": {
    "id": 1,
    "avatar": null,
    "bio": "",
    "phone": "",
    "theme": "system",
    "language": "en",
    "email_notifications": true,
    "created_at": "2026-07-25T12:00:00+06:00",
    "updated_at": "2026-07-25T12:00:00+06:00"
  }
}
```

Partial update example:

```http
PATCH /api/accounts/profile/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "email": "new-email@example.com",
  "first_name": "Md Junayet",
  "profile": {
    "bio": "Building helpful software.",
    "theme": "dark",
    "language": "bn",
    "email_notifications": false
  }
}
```

## Phase 4 API

All Phase 4 routes require:

```http
Authorization: Bearer <access_token>
```

### Chat Session endpoints

| Method | Endpoint | Successful status | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/chats/sessions/` | `200` | List the authenticated user's sessions |
| `POST` | `/api/chats/sessions/` | `201` | Create an owned session |
| `GET` | `/api/chats/sessions/<session_uuid>/` | `200` | Retrieve an owned session |
| `PUT` | `/api/chats/sessions/<session_uuid>/` | `200` | Replace editable session fields |
| `PATCH` | `/api/chats/sessions/<session_uuid>/` | `200` | Partially update a session |
| `DELETE` | `/api/chats/sessions/<session_uuid>/` | `204` | Delete a session and cascade its related data |

Create a session:

```http
POST /api/chats/sessions/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "title": "Django Learning",
  "model_name": "default",
  "system_prompt": "Answer clearly.",
  "is_archived": false
}
```

The response uses the existing model fields:

```json
{
  "id": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
  "title": "Django Learning",
  "model_name": "default",
  "system_prompt": "Answer clearly.",
  "is_archived": false,
  "created_at": "2026-07-25T13:00:00+06:00",
  "updated_at": "2026-07-25T13:00:00+06:00",
  "last_message_at": null
}
```

Session lists return an unpaginated JSON array because no global pagination is
configured:

```json
[
  {
    "id": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "title": "Django Learning",
    "model_name": "default",
    "system_prompt": "Answer clearly.",
    "is_archived": false,
    "created_at": "2026-07-25T13:00:00+06:00",
    "updated_at": "2026-07-25T13:00:00+06:00",
    "last_message_at": null
  }
]
```

Partial update:

```http
PATCH /api/chats/sessions/5d88ea64-d857-4ed3-9c4a-76cb15bba820/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "title": "Advanced Django Learning",
  "is_archived": true
}
```

### Chat Message endpoints

| Method | Endpoint | Successful status | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/chats/sessions/<session_uuid>/messages/` | `200` | List an owned session's messages oldest-first |
| `POST` | `/api/chats/sessions/<session_uuid>/messages/` | `201` | Create a user message |
| `GET` | `/api/chats/messages/<message_uuid>/` | `200` | Retrieve an owned message |
| `DELETE` | `/api/chats/messages/<message_uuid>/` | `204` | Delete an owned message |

Create a user message:

```http
POST /api/chats/sessions/5d88ea64-d857-4ed3-9c4a-76cb15bba820/messages/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "content": "What is Django REST Framework?"
}
```

The session and `user` role are assigned by the server:

```json
{
  "id": "3d4bb676-a3e5-4312-8918-41b80e1e120a",
  "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
  "role": "user",
  "content": "What is Django REST Framework?",
  "status": "sent",
  "created_at": "2026-07-25T13:05:00+06:00",
  "updated_at": "2026-07-25T13:05:00+06:00"
}
```

Message lists return the same fields in chronological order:

```json
[
  {
    "id": "3d4bb676-a3e5-4312-8918-41b80e1e120a",
    "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "role": "user",
    "content": "What is Django REST Framework?",
    "status": "sent",
    "created_at": "2026-07-25T13:05:00+06:00",
    "updated_at": "2026-07-25T13:05:00+06:00"
  }
]
```

Delete a message:

```http
DELETE /api/chats/messages/3d4bb676-a3e5-4312-8918-41b80e1e120a/
Authorization: Bearer <access_token>
```

A successful delete returns HTTP `204` with no response body. Message editing
is not available in Phase 4.

### Ownership and status behavior

- Session queries always filter by `request.user`.
- Message queries always filter through `session__user=request.user`.
- Nested message routes first resolve an owned session.
- Request bodies cannot assign session ownership or create assistant/system
  messages.
- Missing and unowned UUIDs both return `404`, so object ownership is not
  disclosed.
- Missing authentication returns `401`; invalid fields return structured
  `400` responses; successful deletion returns `204`.
- Session deletion relies on the existing database cascades for messages and
  AI usage records.

## Phase 5 API

### AI usage architecture

AI usage is written only by trusted backend code through
`chats.services.record_ai_usage()`. There is no public create, update or delete
endpoint. The service requires a user-role triggering message from the supplied
session, rejects negative or non-integer token counts and negative or
floating-point cost inputs, and preserves cost as `Decimal`. It creates only an
`AIUsage` record; it does not call an AI provider, change message content or
create an assistant message.

All usage endpoints require a Bearer access token:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/chats/usage/` | List the authenticated user's usage newest-first |
| `GET` | `/api/chats/usage/summary/` | Aggregate the authenticated user's usage |
| `GET` | `/api/chats/sessions/<session_uuid>/usage/` | List usage for an owned session |

Usage history is an unpaginated array under the current global settings:

```json
[
  {
    "id": 1,
    "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "message": "3d4bb676-a3e5-4312-8918-41b80e1e120a",
    "model_name": "default",
    "prompt_tokens": 120,
    "completion_tokens": 45,
    "total_tokens": 165,
    "cost": "0.003200",
    "latency_ms": 850,
    "created_at": "2026-07-25T14:00:00+06:00"
  }
]
```

The summary is calculated in the database and returns Decimal cost as a
six-place string:

```json
{
  "usage_records": 12,
  "prompt_tokens": 3200,
  "completion_tokens": 1675,
  "total_tokens": 4875,
  "total_cost": "0.038200"
}
```

An account can never see another user's usage. The nested endpoint resolves the
session through the authenticated owner and returns `404` for missing and
unowned UUIDs.

### Password reset

| Method | Endpoint | Access | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/accounts/password-reset/request/` | Public | Email password-reset instructions |
| `POST` | `/api/accounts/password-reset/verify/` | Public | Check an unexpired, unused reset token |
| `POST` | `/api/accounts/password-reset/confirm/` | Public | Validate and set a new password |

Request instructions:

```http
POST /api/accounts/password-reset/request/
Content-Type: application/json
```

```json
{
  "email": "user@example.com"
}
```

Known active accounts and unknown or inactive accounts receive the same HTTP
`200` response to prevent account enumeration:

```json
{
  "message": "If an account exists for this email, password reset instructions have been sent."
}
```

For an active account, NexaChat generates a cryptographically secure raw token,
stores only its SHA-256 hexadecimal digest and emails the raw value. A new
request consumes the user's older unused tokens. Tokens expire after
`PASSWORD_RESET_TOKEN_TTL_MINUTES` (30 minutes by default) and can be used only
once.

Verify a token without consuming it:

```http
POST /api/accounts/password-reset/verify/
Content-Type: application/json
```

```json
{
  "email": "user@example.com",
  "token": "<raw-reset-token>"
}
```

```json
{
  "valid": true,
  "message": "Reset token is valid."
}
```

Invalid, expired, consumed, mismatched-email and inactive-user cases all return
the same generic HTTP `400` token error.

Confirm a new password:

```http
POST /api/accounts/password-reset/confirm/
Content-Type: application/json
```

```json
{
  "email": "user@example.com",
  "token": "<raw-reset-token>",
  "new_password": "NewStrongPass123!",
  "new_password_confirm": "NewStrongPass123!"
}
```

The new password must differ from the current password and pass Django's
configured password validators. Confirmation transactionally re-checks and
consumes the reset token, hashes the new password with `set_password()`,
invalidates the user's other reset tokens and blacklists every outstanding
SimpleJWT refresh token. It never returns passwords, reset tokens or new JWTs.
Previously issued access tokens cannot be blacklisted by the current
architecture and therefore remain usable only until their configured short
30-minute expiry.

### Local password-reset email

Local development defaults to Django's console email backend, so the reset
message and raw token appear in the terminal running `runserver`. Automated
tests override this with Django's in-memory backend and inspect
`django.core.mail.outbox`.

The optional environment settings are:

```dotenv
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DJANGO_DEFAULT_FROM_EMAIL=NexaChat <no-reply@nexachat.local>
PASSWORD_RESET_TOKEN_TTL_MINUTES=30
```

Use environment variables for any production email configuration; do not add
SMTP credentials to source control.

## Phase 6 AI chat

Only the complete chat endpoint invokes the configured provider:

```http
POST /api/chats/sessions/<session_uuid>/chat/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "content": "What is Django REST Framework?"
}
```

With the default mock provider, a successful HTTP `201` response is:

```json
{
  "user_message": {
    "id": "3d4bb676-a3e5-4312-8918-41b80e1e120a",
    "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "role": "user",
    "content": "What is Django REST Framework?",
    "status": "sent",
    "created_at": "2026-07-25T15:00:00+06:00",
    "updated_at": "2026-07-25T15:00:00+06:00"
  },
  "assistant_message": {
    "id": "76abf933-f534-4167-9453-0dde74dd807b",
    "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "role": "assistant",
    "content": "Mock NexaChat response: What is Django REST Framework?",
    "status": "sent",
    "created_at": "2026-07-25T15:00:01+06:00",
    "updated_at": "2026-07-25T15:00:01+06:00"
  },
  "usage": {
    "id": 1,
    "session": "5d88ea64-d857-4ed3-9c4a-76cb15bba820",
    "message": "3d4bb676-a3e5-4312-8918-41b80e1e120a",
    "model_name": "mock-nexachat-v1",
    "prompt_tokens": 27,
    "completion_tokens": 8,
    "total_tokens": 35,
    "cost": "0.000000",
    "latency_ms": 0,
    "created_at": "2026-07-25T15:00:01+06:00"
  }
}
```

The exact mock token values depend deterministically on the configured system
prompt and bounded conversation text. They are simulated whitespace-based
estimates, not model-tokenizer results. Mock cost is always `0.000000`.

### Provider architecture

`chats.ai.base.AIProvider` accepts provider messages plus a separate system
prompt and returns a validated `ChatCompletionResult`. The provider factory
selects `mock` or `openrouter` case-insensitively from server configuration.
Unknown or incomplete configurations fail explicitly; OpenRouter never silently
falls back to mock.

The mock provider works completely offline, echoes at most 500 characters from
the latest usable user message, and produces deterministic internally
consistent token counts.

The OpenRouter provider sends a non-streaming OpenAI-compatible
`POST /api/v1/chat/completions` request through HTTPX. The API key is present
only in the Bearer authorization header. Requests include the configured model,
system prompt, bounded conversation, temperature and maximum output tokens.
Optional `HTTP-Referer` and `X-OpenRouter-Title` headers are added only when
configured. TLS verification remains enabled and the request has an explicit
timeout.

Returned prompt, completion and total tokens are checked for non-negative
consistency. A provider cost is accepted only from the structured usage object,
converted through `Decimal(str(value))` and rounded to the model's six decimal
places. Cost defaults to `0.000000` when reliable cost data is absent; NexaChat
does not invent pricing or fetch model prices dynamically.

### Conversation and transaction behaviour

NexaChat selects at most `AI_CONTEXT_MESSAGE_LIMIT` messages from the requested
owned session, keeps only user and assistant roles, skips malformed blank
history and returns the selected messages oldest-first. The newly saved user
message is added exactly once. Database IDs, account data, message metadata,
JWTs and password-reset data are never added to provider messages.

The configured `AI_SYSTEM_PROMPT` is passed separately. Clients cannot select a
provider, submit usage values or replace the system prompt.

The complete workflow is:

1. Resolve the session through the authenticated owner.
2. Validate and persist one user message.
3. Build bounded conversation context.
4. Invoke the provider without an open application transaction.
5. In a short transaction, create one assistant message and one `AIUsage`
   record linked to the triggering user message.

If the provider fails, the saved user message remains and no assistant message
or usage record is created. Safe failures include its ID:

```json
{
  "detail": "The AI service is temporarily unavailable.",
  "user_message_id": "3d4bb676-a3e5-4312-8918-41b80e1e120a"
}
```

Configuration, authentication, rate-limit and availability failures return
`503`; timeouts return `504`; malformed or rejected provider responses return
`502`. Raw upstream bodies, API keys and stack traces are never returned. If
the final assistant/usage transaction fails, both writes roll back and the
endpoint returns a safe `500`.

The existing raw message endpoint remains intentionally separate:

```http
POST /api/chats/sessions/<session_uuid>/messages/
```

It creates only a user message. It never calls an AI provider and never creates
`AIUsage`.

## Complete endpoint reference

| Method | Endpoint | Access | Success |
| --- | --- | --- | --- |
| `GET` | `/api/health/` | Public | `200` |
| `GET` | `/api/docs/` | Public | `200` |
| `GET` | `/api/redoc/` | Public | `200` |
| `GET` | `/api/schema.json` | Public | `200` |
| `POST` | `/api/accounts/register/` | Public | `201` |
| `POST` | `/api/accounts/login/` | Public | `200` |
| `POST` | `/api/accounts/token/refresh/` | Public | `200` |
| `POST` | `/api/accounts/logout/` | Bearer JWT | `200` |
| `GET` | `/api/accounts/profile/` | Bearer JWT | `200` |
| `PUT` | `/api/accounts/profile/` | Bearer JWT | `200` |
| `PATCH` | `/api/accounts/profile/` | Bearer JWT | `200` |
| `POST` | `/api/accounts/password-reset/request/` | Public | `200` |
| `POST` | `/api/accounts/password-reset/verify/` | Public | `200` |
| `POST` | `/api/accounts/password-reset/confirm/` | Public | `200` |
| `GET` | `/api/chats/sessions/` | Bearer JWT | `200` |
| `POST` | `/api/chats/sessions/` | Bearer JWT | `201` |
| `GET` | `/api/chats/sessions/<uuid>/` | Bearer JWT | `200` |
| `PUT` | `/api/chats/sessions/<uuid>/` | Bearer JWT | `200` |
| `PATCH` | `/api/chats/sessions/<uuid>/` | Bearer JWT | `200` |
| `DELETE` | `/api/chats/sessions/<uuid>/` | Bearer JWT | `204` |
| `GET` | `/api/chats/sessions/<uuid>/messages/` | Bearer JWT | `200` |
| `POST` | `/api/chats/sessions/<uuid>/messages/` | Bearer JWT | `201` |
| `GET` | `/api/chats/messages/<uuid>/` | Bearer JWT | `200` |
| `DELETE` | `/api/chats/messages/<uuid>/` | Bearer JWT | `204` |
| `POST` | `/api/chats/sessions/<uuid>/chat/` | Bearer JWT | `201` |
| `GET` | `/api/chats/usage/` | Bearer JWT | `200` |
| `GET` | `/api/chats/usage/summary/` | Bearer JWT | `200` |
| `GET` | `/api/chats/sessions/<uuid>/usage/` | Bearer JWT | `200` |

Common status codes are `400` for request validation, `401` for missing or
invalid JWT authentication, `404` for missing or unowned resources, `502` for
an invalid upstream response, `503` for unavailable or misconfigured provider
access, and `504` for a provider timeout.

## AI environment configuration

The default setup works offline:

```dotenv
AI_PROVIDER=mock
```

All Phase 6 options are:

```dotenv
AI_PROVIDER=mock
AI_REQUEST_TIMEOUT_SECONDS=30
AI_MAX_OUTPUT_TOKENS=512
AI_TEMPERATURE=0.7
AI_CONTEXT_MESSAGE_LIMIT=20
AI_SYSTEM_PROMPT=You are NexaChat, a helpful and concise AI assistant.

OPENROUTER_API_KEY=
OPENROUTER_MODEL=
OPENROUTER_BASE_URL=
OPENROUTER_SITE_URL=
OPENROUTER_APP_NAME=NexaChat
```

To use OpenRouter:

1. Copy `.env.example` to `.env`.
2. Set `AI_PROVIDER=openrouter`.
3. Set `OPENROUTER_API_KEY`.
4. Set an active `OPENROUTER_MODEL` identifier.
5. Optionally set the base URL, site URL and application name.
6. Restart Django.

NexaChat loads the project-root `.env` automatically before reading Django or
AI settings. Existing operating-system environment variables keep priority
over `.env`, which is appropriate for production and process-manager settings.

If `OPENROUTER_BASE_URL` is empty, NexaChat uses
`https://openrouter.ai/api/v1`. OpenRouter credentials are never needed in mock
mode and must remain in the server environment, never in Postman or client
request bodies.

### Check AI provider readiness

Validate the selected provider configuration without contacting any external
service:

```bash
python manage.py check_ai_config
```

The command reports the selected provider, whether the OpenRouter API key and
model are configured, and whether the configuration is ready. It never prints
the key or any part of it, never makes a network request, and exits non-zero
when the selected provider configuration is invalid.

The provider test command is also offline by default:

```bash
python manage.py test_ai_provider
python manage.py test_ai_provider --message "Confirm provider readiness."
```

Without `--live`, it only constructs the provider through the Phase 6 factory
and validates its configuration. The optional message is not sent in this
mode. A real provider completion is an explicit operation:

```bash
python manage.py test_ai_provider --live
python manage.py test_ai_provider --live --message "Reply with OK."
```

`--live` may contact the configured external provider. The command sends one
message through the existing provider abstraction, prints only safe response
metadata, and does not create `ChatMessage` or `AIUsage` records. Configuration,
provider, and timeout failures return a non-zero exit status without printing
credentials or raw provider error details.

## Postman

Import both:

- `postman/NexaChat.postman_collection.json`
- `postman/NexaChat.local.postman_environment.json`

Select the **NexaChat Local** environment. Registration and login save access
and refresh tokens, refresh updates rotated tokens, session creation saves the
session UUID, and raw/complete chat requests save message UUIDs. Protected
requests inherit Bearer authentication.

For password reset, run the request endpoint, copy the raw token from Django's
local console email into the `reset_token` environment variable, then run
verify or confirm. The API response intentionally never provides that token.
Run destructive message/session delete requests only after dependent examples.
The OpenRouter API key is intentionally absent from the Postman environment.

## Run locally

### 1. Create and activate a virtual environment

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure the environment

Mock mode needs no credential:

```bash
export AI_PROVIDER=mock
```

For other settings, use the environment configuration section above. Django
loads the ignored project-root `.env` automatically. Variables supplied by the
operating system, shell, or process manager take precedence.

### 4. Create the local database

```bash
python manage.py migrate
```

Each student must generate their own `db.sqlite3` locally.

### 5. Validate and test

```bash
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test
python manage.py generate_swagger - --format json
python -m json.tool postman/NexaChat.postman_collection.json
python -m json.tool postman/NexaChat.local.postman_environment.json
```

The complete automated suite uses mocks or the deterministic mock provider and
never contacts OpenRouter or any other external AI service.

### 6. Start the server

```bash
python manage.py runserver
```

Open:

- Health: `http://127.0.0.1:8000/api/health/`
- Swagger: `http://127.0.0.1:8000/api/docs/`
- ReDoc: `http://127.0.0.1:8000/api/redoc/`
- OpenAPI JSON: `http://127.0.0.1:8000/api/schema.json`
- Admin: `http://127.0.0.1:8000/admin/`

## Expected health response

```json
{
  "status": "ok",
  "project": "Chatbot DRF Backend",
  "phase": 1
}
```

## Phase scope

Phase 6 includes offline mock chat and optional synchronous OpenRouter chat
completion with automatic assistant messages and usage records. Public AI
usage writes, frontend code, WebSockets, streaming, Server-Sent Events,
attachments, voice, RAG, embeddings, vector databases, search, sharing,
collaboration, background workers, payments, subscriptions, fine-tuning and
Phase 7 or later functionality are not implemented.
