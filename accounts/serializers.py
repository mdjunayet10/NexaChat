from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import UserProfile
from .services import (
    INVALID_RESET_TOKEN_MESSAGE,
    InvalidResetTokenError,
    ReusedPasswordError,
    get_valid_password_reset_token,
    request_password_reset,
    reset_user_password,
)

User = get_user_model()


class InvalidPasswordResetToken(APIException):
    status_code = 400
    default_detail = INVALID_RESET_TOKEN_MESSAGE
    default_code = "invalid_reset_token"


class ResetSecretField(serializers.CharField):
    class Meta:
        swagger_schema_fields = {"x_write_only": True}


def normalize_unique_email(value, *, instance=None):
    normalized_email = User._default_manager.normalize_email(value)
    matching_users = User._default_manager.filter(email__iexact=normalized_email)
    if instance is not None:
        matching_users = matching_users.exclude(pk=instance.pk)

    if matching_users.exists():
        raise serializers.ValidationError(
            "A user with this email address already exists."
        )

    return normalized_email


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name")
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
        )
        extra_kwargs = {
            "username": {"required": True},
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
        }

    def validate_email(self, value):
        return normalize_unique_email(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        password = attrs.get("password")

        if password != attrs.get("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )

        candidate_user = User(
            username=attrs.get("username"),
            email=attrs.get("email"),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(password, user=candidate_user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                {"password": list(error.messages)}
            ) from error

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        return User._default_manager.create_user(
            password=password,
            **validated_data,
        )


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs.get("username"),
            password=attrs.get("password"),
        )
        if user is None or not user.is_active:
            raise AuthenticationFailed("Invalid username or password.")

        return {"user": user}


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = (
            "id",
            "avatar",
            "bio",
            "phone",
            "theme",
            "language",
            "email_notifications",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class CurrentUserProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    profile = UserProfileSerializer(required=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "profile",
        )
        read_only_fields = ("id", "username")
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
        }

    def to_internal_value(self, data):
        if "password" in data:
            raise serializers.ValidationError(
                {"password": "Password cannot be updated through this endpoint."}
            )
        return super().to_internal_value(data)

    def validate_email(self, value):
        return normalize_unique_email(value, instance=self.instance)

    @transaction.atomic
    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", None)
        user_update_fields = []

        for field, value in validated_data.items():
            setattr(instance, field, value)
            user_update_fields.append(field)

        if user_update_fields:
            instance.save(update_fields=user_update_fields)

        profile, _ = UserProfile.objects.get_or_create(user=instance)
        if profile_data is not None:
            for field, value in profile_data.items():
                setattr(profile, field, value)
            profile.save()

        return instance


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)

    def validate_refresh(self, value):
        try:
            refresh = RefreshToken(value)
        except TokenError as error:
            raise serializers.ValidationError(
                "Invalid, expired, or blacklisted refresh token."
            ) from error

        request = self.context.get("request")
        token_user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        current_user_id = getattr(
            request.user,
            api_settings.USER_ID_FIELD,
            None,
        )
        if str(token_user_id) != str(current_user_id):
            raise serializers.ValidationError(
                "The refresh token does not belong to the authenticated user."
            )

        return refresh

    def save(self, **kwargs):
        self.validated_data["refresh"].blacklist()


class AuthResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)
    user = UserSummarySerializer(read_only=True)
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class TokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True, required=False)


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def save(self, **kwargs):
        request_password_reset(self.validated_data["email"])


class PasswordResetVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = ResetSecretField(
        write_only=True,
        help_text="Write-only raw password-reset token.",
    )

    def validate(self, attrs):
        if get_valid_password_reset_token(
            attrs["email"],
            attrs["token"],
        ) is None:
            raise InvalidPasswordResetToken
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = ResetSecretField(
        write_only=True,
        help_text="Write-only raw password-reset token.",
    )
    new_password = ResetSecretField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Write-only replacement password.",
    )
    new_password_confirm = ResetSecretField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Write-only replacement password confirmation.",
    )

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )

        reset_token = get_valid_password_reset_token(
            attrs["email"],
            attrs["token"],
        )
        if reset_token is None:
            raise InvalidPasswordResetToken

        user = reset_token.user
        if user.check_password(attrs["new_password"]):
            raise serializers.ValidationError(
                {"new_password": "The new password must be different."}
            )

        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                {"new_password": list(error.messages)}
            ) from error

        return attrs

    def save(self, **kwargs):
        try:
            return reset_user_password(
                self.validated_data["email"],
                self.validated_data["token"],
                self.validated_data["new_password"],
            )
        except InvalidResetTokenError as error:
            raise InvalidPasswordResetToken from error
        except ReusedPasswordError as error:
            raise serializers.ValidationError(
                {"new_password": "The new password must be different."}
            ) from error


class PasswordResetRequestResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)


class PasswordResetVerifyResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)
