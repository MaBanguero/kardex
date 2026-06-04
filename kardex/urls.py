from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [

    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('cambiar-clave/', views.cambiar_clave_view, name='cambiar_clave'),
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

    # Descarga la plantilla de carga masiva en XLSX
    path('api/plantilla-carga/', views.descargar_plantilla_carga, name='plantilla_carga'),

    # --- Panel Administrativo Personalizado (NUEVO) ---
    # Ruta: localhost:8000/admin-kardex/
    path('admin-kardex/', views.admin_dashboard_view, name='admin_dashboard'),
    path('api/gestion-producto/', views.api_gestion_producto, name='api_gestion_producto'),

    # API para procesar el archivo CSV de carga masiva
    path('api/carga-masiva/', views.api_carga_masiva, name='api_carga_masiva'),
    path('api/gestion-usuario/', views.api_gestion_usuario, name='api_gestion_usuario'),
    path('api/crear-solicitud/', views.api_crear_solicitud, name='api_crear_solicitud'),
    path('api/atender-solicitud/', views.api_atender_solicitud, name='api_atender_solicitud'),

    # --- Nuevos CRUD (independencia del admin de Django) ---
    path('api/gestion-ubicacion/', views.api_gestion_ubicacion, name='api_gestion_ubicacion'),
    path('api/gestion-configuracion/', views.api_gestion_configuracion, name='api_gestion_configuracion'),
    path('api/estado-alertas/', views.api_estado_alertas, name='api_estado_alertas'),
    path('api/listar-movimientos/', views.api_listar_movimientos, name='api_listar_movimientos'),
    path('api/cancelar-solicitud/', views.api_cancelar_solicitud, name='api_cancelar_solicitud'),
    path('api/eliminar-stock/', views.api_eliminar_stock, name='api_eliminar_stock'),
    path('api/realizar-traslado/', views.api_realizar_traslado, name='api_realizar_traslado'),
    path('api/aceptar-traslado/', views.api_aceptar_traslado, name='api_aceptar_traslado'),
    path('api/rechazar-traslado/', views.api_rechazar_traslado, name='api_rechazar_traslado'),
    path('remision-traslado/<int:doc_id>/', views.ver_remision_traslado, name='ver_remision_traslado'),
    path('api/eliminar-usuario/', views.api_eliminar_usuario, name='api_eliminar_usuario'),
    path('api/plantilla-usuarios/', views.descargar_plantilla_usuarios, name='plantilla_usuarios'),
    path('api/carga-masiva-usuarios/', views.api_carga_masiva_usuarios, name='api_carga_masiva_usuarios'),
    path('api/cargar-rips/', views.api_cargar_rips, name='api_cargar_rips'),
    path('api/cerrar-turno/', views.api_cerrar_turno, name='api_cerrar_turno'),

    # ==========================================
    # 5. CONCILIACIÓN RIPS
    # ==========================================
    path('conciliacion/', views.conciliacion_lista, name='conciliacion_lista'),
    path('conciliacion/<int:conciliacion_id>/', views.conciliacion_detalle, name='conciliacion_detalle'),
    path('conciliacion/<int:conciliacion_id>/exportar/', views.conciliacion_exportar_excel, name='conciliacion_exportar'),
]
