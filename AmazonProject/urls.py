from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from accounts.views import me_view

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
    path('admin/', admin.site.urls),
]

# Internationalized URLs
urlpatterns += i18n_patterns(
    path('accounts/', include('accounts.urls')),
    path('products/', include('products.urls')),
    path('balance/', include('balance.urls', namespace='balance')),
    path('stoppoints/', include('stoppoints.urls')),
    path('wallet/', include('wallet.urls', namespace='wallet')),
    path("commission/", include("commission.urls")),
    path('notifications/', include('notification.urls')),
    path('me/', me_view, name='me'),
    path("chat/", include("chat.urls")),
)

# Serve media files (works locally and on Render free plan)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

#  Custom error handlers
handler404 = 'AmazonProject.views.error_404'
handler500 = 'AmazonProject.views.error_500'
handler403 = 'AmazonProject.views.error_403'



