import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import AIUsage, ChatMessage, ChatSession
from .services import record_ai_usage


class ChatModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="chat-owner",
            password="test-password",
        )
        self.session = ChatSession.objects.create(user=self.user)

    def test_session_ownership_and_uuid_primary_key(self):
        self.assertIsInstance(self.session.id, uuid.UUID)
        self.assertEqual(self.session.title, "New Chat")
        self.assertEqual(self.session.model_name, "default")
        self.assertEqual(self.user.chat_sessions.get(), self.session)

    def test_message_ordering_and_session_last_message_timestamp(self):
        first = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="First message",
        )
        second = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.ASSISTANT,
            content="Second message",
        )

        self.assertEqual(list(self.session.messages.all()), [first, second])

        self.session.refresh_from_db()
        self.assertEqual(self.session.last_message_at, second.created_at)

    def test_role_and_status_choices_are_enforced_by_model_validation(self):
        self.assertEqual(
            set(ChatMessage.Role.values),
            {"user", "assistant", "system"},
        )
        self.assertEqual(
            set(ChatMessage.Status.values),
            {"sent", "error", "streaming"},
        )

        invalid_role = ChatMessage(
            session=self.session,
            role="invalid",
            content="Invalid role",
        )
        with self.assertRaises(ValidationError):
            invalid_role.full_clean()

        invalid_status = ChatMessage(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Invalid status",
            status="invalid",
        )
        with self.assertRaises(ValidationError):
            invalid_status.full_clean()

    def test_ai_usage_calculates_total_tokens(self):
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.ASSISTANT,
            content="An answer",
        )

        usage = AIUsage.objects.create(
            session=self.session,
            message=message,
            model_name="test-model",
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=999,
        )

        self.assertEqual(usage.total_tokens, 20)
        usage.refresh_from_db()
        self.assertEqual(usage.total_tokens, 20)

    def test_ai_usage_rejects_a_message_from_another_session(self):
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.ASSISTANT,
            content="An answer",
        )
        other_session = ChatSession.objects.create(
            user=self.user,
            title="Another chat",
        )

        with self.assertRaises(ValidationError):
            AIUsage.objects.create(
                session=other_session,
                message=message,
                model_name="test-model",
            )

        self.assertFalse(AIUsage.objects.exists())

    def test_user_deletion_cascades_chat_data(self):
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.ASSISTANT,
            content="An answer",
        )
        AIUsage.objects.create(
            session=self.session,
            message=message,
            model_name="test-model",
        )

        self.user.delete()

        self.assertFalse(ChatSession.objects.exists())
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(AIUsage.objects.exists())


class ProjectHealthTests(SimpleTestCase):
    def test_health_endpoint(self):
        response = self.client.get(reverse("health-check"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["phase"], 1)


class AuthenticatedChatAPIMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username=f"{cls.__name__.lower()}-owner",
            password="OwnerPass123!",
        )
        cls.other_user = get_user_model().objects.create_user(
            username=f"{cls.__name__.lower()}-other",
            password="OtherPass123!",
        )

    def setUp(self):
        super().setUp()
        self.authenticate_as(self.user)

    def authenticate_as(self, user):
        access = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


class ChatSessionListCreateAPITests(
    AuthenticatedChatAPIMixin,
    APITestCase,
):
    def setUp(self):
        super().setUp()
        self.url = reverse("chats:session-list-create")

    def test_session_list_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_session_create_requires_authentication(self):
        self.client.credentials()

        response = self.client.post(
            self.url,
            {"title": "Private chat"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_create_session(self):
        response = self.client.post(
            self.url,
            {
                "title": "Django Learning",
                "model_name": "default",
                "system_prompt": "Answer clearly.",
                "is_archived": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Django Learning")
        self.assertEqual(response.data["system_prompt"], "Answer clearly.")
        self.assertTrue(ChatSession.objects.filter(pk=response.data["id"]).exists())

    def test_created_session_belongs_to_request_user(self):
        response = self.client.post(
            self.url,
            {"title": "Owned session"},
            format="json",
        )

        session = ChatSession.objects.get(pk=response.data["id"])
        self.assertEqual(session.user, self.user)

    def test_client_cannot_assign_session_to_another_user(self):
        response = self.client.post(
            self.url,
            {
                "title": "Still mine",
                "user": self.other_user.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session = ChatSession.objects.get(pk=response.data["id"])
        self.assertEqual(session.user, self.user)
        self.assertNotIn("user", response.data)

    def test_user_sees_only_their_sessions(self):
        owned = ChatSession.objects.create(
            user=self.user,
            title="Owned",
        )
        ChatSession.objects.create(
            user=self.other_user,
            title="Not owned",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in response.data},
            {str(owned.id)},
        )

    def test_empty_session_list_returns_empty_list(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_blank_and_whitespace_session_titles_are_rejected(self):
        for title in ("", "   \n\t"):
            with self.subTest(title=repr(title)):
                response = self.client.post(
                    self.url,
                    {"title": title},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertIn("title", response.data)

    def test_oversized_session_title_is_rejected(self):
        response = self.client.post(
            self.url,
            {"title": "x" * 256},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", response.data)

    def test_omitted_session_fields_use_model_defaults(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "New Chat")
        self.assertEqual(response.data["model_name"], "default")
        self.assertFalse(response.data["is_archived"])

    def test_session_list_preserves_model_activity_ordering(self):
        older_active = ChatSession.objects.create(
            user=self.user,
            title="Recently active",
        )
        newer_empty = ChatSession.objects.create(
            user=self.user,
            title="No messages",
        )
        ChatMessage.objects.create(
            session=older_active,
            role=ChatMessage.Role.USER,
            content="Activity",
        )

        response = self.client.get(self.url)

        self.assertEqual(
            [item["id"] for item in response.data],
            [str(older_active.id), str(newer_empty.id)],
        )

    def test_one_create_request_creates_one_session(self):
        response = self.client.post(
            self.url,
            {"title": "One request"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)


class ChatSessionDetailAPITests(
    AuthenticatedChatAPIMixin,
    APITestCase,
):
    def setUp(self):
        super().setUp()
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Owned session",
        )
        self.other_session = ChatSession.objects.create(
            user=self.other_user,
            title="Other session",
        )
        self.url = reverse(
            "chats:session-detail",
            kwargs={"pk": self.session.id},
        )

    def test_owner_can_retrieve_session(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.session.id))
        self.assertEqual(response.data["title"], self.session.title)

    def test_another_user_receives_not_found_for_session(self):
        self.authenticate_as(self.other_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_patch_session(self):
        response = self.client.patch(
            self.url,
            {"title": "Patched title", "is_archived": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "Patched title")
        self.assertTrue(self.session.is_archived)

    def test_owner_can_put_session(self):
        response = self.client.put(
            self.url,
            {
                "title": "Replacement title",
                "model_name": "replacement-model",
                "system_prompt": "Replacement prompt",
                "is_archived": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "Replacement title")
        self.assertEqual(self.session.model_name, "replacement-model")
        self.assertEqual(self.session.system_prompt, "Replacement prompt")
        self.assertTrue(self.session.is_archived)

    def test_another_user_cannot_update_session(self):
        self.authenticate_as(self.other_user)

        patch_response = self.client.patch(
            self.url,
            {"title": "Stolen"},
            format="json",
        )
        put_response = self.client.put(
            self.url,
            {
                "title": "Stolen",
                "model_name": "default",
                "system_prompt": "",
                "is_archived": False,
            },
            format="json",
        )

        self.assertEqual(patch_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(put_response.status_code, status.HTTP_404_NOT_FOUND)
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "Owned session")

    def test_owner_can_delete_session(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatSession.objects.filter(pk=self.session.id).exists())

    def test_another_user_cannot_delete_session(self):
        self.authenticate_as(self.other_user)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(ChatSession.objects.filter(pk=self.session.id).exists())

    def test_deleting_session_cascades_to_messages_and_usage(self):
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Cascade me",
        )
        usage = AIUsage.objects.create(
            session=self.session,
            message=message,
            model_name="test-model",
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatMessage.objects.filter(pk=message.id).exists())
        self.assertFalse(AIUsage.objects.filter(pk=usage.id).exists())

    def test_missing_session_uuid_returns_not_found(self):
        missing_url = reverse(
            "chats:session-detail",
            kwargs={"pk": uuid.uuid4()},
        )

        response = self.client.get(missing_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_session_owner_and_id_are_not_writable(self):
        original_id = self.session.id

        response = self.client.patch(
            self.url,
            {
                "id": self.other_session.id,
                "user": self.other_user.id,
                "title": "Safe update",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.id, original_id)
        self.assertEqual(self.session.user, self.user)
        self.assertEqual(self.session.title, "Safe update")
        self.assertNotIn("user", response.data)

    def test_invalid_session_update_returns_structured_error(self):
        response = self.client.patch(
            self.url,
            {"title": " "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", response.data)


class SessionMessageListCreateAPITests(
    AuthenticatedChatAPIMixin,
    APITestCase,
):
    def setUp(self):
        super().setUp()
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Owned message session",
        )
        self.other_session = ChatSession.objects.create(
            user=self.other_user,
            title="Other message session",
        )
        self.url = reverse(
            "chats:session-message-list-create",
            kwargs={"session_id": self.session.id},
        )
        self.other_url = reverse(
            "chats:session-message-list-create",
            kwargs={"session_id": self.other_session.id},
        )

    def test_message_list_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_message_create_requires_authentication(self):
        self.client.credentials()

        response = self.client.post(
            self.url,
            {"content": "Private message"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_owner_can_create_user_message(self):
        response = self.client.post(
            self.url,
            {"content": "What is Django REST Framework?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["content"],
            "What is Django REST Framework?",
        )
        self.assertTrue(ChatMessage.objects.filter(pk=response.data["id"]).exists())

    def test_created_message_belongs_to_url_session(self):
        response = self.client.post(
            self.url,
            {"content": "URL-owned message"},
            format="json",
        )

        message = ChatMessage.objects.get(pk=response.data["id"])
        self.assertEqual(message.session, self.session)
        self.assertEqual(response.data["session"], self.session.id)

    def test_created_message_receives_user_role(self):
        response = self.client.post(
            self.url,
            {"content": "Human message"},
            format="json",
        )

        self.assertEqual(response.data["role"], ChatMessage.Role.USER)
        message = ChatMessage.objects.get(pk=response.data["id"])
        self.assertEqual(message.role, ChatMessage.Role.USER)

    def test_client_cannot_create_assistant_or_system_messages(self):
        for requested_role in (
            ChatMessage.Role.ASSISTANT,
            ChatMessage.Role.SYSTEM,
        ):
            with self.subTest(role=requested_role):
                response = self.client.post(
                    self.url,
                    {
                        "content": f"Requested {requested_role}",
                        "role": requested_role,
                    },
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                )
                self.assertEqual(
                    response.data["role"],
                    ChatMessage.Role.USER,
                )

    def test_client_cannot_override_message_session(self):
        response = self.client.post(
            self.url,
            {
                "content": "Still attached to URL session",
                "session": self.other_session.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        message = ChatMessage.objects.get(pk=response.data["id"])
        self.assertEqual(message.session, self.session)
        self.assertNotEqual(message.session, self.other_session)

    def test_missing_message_content_is_rejected(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("content", response.data)

    def test_empty_message_content_is_rejected(self):
        response = self.client.post(
            self.url,
            {"content": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("content", response.data)

    def test_whitespace_only_message_content_is_rejected(self):
        response = self.client.post(
            self.url,
            {"content": "  \n\t  "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("content", response.data)

    def test_message_creation_preserves_meaningful_line_breaks(self):
        content = "First line\n\nSecond line\n"

        response = self.client.post(
            self.url,
            {"content": content},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["content"], content)
        message = ChatMessage.objects.get(pk=response.data["id"])
        self.assertEqual(message.content, content)

    def test_owner_can_list_messages_in_session(self):
        first = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="First",
        )
        second = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.ASSISTANT,
            content="Second",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in response.data},
            {str(first.id), str(second.id)},
        )

    def test_messages_are_listed_in_chronological_order(self):
        first = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="First",
        )
        second = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Second",
        )

        response = self.client.get(self.url)

        self.assertEqual(
            [item["id"] for item in response.data],
            [str(first.id), str(second.id)],
        )

    def test_another_user_receives_not_found_for_nested_message_endpoint(self):
        self.authenticate_as(self.other_user)

        list_response = self.client.get(self.url)
        create_response = self.client.post(
            self.url,
            {"content": "Not allowed"},
            format="json",
        )

        self.assertEqual(list_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(create_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ChatMessage.objects.filter(session=self.session).exists())

    def test_user_cannot_see_messages_from_another_session(self):
        owned_message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Owned",
        )
        ChatMessage.objects.create(
            session=self.other_session,
            role=ChatMessage.Role.USER,
            content="Private to another user",
        )

        response = self.client.get(self.url)

        self.assertEqual(
            [item["id"] for item in response.data],
            [str(owned_message.id)],
        )

    def test_empty_message_list_returns_empty_list(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_api_message_creation_updates_session_activity_timestamp(self):
        response = self.client.post(
            self.url,
            {"content": "Update activity"},
            format="json",
        )

        message = ChatMessage.objects.get(pk=response.data["id"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.last_message_at, message.created_at)

    def test_api_message_creation_does_not_create_ai_usage(self):
        response = self.client.post(
            self.url,
            {"content": "No AI call"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(AIUsage.objects.exists())

    def test_one_create_request_creates_one_message(self):
        response = self.client.post(
            self.url,
            {"content": "Create once"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChatMessage.objects.filter(session=self.session).count(), 1)

    def test_message_status_and_metadata_are_not_client_controlled(self):
        response = self.client.post(
            self.url,
            {
                "content": "Safe fields",
                "status": ChatMessage.Status.ERROR,
                "metadata": {"private": "client value"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        message = ChatMessage.objects.get(pk=response.data["id"])
        self.assertEqual(message.status, ChatMessage.Status.SENT)
        self.assertEqual(message.metadata, {})
        self.assertNotIn("metadata", response.data)

    def test_body_session_identifier_cannot_bypass_url_ownership(self):
        response = self.client.post(
            self.other_url,
            {
                "content": "Attempted bypass",
                "session": self.session.id,
                "user": self.user.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            ChatMessage.objects.filter(content="Attempted bypass").exists()
        )


class ChatMessageDetailAPITests(
    AuthenticatedChatAPIMixin,
    APITestCase,
):
    def setUp(self):
        super().setUp()
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Owned detail session",
        )
        self.other_session = ChatSession.objects.create(
            user=self.other_user,
            title="Other detail session",
        )
        self.message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Owned message",
        )
        self.other_message = ChatMessage.objects.create(
            session=self.other_session,
            role=ChatMessage.Role.USER,
            content="Other message",
        )
        self.url = reverse(
            "chats:message-detail",
            kwargs={"pk": self.message.id},
        )

    def test_message_detail_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_owner_can_retrieve_message(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.message.id))
        self.assertEqual(response.data["session"], self.session.id)

    def test_another_user_receives_not_found_for_message(self):
        self.authenticate_as(self.other_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_delete_message(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatMessage.objects.filter(pk=self.message.id).exists())

    def test_another_user_cannot_delete_message(self):
        self.authenticate_as(self.other_user)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(ChatMessage.objects.filter(pk=self.message.id).exists())

    def test_missing_message_uuid_returns_not_found(self):
        missing_url = reverse(
            "chats:message-detail",
            kwargs={"pk": uuid.uuid4()},
        )

        response = self.client.get(missing_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_deleting_message_cascades_to_related_ai_usage(self):
        usage = AIUsage.objects.create(
            session=self.session,
            message=self.message,
            model_name="test-model",
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AIUsage.objects.filter(pk=usage.id).exists())

    def test_message_editing_methods_are_not_available(self):
        put_response = self.client.put(
            self.url,
            {"content": "Edited"},
            format="json",
        )
        patch_response = self.client.patch(
            self.url,
            {"content": "Edited"},
            format="json",
        )

        self.assertEqual(
            put_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            patch_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, "Owned message")


class ChatAPIRoutingAndSchemaTests(SimpleTestCase):
    def test_phase_four_uuid_routes_are_named(self):
        session_id = uuid.uuid4()
        message_id = uuid.uuid4()

        self.assertEqual(
            reverse("chats:session-list-create"),
            "/api/chats/sessions/",
        )
        self.assertEqual(
            reverse("chats:session-detail", kwargs={"pk": session_id}),
            f"/api/chats/sessions/{session_id}/",
        )
        self.assertEqual(
            reverse(
                "chats:session-message-list-create",
                kwargs={"session_id": session_id},
            ),
            f"/api/chats/sessions/{session_id}/messages/",
        )
        self.assertEqual(
            reverse("chats:message-detail", kwargs={"pk": message_id}),
            f"/api/chats/messages/{message_id}/",
        )

    def test_openapi_schema_contains_phase_four_operations_and_uuid_parameters(self):
        response = self.client.get(
            reverse("schema-json", kwargs={"format": ".json"})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schema = json.loads(response.content)
        paths = schema["paths"]

        list_path = paths["/chats/sessions/"]
        session_path = paths["/chats/sessions/{id}/"]
        nested_path = paths["/chats/sessions/{session_id}/messages/"]
        message_path = paths["/chats/messages/{id}/"]

        self.assertEqual({"get", "post"}, set(list_path) - {"parameters"})
        self.assertEqual(
            {"get", "put", "patch", "delete"},
            set(session_path) - {"parameters"},
        )
        self.assertEqual({"get", "post"}, set(nested_path) - {"parameters"})
        self.assertEqual(
            {"get", "delete"},
            set(message_path) - {"parameters"},
        )

        operations_with_uuid_parameters = (
            session_path["get"],
            session_path["put"],
            session_path["patch"],
            session_path["delete"],
            nested_path["get"],
            nested_path["post"],
            message_path["get"],
            message_path["delete"],
        )
        for operation in operations_with_uuid_parameters:
            path_parameters = [
                parameter
                for parameter in operation.get("parameters", [])
                if parameter["in"] == "path"
            ]
            self.assertEqual(len(path_parameters), 1)
            parameter = path_parameters[0]
            self.assertEqual(parameter["type"], "string")
            self.assertEqual(parameter["format"], "uuid")

        for operation in (
            list_path["get"],
            list_path["post"],
            session_path["get"],
            nested_path["get"],
            nested_path["post"],
            message_path["get"],
            message_path["delete"],
        ):
            self.assertEqual(operation["security"], [{"Bearer": []}])


class AIUsageServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="usage-service-user",
            password="UsageServicePass123!",
        )
        cls.session = ChatSession.objects.create(
            user=cls.user,
            title="Usage service session",
        )
        cls.message = ChatMessage.objects.create(
            session=cls.session,
            role=ChatMessage.Role.USER,
            content="Trigger usage",
        )

    def record(self, **overrides):
        values = {
            "session": self.session,
            "message": self.message,
            "model_name": "internal-test-model",
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "cost": Decimal("0.123456"),
            "latency_ms": 250,
        }
        values.update(overrides)
        return record_ai_usage(**values)

    def test_valid_usage_is_recorded_with_correct_relationships(self):
        usage = self.record()

        self.assertEqual(usage.session, self.session)
        self.assertEqual(usage.message, self.message)
        self.assertEqual(usage.total_tokens, 20)
        self.assertEqual(AIUsage.objects.count(), 1)

    def test_negative_token_counts_are_rejected(self):
        for field_name in ("prompt_tokens", "completion_tokens"):
            with self.subTest(field=field_name):
                with self.assertRaises(ValidationError):
                    self.record(**{field_name: -1})

        self.assertFalse(AIUsage.objects.exists())

    def test_negative_and_non_finite_costs_are_rejected(self):
        for invalid_cost in (Decimal("-0.000001"), Decimal("NaN")):
            with self.subTest(cost=invalid_cost):
                with self.assertRaises(ValidationError):
                    self.record(cost=invalid_cost)

        self.assertFalse(AIUsage.objects.exists())

    def test_floating_point_cost_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.record(cost=0.1)

        self.assertFalse(AIUsage.objects.exists())

    def test_message_from_another_session_is_rejected(self):
        other_session = ChatSession.objects.create(
            user=self.user,
            title="Another service session",
        )

        with self.assertRaises(ValidationError):
            self.record(session=other_session)

        self.assertFalse(AIUsage.objects.exists())

    def test_non_user_trigger_roles_are_rejected(self):
        for role in (ChatMessage.Role.ASSISTANT, ChatMessage.Role.SYSTEM):
            with self.subTest(role=role):
                message = ChatMessage.objects.create(
                    session=self.session,
                    role=role,
                    content=f"{role} message",
                )
                with self.assertRaises(ValidationError):
                    self.record(message=message)

        self.assertFalse(AIUsage.objects.exists())

    def test_recording_usage_does_not_create_or_change_messages(self):
        original_content = self.message.content
        original_message_count = ChatMessage.objects.count()

        self.record()

        self.message.refresh_from_db()
        self.assertEqual(self.message.content, original_content)
        self.assertEqual(ChatMessage.objects.count(), original_message_count)
        self.assertFalse(
            ChatMessage.objects.filter(role=ChatMessage.Role.ASSISTANT).exists()
        )

    def test_decimal_cost_precision_is_preserved(self):
        usage = self.record(cost=Decimal("123.456789"))
        usage.refresh_from_db()

        self.assertEqual(usage.cost, Decimal("123.456789"))


class AIUsageAPITests(
    AuthenticatedChatAPIMixin,
    APITestCase,
):
    def setUp(self):
        super().setUp()
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Usage API session",
        )
        self.message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.Role.USER,
            content="Usage API trigger",
        )
        self.other_session = ChatSession.objects.create(
            user=self.other_user,
            title="Other usage API session",
        )
        self.other_message = ChatMessage.objects.create(
            session=self.other_session,
            role=ChatMessage.Role.USER,
            content="Other usage trigger",
        )
        self.list_url = reverse("chats:usage-list")
        self.summary_url = reverse("chats:usage-summary")
        self.session_url = reverse(
            "chats:session-usage-list",
            kwargs={"session_id": self.session.id},
        )

    def record_owned(self, **overrides):
        values = {
            "session": self.session,
            "message": self.message,
            "model_name": "owned-model",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost": Decimal("0.100001"),
        }
        values.update(overrides)
        return record_ai_usage(**values)

    def record_other(self, **overrides):
        values = {
            "session": self.other_session,
            "message": self.other_message,
            "model_name": "other-model",
            "prompt_tokens": 99,
            "completion_tokens": 1,
            "cost": Decimal("9.999999"),
        }
        values.update(overrides)
        return record_ai_usage(**values)

    def test_usage_list_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_usage(self):
        usage = self.record_owned()

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], usage.id)
        self.assertEqual(response.data[0]["session"], self.session.id)
        self.assertEqual(response.data[0]["message"], self.message.id)
        self.assertEqual(response.data[0]["cost"], "0.100001")

    def test_usage_list_excludes_another_users_records_and_identifiers(self):
        owned_usage = self.record_owned()
        other_usage = self.record_other()

        response = self.client.get(self.list_url)

        self.assertEqual(
            [record["id"] for record in response.data],
            [owned_usage.id],
        )
        self.assertNotIn(
            other_usage.id,
            [record["id"] for record in response.data],
        )
        self.assertNotIn(
            self.other_session.id,
            [record["session"] for record in response.data],
        )
        self.assertNotIn(
            self.other_message.id,
            [record["message"] for record in response.data],
        )

    def test_empty_usage_list_returns_empty_list(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_session_usage_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.session_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_owner_can_list_usage_for_session(self):
        usage = self.record_owned()

        response = self.client.get(self.session_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([record["id"] for record in response.data], [usage.id])

    def test_another_user_receives_not_found_for_session_usage(self):
        self.authenticate_as(self.other_user)

        response = self.client.get(self.session_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_session_usage_returns_not_found(self):
        missing_url = reverse(
            "chats:session-usage-list",
            kwargs={"session_id": uuid.uuid4()},
        )

        response = self.client.get(missing_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_usage_endpoints_are_read_only_and_have_no_public_create(self):
        list_post = self.client.post(self.list_url, {}, format="json")
        summary_post = self.client.post(self.summary_url, {}, format="json")
        session_post = self.client.post(self.session_url, {}, format="json")

        self.assertEqual(
            list_post.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            summary_post.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            session_post.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_summary_requires_authentication(self):
        self.client.credentials()

        response = self.client.get(self.summary_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_summary_returns_zero_values(self):
        response = self.client.get(self.summary_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "usage_records": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "total_cost": "0.000000",
            },
        )

    def test_summary_uses_database_totals_for_owned_usage_only(self):
        self.record_owned(
            prompt_tokens=10,
            completion_tokens=5,
            cost=Decimal("0.100001"),
        )
        self.record_owned(
            prompt_tokens=7,
            completion_tokens=3,
            cost=Decimal("0.200002"),
        )
        self.record_other()

        response = self.client.get(self.summary_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["usage_records"], 2)
        self.assertEqual(response.data["prompt_tokens"], 17)
        self.assertEqual(response.data["completion_tokens"], 8)
        self.assertEqual(response.data["total_tokens"], 25)
        self.assertEqual(response.data["total_cost"], "0.300003")


class PhaseFiveUsageRoutingAndSchemaTests(SimpleTestCase):
    def test_usage_routes_are_named_with_uuid_session_path(self):
        session_id = uuid.uuid4()

        self.assertEqual(reverse("chats:usage-list"), "/api/chats/usage/")
        self.assertEqual(
            reverse("chats:usage-summary"),
            "/api/chats/usage/summary/",
        )
        self.assertEqual(
            reverse(
                "chats:session-usage-list",
                kwargs={"session_id": session_id},
            ),
            f"/api/chats/sessions/{session_id}/usage/",
        )

    def test_openapi_schema_documents_read_only_usage_operations(self):
        response = self.client.get(
            reverse("schema-json", kwargs={"format": ".json"})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schema = json.loads(response.content)
        paths = schema["paths"]
        list_path = paths["/chats/usage/"]
        summary_path = paths["/chats/usage/summary/"]
        session_path = paths["/chats/sessions/{session_id}/usage/"]

        self.assertEqual(set(list_path) - {"parameters"}, {"get"})
        self.assertEqual(set(summary_path) - {"parameters"}, {"get"})
        self.assertEqual(set(session_path) - {"parameters"}, {"get"})

        for operation in (
            list_path["get"],
            summary_path["get"],
            session_path["get"],
        ):
            self.assertEqual(operation["security"], [{"Bearer": []}])

        path_parameter = [
            parameter
            for parameter in session_path["get"]["parameters"]
            if parameter["in"] == "path"
        ][0]
        self.assertEqual(path_parameter["type"], "string")
        self.assertEqual(path_parameter["format"], "uuid")
