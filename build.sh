#!/usr/bin/env bash
set -o errexit
set -o pipefail
set -o nounset

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate --noinput

# Create superuser once using env vars, skip if exists
python manage.py shell -c "
from django.contrib.auth import get_user_model;
User = get_user_model();
username = '$DJANGO_SUPERUSER_USERNAME'
password = '$DJANGO_SUPERUSER_PASSWORD'
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, password)
    print(f'Superuser {username} created.')
else:
    print(f'Superuser {username} already exists.')
"

# Collect static files for WhiteNoise
python manage.py collectstatic --noinput
