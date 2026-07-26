# Codex Task — Implement Phase 2 Database Foundation

Work inside this existing Django project. Do not recreate the project, rename
apps, delete Phase 1 configuration, copy another repository, or modify the
shared ERD design.

## Objective

Implement the shared chatbot database foundation using Django models,
migrations, admin registration, signals and focused model tests. Keep API
serializers/views/endpoints for later phases unless needed minimally for model
validation.

## Required models and relationships

Use Django's default `AUTH_USER_MODEL` for users.

### 1. `accounts.UserProfile`

- `user`: OneToOneField to `AUTH_USER_MODEL`, related name `profile`, cascade
- `avatar`: ImageField, optional, upload to `profiles/avatars/`
- `bio`: TextField, optional
- `phone`: CharField, max length 20, optional
- `theme`: CharField with `light`, `dark`, `system`; default `system`
- `language`: CharField, max length 10; default `en`
- `email_notifications`: BooleanField; default `True`
- `created_at`: DateTimeField auto-created
- `updated_at`: DateTimeField auto-updated

Create the profile automatically whenever a new user is created. Use a Django
signal and ensure app configuration loads it correctly. Avoid duplicate
profiles.

### 2. `chats.ChatSession`

- `id`: UUIDField primary key, default `uuid.uuid4`, not editable
- `user`: ForeignKey to `AUTH_USER_MODEL`, related name `chat_sessions`, cascade
- `title`: CharField max length 255; default `New Chat`
- `model_name`: CharField max length 100; default `default`
- `system_prompt`: TextField, optional
- `is_archived`: BooleanField default `False`
- `created_at`: DateTimeField auto-created
- `updated_at`: DateTimeField auto-updated
- `last_message_at`: DateTimeField nullable/optional

Ordering: newest `last_message_at` first, then newest `created_at`.
Add useful database indexes for user/archive/time queries.

### 3. `chats.ChatMessage`

- `id`: UUIDField primary key, default `uuid.uuid4`, not editable
- `session`: ForeignKey to ChatSession, related name `messages`, cascade
- `role`: choices `user`, `assistant`, `system`
- `content`: TextField
- `status`: choices `sent`, `error`, `streaming`; default `sent`
- `metadata`: JSONField default dict, blank
- `created_at`: DateTimeField auto-created
- `updated_at`: DateTimeField auto-updated

Ordering: oldest message first. Add an index for session + creation time.
When a message is created, update its session's `last_message_at` efficiently.
Do not create recursive save behaviour.

### 4. `chats.AIUsage`

- `id`: BigAutoField primary key
- `session`: ForeignKey to ChatSession, related name `ai_usage_records`, cascade
- `message`: ForeignKey to ChatMessage, related name `ai_usage_records`, cascade
- `model_name`: CharField max length 100
- `prompt_tokens`: PositiveIntegerField default 0
- `completion_tokens`: PositiveIntegerField default 0
- `total_tokens`: PositiveIntegerField default 0
- `cost`: DecimalField max digits 12, decimal places 6, default 0
- `latency_ms`: PositiveIntegerField nullable/optional
- `created_at`: DateTimeField auto-created

Validate that `message.session_id` equals `session_id`. Keep total token data
consistent: either validate the supplied total or calculate it in a clean,
testable way. Do not use floating-point fields for cost.

### 5. `accounts.PasswordResetToken`

- `id`: UUIDField primary key, default `uuid.uuid4`, not editable
- `user`: ForeignKey to `AUTH_USER_MODEL`, related name
  `password_reset_tokens`, cascade
- `token_hash`: CharField max length 128, unique and indexed
- `expires_at`: DateTimeField
- `used_at`: DateTimeField nullable/optional
- `created_at`: DateTimeField auto-created

Do not store raw reset tokens. Add model properties or methods such as
`is_expired`, `is_used`, and `is_valid` using timezone-aware comparisons.

## Engineering requirements

1. Add `rest_framework_simplejwt.token_blacklist` to `INSTALLED_APPS` so later logout can blacklist refresh tokens.
2. Keep models split correctly between `accounts` and `chats`.
3. Use `settings.AUTH_USER_MODEL`, not a hard-coded User import, in relations.
4. Add readable `__str__` methods.
5. Register all models in Django admin with useful list displays, filters,
   searches and read-only timestamps.
6. Create and commit initial app migrations.
7. Add Pillow to requirements because `ImageField` needs it.
8. Add model tests covering:
   - automatic profile creation
   - session ownership and UUID primary key
   - message ordering and `last_message_at` update
   - role/status choices
   - AIUsage session/message consistency and token totals
   - password-reset validity before/after expiry and use
   - cascade deletion behaviour
9. Run and fix:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py test
```

10. Do not commit `.venv`, `.env`, `db.sqlite3`, cache folders or generated
   media.
11. Update README with a concise Phase 2 section and model relationship summary.

## Acceptance criteria

- All six ERD entities exist: User, UserProfile, ChatSession, ChatMessage,
  AIUsage and PasswordResetToken.
- Shared ERD structure is preserved.
- Migrations apply on a fresh SQLite database.
- `python manage.py check` reports no issues.
- All automated tests pass.
- No authentication, CRUD or password-reset API is invented in this phase.
