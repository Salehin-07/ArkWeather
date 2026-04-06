from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages


def register(request):
    """
    Registration view. Login and logout use Django's built-in views
    (django.contrib.auth.views.LoginView / LogoutView) configured in urls.py.
    """
    if request.user.is_authenticated:
        return redirect('weather:home')

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome to ArkWeather, {user.username}!')
            return redirect('weather:my_devices')
    else:
        form = UserCreationForm()

    return render(request, 'registration/register.html', {'form': form})
