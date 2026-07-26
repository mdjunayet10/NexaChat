from django.contrib import admin

from .models import AIUsage, ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "model_name",
        "is_archived",
        "last_message_at",
        "created_at",
    )
    list_filter = ("is_archived", "model_name", "created_at", "last_message_at")
    search_fields = (
        "title",
        "user__username",
        "user__email",
        "model_name",
        "system_prompt",
    )
    readonly_fields = ("id", "created_at", "updated_at", "last_message_at")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = (
        "content_preview",
        "session",
        "role",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("role", "status", "created_at")
    search_fields = (
        "content",
        "session__title",
        "session__user__username",
        "session__user__email",
    )
    readonly_fields = ("id", "created_at", "updated_at")

    @admin.display(description="Content")
    def content_preview(self, obj):
        return obj.content[:80]


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "message",
        "model_name",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "latency_ms",
        "created_at",
    )
    list_filter = ("model_name", "created_at")
    search_fields = (
        "model_name",
        "session__title",
        "session__user__username",
        "session__user__email",
        "message__content",
    )
    readonly_fields = ("id", "total_tokens", "created_at")
