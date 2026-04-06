from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Registration (custom view + template)
    path('register/', views.register, name='register'),

    # Login — Django built-in, uses templates/registration/login.html
    path('login/', auth_views.LoginView.as_view(
        redirect_authenticated_user=True,
    ), name='login'),

    # Logout — Django built-in, uses templates/registration/logged_out.html
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # Password reset flow — all Django built-ins
    # Templates expected in templates/registration/
    path('password-reset/',
         auth_views.PasswordResetView.as_view(),
         name='password_reset'),

    path('password-reset/done/',
         auth_views.PasswordResetDoneView.as_view(),
         name='password_reset_done'),

    path('password-reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),

    path('password-reset/complete/',
         auth_views.PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),
]
