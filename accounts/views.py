from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import UserProfile
from .serializers import (
    AuthResponseSerializer,
    CurrentUserProfileSerializer,
    LoginSerializer,
    LogoutSerializer,
    MessageResponseSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestResponseSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifyResponseSerializer,
    PasswordResetVerifySerializer,
    RegisterSerializer,
    TokenRefreshResponseSerializer,
    UserSummarySerializer,
)


def build_auth_response(user, message):
    refresh = RefreshToken.for_user(user)
    return {
        "message": message,
        "user": UserSummarySerializer(user).data,
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


class RegisterView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    @swagger_auto_schema(
        operation_summary="Register a user",
        request_body=RegisterSerializer,
        responses={
            status.HTTP_201_CREATED: AuthResponseSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
        },
        security=[],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            build_auth_response(user, "Registration successful."),
            status=status.HTTP_201_CREATED,
        )


class LoginView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    def get_authenticate_header(self, request):
        return 'Bearer realm="api"'

    @swagger_auto_schema(
        operation_summary="Log in",
        request_body=LoginSerializer,
        responses={
            status.HTTP_200_OK: AuthResponseSerializer,
            status.HTTP_401_UNAUTHORIZED: "Invalid username or password.",
        },
        security=[],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            build_auth_response(
                serializer.validated_data["user"],
                "Login successful.",
            )
        )


class AccountTokenRefreshView(TokenRefreshView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Refresh a JWT access token",
        request_body=TokenRefreshSerializer,
        responses={
            status.HTTP_200_OK: TokenRefreshResponseSerializer,
            status.HTTP_401_UNAUTHORIZED: "Invalid or expired refresh token.",
        },
        security=[],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LogoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    @swagger_auto_schema(
        operation_summary="Log out and blacklist a refresh token",
        request_body=LogoutSerializer,
        responses={
            status.HTTP_200_OK: MessageResponseSerializer,
            status.HTTP_400_BAD_REQUEST: (
                "Missing, invalid, expired, or already blacklisted refresh token."
            ),
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=[{"Bearer": []}],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Logout successful."})


class CurrentUserProfileView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserProfileSerializer

    def get_object(self):
        user = self.request.user
        UserProfile.objects.get_or_create(user=user)
        return user

    @swagger_auto_schema(
        operation_summary="Retrieve the authenticated user's profile",
        responses={
            status.HTTP_200_OK: CurrentUserProfileSerializer,
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=[{"Bearer": []}],
        tags=["Profile"],
    )
    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_summary="Replace the authenticated user's profile",
        request_body=CurrentUserProfileSerializer,
        responses={
            status.HTTP_200_OK: CurrentUserProfileSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=[{"Bearer": []}],
        tags=["Profile"],
    )
    def put(self, request, *args, **kwargs):
        return self.update(request, partial=False)

    @swagger_auto_schema(
        operation_summary="Partially update the authenticated user's profile",
        request_body=CurrentUserProfileSerializer,
        responses={
            status.HTTP_200_OK: CurrentUserProfileSerializer,
            status.HTTP_400_BAD_REQUEST: "Field-level validation errors.",
            status.HTTP_401_UNAUTHORIZED: "JWT authentication is required.",
        },
        security=[{"Bearer": []}],
        tags=["Profile"],
    )
    def patch(self, request, *args, **kwargs):
        return self.update(request, partial=True)

    def update(self, request, *, partial):
        user = self.get_object()
        serializer = self.get_serializer(
            user,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PasswordResetRequestView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

    @swagger_auto_schema(
        operation_summary="Request password-reset instructions",
        request_body=PasswordResetRequestSerializer,
        responses={
            status.HTTP_200_OK: PasswordResetRequestResponseSerializer,
            status.HTTP_400_BAD_REQUEST: "Invalid email format.",
        },
        security=[],
        tags=["Password Reset"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {
                "message": (
                    "If an account exists for this email, password reset "
                    "instructions have been sent."
                )
            }
        )


class PasswordResetVerifyView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = PasswordResetVerifySerializer

    @swagger_auto_schema(
        operation_summary="Verify a password-reset token",
        request_body=PasswordResetVerifySerializer,
        responses={
            status.HTTP_200_OK: PasswordResetVerifyResponseSerializer,
            status.HTTP_400_BAD_REQUEST: "Invalid or expired reset token.",
        },
        security=[],
        tags=["Password Reset"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            {
                "valid": True,
                "message": "Reset token is valid.",
            }
        )


class PasswordResetConfirmView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    @swagger_auto_schema(
        operation_summary="Set a new password using a reset token",
        request_body=PasswordResetConfirmSerializer,
        responses={
            status.HTTP_200_OK: MessageResponseSerializer,
            status.HTTP_400_BAD_REQUEST: "Validation or reset-token errors.",
        },
        security=[],
        tags=["Password Reset"],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {
                "message": (
                    "Password reset successful. Please log in with your new "
                    "password."
                )
            }
        )
