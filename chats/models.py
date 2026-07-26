import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    title = models.CharField(max_length=255, default="New Chat")
    model_name = models.CharField(max_length=100, default="default")
    system_prompt = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["user", "is_archived", "-last_message_at"],
                name="chat_user_archive_time_idx",
            ),
            models.Index(
                fields=["user", "-created_at"],
                name="chat_user_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.user})"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        ERROR = "error", "Error"
        STREAMING = "streaming", "Streaming"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["session", "created_at"],
                name="chat_message_session_time_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_role_display()} message in {self.session.title}"


class AIUsage(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="ai_usage_records",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="ai_usage_records",
    )
    model_name = models.CharField(max_length=100)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def calculate_total_tokens(self):
        return self.prompt_tokens + self.completion_tokens

    def clean(self):
        super().clean()
        self.total_tokens = self.calculate_total_tokens()

        message_session_id = None
        if self.message_id:
            cached_message = self._state.fields_cache.get("message")
            if cached_message is not None:
                message_session_id = cached_message.session_id
            else:
                message_session_id = (
                    ChatMessage.objects.filter(pk=self.message_id)
                    .values_list("session_id", flat=True)
                    .first()
                )

        if message_session_id and message_session_id != self.session_id:
            raise ValidationError(
                {"message": "The message must belong to the selected session."}
            )

    def save(self, *args, **kwargs):
        self.total_tokens = self.calculate_total_tokens()
        self.full_clean()

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"total_tokens"}

        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.model_name} usage for {self.message}"
