from decimal import Decimal

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai.exceptions import (
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from .models import AIUsage, ChatMessage, ChatSession
from .serializers import (
    AIUsageSerializer,
    AIUsageSummarySerializer,
    ChatCompletionRequestSerializer,
    ChatCompletionResponseSerializer,
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatProviderFailureSerializer,
    ChatSessionSerializer,
)
from .services import (
    ChatProviderFailure,
    ChatResponsePersistenceError,
    process_chat_message,
)

BEARER_SECURITY = [{"Bearer": []}]
SESSION_ID_PARAMETER = openapi.Parameter(
    "id",
    openapi.IN_PATH,
    description="Chat session UUID.",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
    required=True,
)
NESTED_SESSION_ID_PARAMETER = openapi.Parameter(
    "session_id",
    openapi.IN_PATH,
    description="Chat session UUID.",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
    required=True,
)
MESSAGE_ID_PARAMETER = openapi.Parameter(
    "id",
    openapi.IN_PATH,
    description="Chat message UUID.",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
    required=True,
)


class ChatSessionListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatSessionSerializer
    queryset = ChatSession.objects.all()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ChatSession.objects.none()
        return ChatSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @swagger_auto_schema(
        operation_summary="List the authenticated user's chat sessions",
        responses={status.HTTP_200_OK: ChatSessionSerializer(many=True)},
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Create a chat session",
        request_body=ChatSessionSerializer,
        responses={
            status.HTTP_201_CREATED: ChatSessionSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ChatSessionDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatSessionSerializer
    queryset = ChatSession.objects.all()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ChatSession.objects.none()
        return ChatSession.objects.filter(user=self.request.user)

    @swagger_auto_schema(
        operation_summary="Retrieve an owned chat session",
        manual_parameters=[SESSION_ID_PARAMETER],
        responses={
            status.HTTP_200_OK: ChatSessionSerializer,
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Replace an owned chat session",
        manual_parameters=[SESSION_ID_PARAMETER],
        request_body=ChatSessionSerializer,
        responses={
            status.HTTP_200_OK: ChatSessionSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Partially update an owned chat session",
        manual_parameters=[SESSION_ID_PARAMETER],
        request_body=ChatSessionSerializer,
        responses={
            status.HTTP_200_OK: ChatSessionSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Delete an owned chat session",
        manual_parameters=[SESSION_ID_PARAMETER],
        responses={
            status.HTTP_204_NO_CONTENT: "Session deleted.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Sessions"],
    )
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)


class SessionMessageListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatMessageSerializer
    queryset = ChatMessage.objects.all()

    def get_session(self):
        if not hasattr(self, "_owned_session"):
            self._owned_session = get_object_or_404(
                ChatSession.objects.filter(user=self.request.user),
                pk=self.kwargs["session_id"],
            )
        return self._owned_session

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ChatMessage.objects.none()
        return ChatMessage.objects.filter(session=self.get_session())

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ChatMessageCreateSerializer
        return ChatMessageSerializer

    @swagger_auto_schema(
        operation_summary="List messages in an owned chat session",
        manual_parameters=[NESTED_SESSION_ID_PARAMETER],
        responses={
            status.HTTP_200_OK: ChatMessageSerializer(many=True),
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Messages"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Create a user message in an owned chat session",
        manual_parameters=[NESTED_SESSION_ID_PARAMETER],
        request_body=ChatMessageCreateSerializer,
        responses={
            status.HTTP_201_CREATED: ChatMessageSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Messages"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(
            session=self.get_session(),
            role=ChatMessage.Role.USER,
        )
        response_serializer = ChatMessageSerializer(
            message,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class ChatMessageDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatMessageSerializer
    queryset = ChatMessage.objects.all()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ChatMessage.objects.none()
        return ChatMessage.objects.filter(
            session__user=self.request.user
        ).select_related("session")

    @swagger_auto_schema(
        operation_summary="Retrieve an owned chat message",
        manual_parameters=[MESSAGE_ID_PARAMETER],
        responses={
            status.HTTP_200_OK: ChatMessageSerializer,
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Message not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Messages"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Delete an owned chat message",
        manual_parameters=[MESSAGE_ID_PARAMETER],
        responses={
            status.HTTP_204_NO_CONTENT: "Message deleted.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Message not found.",
        },
        security=BEARER_SECURITY,
        tags=["Chat Messages"],
    )
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)


class SessionChatView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatCompletionRequestSerializer

    def get_session(self):
        return get_object_or_404(
            ChatSession.objects.filter(user=self.request.user),
            pk=self.kwargs["session_id"],
        )

    @staticmethod
    def provider_failure_response(failure):
        provider_error = failure.provider_error
        if isinstance(provider_error, AIProviderTimeoutError):
            response_status = status.HTTP_504_GATEWAY_TIMEOUT
            detail = "The AI service timed out."
        elif isinstance(
            provider_error,
            (
                AIProviderAuthenticationError,
                AIProviderConfigurationError,
                AIProviderRateLimitError,
                AIProviderUnavailableError,
            ),
        ):
            response_status = status.HTTP_503_SERVICE_UNAVAILABLE
            detail = "The AI service is temporarily unavailable."
        elif isinstance(provider_error, AIProviderResponseError):
            response_status = status.HTTP_502_BAD_GATEWAY
            detail = "The AI service returned an invalid response."
        else:
            response_status = status.HTTP_502_BAD_GATEWAY
            detail = "The AI service could not complete the request."

        return Response(
            {
                "detail": detail,
                "user_message_id": failure.user_message.id,
            },
            status=response_status,
        )

    @swagger_auto_schema(
        operation_summary="Create a user message and generate an AI response",
        manual_parameters=[NESTED_SESSION_ID_PARAMETER],
        request_body=ChatCompletionRequestSerializer,
        responses={
            status.HTTP_201_CREATED: ChatCompletionResponseSerializer,
            status.HTTP_400_BAD_REQUEST: "Content validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
            status.HTTP_500_INTERNAL_SERVER_ERROR: ChatProviderFailureSerializer,
            status.HTTP_502_BAD_GATEWAY: ChatProviderFailureSerializer,
            status.HTTP_503_SERVICE_UNAVAILABLE: ChatProviderFailureSerializer,
            status.HTTP_504_GATEWAY_TIMEOUT: ChatProviderFailureSerializer,
        },
        security=BEARER_SECURITY,
        tags=["AI Chat"],
    )
    def post(self, request, *args, **kwargs):
        session = self.get_session()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = process_chat_message(
                session=session,
                user=request.user,
                content=serializer.validated_data["content"],
            )
        except ChatProviderFailure as failure:
            return self.provider_failure_response(failure)
        except ChatResponsePersistenceError as failure:
            return Response(
                {
                    "detail": "The AI response could not be saved.",
                    "user_message_id": failure.user_message.id,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_serializer = ChatCompletionResponseSerializer(
            {
                "user_message": result.user_message,
                "assistant_message": result.assistant_message,
                "usage": result.usage,
            },
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class AIUsageListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIUsageSerializer
    queryset = AIUsage.objects.all()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AIUsage.objects.none()
        return (
            AIUsage.objects.filter(session__user=self.request.user)
            .select_related("session", "message")
            .order_by("-created_at")
        )

    @swagger_auto_schema(
        operation_summary="List the authenticated user's AI usage history",
        responses={
            status.HTTP_200_OK: AIUsageSerializer(many=True),
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=BEARER_SECURITY,
        tags=["AI Usage"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AIUsageSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Summarize the authenticated user's AI usage",
        responses={
            status.HTTP_200_OK: AIUsageSummarySerializer,
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=BEARER_SECURITY,
        tags=["AI Usage"],
    )
    def get(self, request):
        aggregates = AIUsage.objects.filter(
            session__user=request.user
        ).aggregate(
            usage_records=Count("id"),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("cost"),
        )
        summary = {
            "usage_records": aggregates["usage_records"],
            "prompt_tokens": aggregates["prompt_tokens"] or 0,
            "completion_tokens": aggregates["completion_tokens"] or 0,
            "total_tokens": aggregates["total_tokens"] or 0,
            "total_cost": aggregates["total_cost"] or Decimal("0"),
        }
        return Response(AIUsageSummarySerializer(summary).data)


class SessionAIUsageListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIUsageSerializer
    queryset = AIUsage.objects.all()

    def get_session(self):
        if not hasattr(self, "_owned_session"):
            self._owned_session = get_object_or_404(
                ChatSession.objects.filter(user=self.request.user),
                pk=self.kwargs["session_id"],
            )
        return self._owned_session

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AIUsage.objects.none()
        return (
            AIUsage.objects.filter(session=self.get_session())
            .select_related("session", "message")
            .order_by("-created_at")
        )

    @swagger_auto_schema(
        operation_summary="List AI usage for an owned chat session",
        manual_parameters=[NESTED_SESSION_ID_PARAMETER],
        responses={
            status.HTTP_200_OK: AIUsageSerializer(many=True),
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
            status.HTTP_404_NOT_FOUND: "Session not found.",
        },
        security=BEARER_SECURITY,
        tags=["AI Usage"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
