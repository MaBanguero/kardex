from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [

    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    # ==========================================
    # 1. INTERFAZ DE USUARIO (UI)
    # ==========================================
    # Vista principal que carga el esqueleto HTML (Dashboard SPA)
    path('', views.dashboard_kardex, name='dashboard'),

    # ==========================================
    # 2. ENDPOINTS DE LA API (PWA & Asincronismo)
    # ==========================================
    # Descarga el catálogo completo para el LocalStorage (0ms latencia)
    path('api/sincronizar/', views.sincronizar_inventario_api, name='sincronizar_inventario_api'),

    # Consulta los movimientos de las últimas 24h para la pestaña de Historial
    path('api/historial/', views.historial_movimientos_api, name='api_historial'),

    # ==========================================
    # 3. TRANSACCIONES ACID
    # ==========================================
    # Recibe el JSON por POST para descontar stock o procesar devoluciones
    path('movimiento/', views.registrar_movimiento_view, name='registrar_movimiento'),

    # ==========================================
    # 4. REPORTES
    # ==========================================
    # Genera y descarga el archivo XLSX con el formato hospitalario
    path('exportar/', views.exportar_kardex_excel, name='exportar_excel'),

    # --- Panel Administrativo Personalizado (NUEVO) ---
    # Ruta: localhost:8000/admin-kardex/
    path('admin-kardex/', views.admin_dashboard_view, name='admin_dashboard'),
    path('api/gestion-producto/', views.api_gestion_producto, name='api_gestion_producto'),

    # API para procesar el archivo CSV de carga masiva
    path('api/carga-masiva/', views.api_carga_masiva, name='api_carga_masiva'),
    path('api/gestion-usuario/', views.api_gestion_usuario, name='api_gestion_usuario'),
    path('api/crear-solicitud/', views.api_crear_solicitud, name='api_crear_solicitud'),
path('api/atender-solicitud/', views.api_atender_solicitud, name='api_atender_solicitud'),
]
