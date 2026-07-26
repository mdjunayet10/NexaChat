import json
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from chats.models import ChatMessage, ChatSession

from .models import PasswordResetToken, UserProfile
from .services import (
    INVALID_RESET_TOKEN_MESSAGE,
    InvalidResetTokenError,
    create_password_reset_token,
    hash_reset_token,
    reset_user_password,
)


class UserProfileTests(TestCase):
    def test_profile_is_created_once_with_expected_defaults(self):
        user = get_user_model().objects.create_user(
            username="profile-owner",
            password="test-password",
        )

        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)
        self.assertEqual(user.profile.theme, UserProfile.Theme.SYSTEM)
        self.assertEqual(user.profile.language, "en")
        self.assertTrue(user.profile.email_notifications)

        user.save()

        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)


class PasswordResetTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reset-owner",
            password="test-password",
        )

    def test_validity_before_and_after_expiry_and_use(self):
        token = PasswordResetToken.objects.create(
            user=self.user,
            token_hash="hashed-reset-token",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.assertFalse(token.is_expired)
        self.assertFalse(token.is_used)
        self.assertTrue(token.is_valid)

        token.expires_at = timezone.now() - timedelta(seconds=1)

        self.assertTrue(token.is_expired)
        self.assertFalse(token.is_valid)

        token.expires_at = timezone.now() + timedelta(hours=1)
        token.used_at = timezone.now()

        self.assertTrue(token.is_used)
        self.assertFalse(token.is_valid)

    def test_user_deletion_cascades_profile_and_reset_tokens(self):
        PasswordResetToken.objects.create(
            user=self.user,
            token_hash="another-hashed-reset-token",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.user.delete()

        self.assertFalse(UserProfile.objects.exists())
        self.assertFalse(PasswordResetToken.objects.exists())


class AccountsRoutingTests(SimpleTestCase):
    def test_accounts_namespace_is_registered(self):
        self.assertEqual(reverse("health-check"), "/api/health/")

    def test_phase_three_routes_are_named_and_mounted_once(self):
        self.assertEqual(reverse("accounts:register"), "/api/accounts/register/")
        self.assertEqual(reverse("accounts:login"), "/api/accounts/login/")
        self.assertEqual(
            reverse("accounts:token_refresh"),
            "/api/accounts/token/refresh/",
        )
        self.assertEqual(reverse("accounts:logout"), "/api/accounts/logout/")
        self.assertEqual(reverse("accounts:profile"), "/api/accounts/profile/")


class RegistrationAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("accounts:register")

    def registration_payload(self, **overrides):
        payload = {
            "username": "junayet",
            "email": "junayet@example.com",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
            "first_name": "Md",
            "last_name": "Junayet",
        }
        payload.update(overrides)
        return payload

    def test_successful_registration_returns_created(self):
        response = self.client.post(
            self.url,
            self.registration_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["message"], "Registration successful.")
        self.assertNotIn("password", response.data)
        self.assertNotIn("password", response.data["user"])
        self.assertNotIn("password_confirm", response.data["user"])

    def test_registration_creates_user_and_normalizes_email(self):
        response = self.client.post(
            self.url,
            self.registration_payload(email="junayet@EXAMPLE.COM"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username="junayet")
        self.assertEqual(user.email, "junayet@example.com")
        self.assertEqual(user.first_name, "Md")
        self.assertEqual(user.last_name, "Junayet")

    def test_registration_hashes_password(self):
        raw_password = "StrongPass123!"

        response = self.client.post(
            self.url,
            self.registration_payload(password=raw_password),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username="junayet")
        self.assertNotEqual(user.password, raw_password)
        self.assertTrue(user.check_password(raw_password))

    def test_registration_signal_creates_exactly_one_profile(self):
        response = self.client.post(
            self.url,
            self.registration_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username="junayet")
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)

    def test_registration_returns_access_and_refresh_tokens(self):
        response = self.client.post(
            self.url,
            self.registration_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])

    def test_duplicate_username_is_rejected(self):
        get_user_model().objects.create_user(
            username="junayet",
            email="existing@example.com",
            password="ExistingPass123!",
        )

        response = self.client.post(
            self.url,
            self.registration_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        get_user_model().objects.create_user(
            username="existing",
            email="Junayet@Example.com",
            password="ExistingPass123!",
        )

        response = self.client.post(
            self.url,
            self.registration_payload(email="junayet@example.COM"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_password_mismatch_is_rejected(self):
        response = self.client.post(
            self.url,
            self.registration_payload(password_confirm="DifferentPass123!"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirm", response.data)

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            self.url,
            self.registration_payload(
                password="short",
                password_confirm="short",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)


class LoginAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("accounts:login")
        self.password = "LoginPass123!"
        self.user = get_user_model().objects.create_user(
            username="login-user",
            email="login@example.com",
            password=self.password,
            first_name="Login",
        )

    def test_valid_login_returns_user_and_tokens(self):
        response = self.client.post(
            self.url,
            {"username": self.user.username, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Login successful.")
        self.assertEqual(response.data["user"]["id"], self.user.id)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])
        self.assertNotIn("password", response.data["user"])

    def test_invalid_password_returns_safe_unauthorized_response(self):
        response = self.client.post(
            self.url,
            {"username": self.user.username, "password": "WrongPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            response.data["detail"],
            "Invalid username or password.",
        )

    def test_unknown_username_returns_safe_unauthorized_response(self):
        response = self.client.post(
            self.url,
            {"username": "unknown-user", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            response.data["detail"],
            "Invalid username or password.",
        )

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.url,
            {"username": self.user.username, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            response.data["detail"],
            "Invalid username or password.",
        )


class TokenRefreshAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("accounts:token_refresh")
        self.user = get_user_model().objects.create_user(
            username="refresh-user",
            password="RefreshPass123!",
        )

    def test_valid_refresh_returns_new_access_token(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            self.url,
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])
        self.assertNotEqual(response.data["refresh"], str(refresh))

    def test_invalid_refresh_is_rejected(self):
        response = self.client.post(
            self.url,
            {"refresh": "not-a-valid-refresh-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("accounts:logout")
        self.refresh_url = reverse("accounts:token_refresh")
        self.user = get_user_model().objects.create_user(
            username="logout-user",
            password="LogoutPass123!",
        )
        self.refresh = RefreshToken.for_user(self.user)
        self.access = str(self.refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")

    def test_authenticated_logout_blacklists_refresh_token(self):
        response = self.client.post(
            self.url,
            {"refresh": str(self.refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Logout successful.")
        self.assertTrue(
            BlacklistedToken.objects.filter(
                token__jti=self.refresh["jti"]
            ).exists()
        )

    def test_blacklisted_refresh_token_cannot_be_reused(self):
        logout_response = self.client.post(
            self.url,
            {"refresh": str(self.refresh)},
            format="json",
        )
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            self.refresh_url,
            {"refresh": str(self.refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_without_authentication_is_rejected(self):
        self.client.credentials()

        response = self.client.post(
            self.url,
            {"refresh": str(self.refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_without_refresh_token_is_rejected(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.data)

    def test_logout_with_malformed_refresh_token_is_rejected(self):
        response = self.client.post(
            self.url,
            {"refresh": "not-a-valid-refresh-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.data)

    def test_logout_with_expired_refresh_token_is_rejected(self):
        self.refresh.set_exp(lifetime=timedelta(seconds=-1))

        response = self.client.post(
            self.url,
            {"refresh": str(self.refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.data)

    def test_logout_with_already_blacklisted_token_is_rejected(self):
        self.refresh.blacklist()

        response = self.client.post(
            self.url,
            {"refresh": str(self.refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.data)

    def test_logout_rejects_another_users_refresh_token(self):
        other_user = get_user_model().objects.create_user(
            username="another-logout-user",
            password="AnotherPass123!",
        )
        other_refresh = RefreshToken.for_user(other_user)

        response = self.client.post(
            self.url,
            {"refresh": str(other_refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.data)
        self.assertFalse(
            BlacklistedToken.objects.filter(
                token__jti=other_refresh["jti"]
            ).exists()
        )


class ProfileAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("accounts:profile")
        self.password = "ProfilePass123!"
        self.user = get_user_model().objects.create_user(
            username="profile-api-user",
            email="profile@example.com",
            password=self.password,
            first_name="Profile",
            last_name="Owner",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-profile-user",
            email="other@example.com",
            password="OtherPass123!",
        )
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
        )

    def test_authenticated_user_can_retrieve_own_profile(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.user.id)
        self.assertEqual(response.data["username"], self.user.username)
        self.assertEqual(response.data["email"], self.user.email)
        self.assertEqual(response.data["profile"]["id"], self.user.profile.id)
        self.assertNotIn("password", response.data)

    def test_all_profile_methods_require_authentication(self):
        self.client.credentials()

        get_response = self.client.get(self.url)
        put_response = self.client.put(self.url, {}, format="json")
        patch_response = self.client.patch(self.url, {}, format="json")

        self.assertEqual(get_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(put_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(patch_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_patch_user_and_profile_fields(self):
        response = self.client.patch(
            self.url,
            {
                "email": "updated@EXAMPLE.COM",
                "first_name": "Updated",
                "profile": {
                    "bio": "Updated bio",
                    "phone": "+8801700000000",
                    "theme": UserProfile.Theme.DARK,
                    "language": "bn",
                    "email_notifications": False,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.email, "updated@example.com")
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.profile.bio, "Updated bio")
        self.assertEqual(self.user.profile.phone, "+8801700000000")
        self.assertEqual(self.user.profile.theme, UserProfile.Theme.DARK)
        self.assertEqual(self.user.profile.language, "bn")
        self.assertFalse(self.user.profile.email_notifications)

    def test_authenticated_user_can_put_user_and_profile_fields(self):
        response = self.client.put(
            self.url,
            {
                "email": "replacement@example.com",
                "first_name": "Replacement",
                "last_name": "Name",
                "profile": {
                    "bio": "Replacement bio",
                    "phone": "0123456789",
                    "theme": UserProfile.Theme.LIGHT,
                    "language": "en",
                    "email_notifications": True,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.email, "replacement@example.com")
        self.assertEqual(self.user.first_name, "Replacement")
        self.assertEqual(self.user.last_name, "Name")
        self.assertEqual(self.user.profile.bio, "Replacement bio")
        self.assertEqual(self.user.profile.theme, UserProfile.Theme.LIGHT)

    def test_profile_update_cannot_edit_password(self):
        original_password_hash = self.user.password

        response = self.client.patch(
            self.url,
            {"password": "ReplacementPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original_password_hash)
        self.assertTrue(self.user.check_password(self.password))

    def test_profile_update_rejects_duplicate_email(self):
        response = self.client.patch(
            self.url,
            {"email": "OTHER@EXAMPLE.COM"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "profile@example.com")

    def test_profile_route_cannot_retrieve_or_modify_another_user(self):
        other_profile_id = self.other_user.profile.id

        get_response = self.client.get(self.url)
        self.assertEqual(get_response.data["id"], self.user.id)
        self.assertNotEqual(get_response.data["id"], self.other_user.id)

        patch_response = self.client.patch(
            self.url,
            {
                "id": self.other_user.id,
                "username": self.other_user.username,
                "profile": {
                    "id": other_profile_id,
                    "bio": "Only my profile changes",
                },
            },
            format="json",
        )

        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["id"], self.user.id)
        self.assertEqual(patch_response.data["username"], self.user.username)
        self.user.profile.refresh_from_db()
        self.other_user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.bio, "Only my profile changes")
        self.assertEqual(self.other_user.profile.bio, "")

    def test_profile_updates_do_not_create_duplicate_profiles(self):
        self.assertEqual(UserProfile.objects.filter(user=self.user).count(), 1)

        first_response = self.client.patch(
            self.url,
            {"profile": {"bio": "First update"}},
            format="json",
        )
        second_response = self.client.patch(
            self.url,
            {"profile": {"bio": "Second update"}},
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(UserProfile.objects.filter(user=self.user).count(), 1)

    def test_missing_legacy_profile_is_recreated_defensively(self):
        self.user.profile.delete()
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(UserProfile.objects.filter(user=self.user).count(), 1)


class PhaseThreeSchemaTests(SimpleTestCase):
    def test_openapi_schema_contains_all_phase_three_operations(self):
        response = self.client.get(
            reverse("schema-json", kwargs={"format": ".json"})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schema = json.loads(response.content)
        self.assertEqual(schema["basePath"], "/api")

        paths = schema["paths"]
        self.assertIn("/accounts/register/", paths)
        self.assertIn("/accounts/login/", paths)
        self.assertIn("/accounts/token/refresh/", paths)
        self.assertIn("/accounts/logout/", paths)
        self.assertIn("/accounts/profile/", paths)
        self.assertIn("get", paths["/accounts/profile/"])
        self.assertIn("put", paths["/accounts/profile/"])
        self.assertIn("patch", paths["/accounts/profile/"])
        self.assertEqual(paths["/accounts/register/"]["post"]["security"], [])
        self.assertEqual(paths["/accounts/login/"]["post"]["security"], [])
        self.assertEqual(
            paths["/accounts/logout/"]["post"]["security"],
            [{"Bearer": []}],
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_RESET_TOKEN_TTL_MINUTES=30,
)
class PasswordResetRequestAPITests(APITestCase):
    generic_message = (
        "If an account exists for this email, password reset instructions "
        "have been sent."
    )

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="reset-request-user",
            email="Reset.Request@Example.com",
            password="OriginalPass123!",
        )

    def setUp(self):
        self.url = reverse("accounts:password-reset-request")

    def extract_raw_token(self, email_message=None):
        email_message = email_message or mail.outbox[-1]
        match = re.search(r"Reset token: ([^\s]+)", email_message.body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_known_email_returns_generic_success_without_sensitive_data(self):
        response = self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"message": self.generic_message})
        self.assertNotIn("token", json.dumps(response.data).lower())
        self.assertNotIn(self.user.username, json.dumps(response.data))

    def test_unknown_email_returns_identical_success_without_side_effects(self):
        known_response = self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )
        PasswordResetToken.objects.all().delete()
        mail.outbox.clear()

        unknown_response = self.client.post(
            self.url,
            {"email": "unknown@example.com"},
            format="json",
        )

        self.assertEqual(unknown_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown_response.data, known_response.data)
        self.assertFalse(PasswordResetToken.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_request_email_matching_is_case_insensitive(self):
        response = self.client.post(
            self.url,
            {"email": "reset.request@EXAMPLE.COM"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            PasswordResetToken.objects.filter(user=self.user).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_request_creates_one_active_token_with_configured_expiry(self):
        before = timezone.now()

        response = self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = PasswordResetToken.objects.get(user=self.user)
        self.assertIsNone(token.used_at)
        self.assertGreaterEqual(
            token.expires_at,
            before + timedelta(minutes=29),
        )
        self.assertLessEqual(
            token.expires_at,
            timezone.now() + timedelta(minutes=31),
        )

    def test_request_sends_email_containing_raw_token_not_digest(self):
        self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(len(mail.outbox), 1)
        raw_token = self.extract_raw_token()
        stored_token = PasswordResetToken.objects.get(user=self.user)
        self.assertIn("NexaChat", mail.outbox[0].subject)
        self.assertIn("30 minutes", mail.outbox[0].body)
        self.assertIn("/api/accounts/password-reset/confirm/", mail.outbox[0].body)
        self.assertIn(raw_token, mail.outbox[0].body)
        self.assertNotEqual(stored_token.token_hash, raw_token)
        self.assertEqual(stored_token.token_hash, hash_reset_token(raw_token))
        self.assertEqual(len(stored_token.token_hash), 64)

    def test_second_request_invalidates_previous_active_token(self):
        self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )
        first_raw_token = self.extract_raw_token()
        first_token = PasswordResetToken.objects.get(
            token_hash=hash_reset_token(first_raw_token)
        )

        self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        first_token.refresh_from_db()
        self.assertIsNotNone(first_token.used_at)
        self.assertEqual(
            PasswordResetToken.objects.filter(
                user=self.user,
                used_at__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 2)

    def test_inactive_user_receives_generic_response_without_token_or_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"message": self.generic_message})
        self.assertFalse(PasswordResetToken.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_format_returns_bad_request(self):
        response = self.client.post(
            self.url,
            {"email": "not-an-email"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    @mock.patch("accounts.services.send_mail", return_value=0)
    def test_email_delivery_failure_rolls_back_active_token(self, send_mail_mock):
        response = self.client.post(
            self.url,
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"message": self.generic_message})
        self.assertFalse(PasswordResetToken.objects.exists())
        send_mail_mock.assert_called_once()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PasswordResetVerifyAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.original_password = "OriginalVerifyPass123!"
        cls.user = get_user_model().objects.create_user(
            username="reset-verify-user",
            email="verify@example.com",
            password=cls.original_password,
        )
        cls.other_user = get_user_model().objects.create_user(
            username="other-reset-verify-user",
            email="other-verify@example.com",
            password="OtherVerifyPass123!",
        )

    def setUp(self):
        self.url = reverse("accounts:password-reset-verify")
        self.raw_token, self.reset_token = create_password_reset_token(self.user)

    def payload(self, **overrides):
        values = {
            "email": self.user.email,
            "token": self.raw_token,
        }
        values.update(overrides)
        return values

    def test_valid_token_verifies_without_consuming_or_changing_password(self):
        original_password_hash = self.user.password

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"valid": True, "message": "Reset token is valid."},
        )
        self.reset_token.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNone(self.reset_token.used_at)
        self.assertEqual(self.user.password, original_password_hash)
        self.assertTrue(self.user.check_password(self.original_password))

    def test_invalid_token_returns_generic_bad_request(self):
        response = self.client.post(
            self.url,
            self.payload(token="invalid-token"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_expired_token_returns_generic_bad_request(self):
        self.reset_token.expires_at = timezone.now() - timedelta(seconds=1)
        self.reset_token.save(update_fields=["expires_at"])

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_consumed_token_returns_generic_bad_request(self):
        self.reset_token.used_at = timezone.now()
        self.reset_token.save(update_fields=["used_at"])

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_token_for_another_email_returns_generic_bad_request(self):
        response = self.client.post(
            self.url,
            self.payload(email=self.other_user.email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_inactive_user_token_is_not_usable(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_invalid_failure_reasons_return_same_public_detail(self):
        invalid_response = self.client.post(
            self.url,
            self.payload(token="invalid"),
            format="json",
        )

        self.reset_token.expires_at = timezone.now() - timedelta(seconds=1)
        self.reset_token.save(update_fields=["expires_at"])
        expired_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.reset_token.expires_at = timezone.now() + timedelta(hours=1)
        self.reset_token.used_at = timezone.now()
        self.reset_token.save(update_fields=["expires_at", "used_at"])
        consumed_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(
            invalid_response.data["detail"],
            expired_response.data["detail"],
        )
        self.assertEqual(
            expired_response.data["detail"],
            consumed_response.data["detail"],
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PasswordResetConfirmAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.original_password = "OriginalConfirmPass123!"
        cls.new_password = "NewConfirmPass456!"
        cls.user = get_user_model().objects.create_user(
            username="reset-confirm-user",
            email="confirm@example.com",
            password=cls.original_password,
        )
        cls.other_user = get_user_model().objects.create_user(
            username="other-reset-confirm-user",
            email="other-confirm@example.com",
            password="OtherConfirmPass123!",
        )
        cls.session = ChatSession.objects.create(
            user=cls.user,
            title="Preserved reset session",
        )
        cls.message = ChatMessage.objects.create(
            session=cls.session,
            role=ChatMessage.Role.USER,
            content="Preserved reset message",
        )

    def setUp(self):
        self.url = reverse("accounts:password-reset-confirm")
        self.refresh_url = reverse("accounts:token_refresh")
        self.raw_token, self.reset_token = create_password_reset_token(self.user)

    def payload(self, **overrides):
        values = {
            "email": self.user.email,
            "token": self.raw_token,
            "new_password": self.new_password,
            "new_password_confirm": self.new_password,
        }
        values.update(overrides)
        return values

    def test_valid_confirmation_securely_resets_password_and_preserves_data(self):
        original_profile_id = self.user.profile.id
        original_password_hash = self.user.password

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "message": (
                    "Password reset successful. Please log in with your new "
                    "password."
                )
            },
        )
        rendered_response = json.dumps(response.data)
        self.assertEqual(set(response.data), {"message"})
        self.assertNotIn(self.new_password, rendered_response)
        self.assertNotIn(self.raw_token, rendered_response)

        self.user.refresh_from_db()
        self.reset_token.refresh_from_db()
        self.assertFalse(self.user.check_password(self.original_password))
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertNotEqual(self.user.password, self.new_password)
        self.assertNotEqual(self.user.password, original_password_hash)
        self.assertIsNotNone(self.reset_token.used_at)
        self.assertEqual(
            UserProfile.objects.filter(user=self.user).count(),
            1,
        )
        self.assertEqual(self.user.profile.id, original_profile_id)
        self.assertTrue(ChatSession.objects.filter(pk=self.session.id).exists())
        self.assertTrue(ChatMessage.objects.filter(pk=self.message.id).exists())

    def test_password_mismatch_returns_bad_request(self):
        response = self.client.post(
            self.url,
            self.payload(new_password_confirm="DifferentPass456!"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password_confirm", response.data)

    def test_weak_password_returns_bad_request(self):
        response = self.client.post(
            self.url,
            self.payload(
                new_password="short",
                new_password_confirm="short",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_missing_token_returns_bad_request(self):
        payload = self.payload()
        payload.pop("token")

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", response.data)

    def test_invalid_token_returns_generic_bad_request(self):
        response = self.client.post(
            self.url,
            self.payload(token="invalid-token"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_expired_token_returns_generic_bad_request(self):
        self.reset_token.expires_at = timezone.now() - timedelta(seconds=1)
        self.reset_token.save(update_fields=["expires_at"])

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_previously_consumed_token_returns_generic_bad_request(self):
        self.reset_token.used_at = timezone.now()
        self.reset_token.save(update_fields=["used_at"])

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)

    def test_successful_token_cannot_be_reused(self):
        first_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )
        second_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            second_response.data["detail"],
            INVALID_RESET_TOKEN_MESSAGE,
        )

    def test_confirmation_invalidates_all_other_active_reset_tokens(self):
        sibling_raw_token = "sibling-active-token"
        sibling = PasswordResetToken.objects.create(
            user=self.user,
            token_hash=hash_reset_token(sibling_raw_token),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sibling.refresh_from_db()
        self.assertIsNotNone(sibling.used_at)
        self.assertFalse(
            PasswordResetToken.objects.filter(
                user=self.user,
                used_at__isnull=True,
            ).exists()
        )

    def test_outstanding_refresh_tokens_are_blacklisted_and_cannot_refresh(self):
        refresh = RefreshToken.for_user(self.user)

        reset_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(reset_response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        )

        refresh_response = self.client.post(
            self.refresh_url,
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(
            refresh_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_already_blacklisted_refresh_does_not_break_confirmation(self):
        already_blacklisted = RefreshToken.for_user(self.user)
        already_blacklisted.blacklist()
        active_refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BlacklistedToken.objects.filter(
                token__jti=already_blacklisted["jti"]
            ).exists()
        )
        self.assertTrue(
            BlacklistedToken.objects.filter(
                token__jti=active_refresh["jti"]
            ).exists()
        )

    def test_confirmation_resets_only_user_associated_with_token(self):
        other_raw_token, _ = create_password_reset_token(self.other_user)
        other_new_password = "OtherNewConfirmPass456!"

        response = self.client.post(
            self.url,
            {
                "email": self.other_user.email,
                "token": other_raw_token,
                "new_password": other_new_password,
                "new_password_confirm": other_new_password,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.original_password))
        self.assertTrue(self.other_user.check_password(other_new_password))

    def test_token_cannot_be_used_with_another_email(self):
        response = self.client.post(
            self.url,
            self.payload(email=self.other_user.email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], INVALID_RESET_TOKEN_MESSAGE)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.original_password))

    def test_current_password_cannot_be_reused(self):
        response = self.client.post(
            self.url,
            self.payload(
                new_password=self.original_password,
                new_password_confirm=self.original_password,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.original_password))

    def test_transactional_service_recheck_prevents_sequential_reuse(self):
        reset_user_password(
            self.user.email,
            self.raw_token,
            self.new_password,
        )

        with self.assertRaises(InvalidResetTokenError):
            reset_user_password(
                self.user.email,
                self.raw_token,
                "AnotherStrongPass789!",
            )

    def test_login_old_password_fails_and_new_password_succeeds(self):
        reset_response = self.client.post(
            self.url,
            self.payload(),
            format="json",
        )
        self.assertEqual(reset_response.status_code, status.HTTP_200_OK)

        login_url = reverse("accounts:login")
        old_login = self.client.post(
            login_url,
            {
                "username": self.user.username,
                "password": self.original_password,
            },
            format="json",
        )
        new_login = self.client.post(
            login_url,
            {
                "username": self.user.username,
                "password": self.new_password,
            },
            format="json",
        )

        self.assertEqual(old_login.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)
        self.assertTrue(new_login.data["access"])
        self.assertTrue(new_login.data["refresh"])


class PhaseFiveAccountRoutingAndSchemaTests(SimpleTestCase):
    def test_password_reset_routes_are_named(self):
        self.assertEqual(
            reverse("accounts:password-reset-request"),
            "/api/accounts/password-reset/request/",
        )
        self.assertEqual(
            reverse("accounts:password-reset-verify"),
            "/api/accounts/password-reset/verify/",
        )
        self.assertEqual(
            reverse("accounts:password-reset-confirm"),
            "/api/accounts/password-reset/confirm/",
        )

    def test_openapi_schema_documents_public_password_reset_operations(self):
        response = self.client.get(
            reverse("schema-json", kwargs={"format": ".json"})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schema = json.loads(response.content)
        paths = schema["paths"]

        for path in (
            "/accounts/password-reset/request/",
            "/accounts/password-reset/verify/",
            "/accounts/password-reset/confirm/",
        ):
            self.assertIn(path, paths)
            self.assertEqual(set(paths[path]) - {"parameters"}, {"post"})
            self.assertEqual(paths[path]["post"]["security"], [])

        confirm_schema = schema["definitions"]["PasswordResetConfirm"]
        self.assertIn("email", confirm_schema["properties"])
        self.assertIn("token", confirm_schema["properties"])
        self.assertIn("new_password", confirm_schema["properties"])
        self.assertIn("new_password_confirm", confirm_schema["properties"])
        self.assertTrue(
            schema["definitions"]["PasswordResetVerify"]["properties"]["token"][
                "x-writeOnly"
            ]
        )
        for field in ("token", "new_password", "new_password_confirm"):
            self.assertTrue(confirm_schema["properties"][field]["x-writeOnly"])
