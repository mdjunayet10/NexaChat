from rest_framework import serializers

from .models import AIUsage, ChatMessage, ChatSession


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = (
            "id",
            "title",
            "model_name",
            "system_prompt",
            "is_archived",
            "created_at",
            "updated_at",
            "last_message_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "last_message_at",
        )

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "The session title cannot be blank."
            )
        return value


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "session",
            "role",
            "content",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatMessageCreateSerializer(serializers.ModelSerializer):
    content = serializers.CharField(
        allow_blank=False,
        trim_whitespace=False,
    )

    class Meta:
        model = ChatMessage
        fields = ("content",)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Message content cannot contain only whitespace."
            )
        return value


class ChatCompletionRequestSerializer(ChatMessageCreateSerializer):
    pass


class AIUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIUsage
        fields = (
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
        read_only_fields = fields


class AIUsageSummarySerializer(serializers.Serializer):
    usage_records = serializers.IntegerField(read_only=True)
    prompt_tokens = serializers.IntegerField(read_only=True)
    completion_tokens = serializers.IntegerField(read_only=True)
    total_tokens = serializers.IntegerField(read_only=True)
    total_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=6,
        read_only=True,
    )


class ChatCompletionResponseSerializer(serializers.Serializer):
    user_message = ChatMessageSerializer(read_only=True)
    assistant_message = ChatMessageSerializer(read_only=True)
    usage = AIUsageSerializer(read_only=True)


class ChatProviderFailureSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)
    user_message_id = serializers.UUIDField(read_only=True)
