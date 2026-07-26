import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .models import PasswordResetToken

User = get_user_model()

INVALID_RESET_TOKEN_MESSAGE = "Invalid or expired reset token."


class InvalidResetTokenError(Exception):
    pass


class PasswordResetDeliveryError(Exception):
    pass


class ReusedPasswordError(Exception):
    pass


def generate_reset_token():
    return secrets.token_urlsafe(32)


def hash_reset_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def invalidate_user_reset_tokens(user, *, used_at=None):
    return PasswordResetToken.objects.filter(
        user=user,
        used_at__isnull=True,
    ).update(used_at=used_at or timezone.now())


def create_password_reset_token(user):
    invalidate_user_reset_tokens(user)
    raw_token = generate_reset_token()
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_reset_token(raw_token),
        expires_at=timezone.now()
        + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_TTL_MINUTES),
    )
    return raw_token, token


def send_password_reset_email(user, raw_token):
    ttl_minutes = settings.PASSWORD_RESET_TOKEN_TTL_MINUTES
    message = (
        f"Hello {user.username},\n\n"
        "A password reset was requested for your NexaChat account.\n\n"
        f"Reset token: {raw_token}\n"
        f"This token expires in {ttl_minutes} minutes.\n"
        "Submit it to /api/accounts/password-reset/confirm/ with your email "
        "and new password.\n\n"
        "If you did not request this reset, you can ignore this email."
    )
    return send_mail(
        subject="NexaChat password reset",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def request_password_reset(email):
    user = (
        User._default_manager.filter(email__iexact=email, is_active=True)
        .order_by("pk")
        .first()
    )
    if user is None:
        return False

    try:
        with transaction.atomic():
            raw_token, _ = create_password_reset_token(user)
            if send_password_reset_email(user, raw_token) != 1:
                raise PasswordResetDeliveryError
    except PasswordResetDeliveryError:
        return False

    return True


def get_valid_password_reset_token(email, raw_token, *, for_update=False):
    if not isinstance(raw_token, str) or not raw_token:
        return None

    users = User._default_manager.filter(
        email__iexact=email,
        is_active=True,
    ).order_by("pk")
    if for_update:
        users = users.select_for_update()
    user = users.first()
    if user is None:
        return None

    candidate_hash = hash_reset_token(raw_token)
    tokens = PasswordResetToken.objects.filter(
        user=user,
        token_hash=candidate_hash,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).select_related("user")
    if for_update:
        tokens = tokens.select_for_update()
    token = tokens.first()

    if token is None or not secrets.compare_digest(
        token.token_hash,
        candidate_hash,
    ):
        return None

    return token


def revoke_user_refresh_tokens(user):
    for outstanding_token in OutstandingToken.objects.filter(
        user=user
    ).iterator():
        BlacklistedToken.objects.get_or_create(token=outstanding_token)


def reset_user_password(email, raw_token, new_password):
    with transaction.atomic():
        reset_token = get_valid_password_reset_token(
            email,
            raw_token,
            for_update=True,
        )
        if reset_token is None:
            raise InvalidResetTokenError

        user = reset_token.user
        if user.check_password(new_password):
            raise ReusedPasswordError

        consumed_at = timezone.now()
        consumed = PasswordResetToken.objects.filter(
            pk=reset_token.pk,
            used_at__isnull=True,
            expires_at__gt=consumed_at,
        ).update(used_at=consumed_at)
        if consumed != 1:
            raise InvalidResetTokenError

        user.set_password(new_password)
        user.save(update_fields=["password"])

        PasswordResetToken.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=consumed_at)
        revoke_user_refresh_tokens(user)

    return user
