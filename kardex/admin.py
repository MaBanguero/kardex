from django.contrib import admin
from .models import (
    PerfilUsuario, ConfiguracionSistema, Medicamento,
    Ubicacion, InventarioStock, Documento, DocumentoDetalle
)

class DocumentoDetalleInline(admin.TabularInline):
    model = DocumentoDetalle
    extra = 1

@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'fecha', 'tipo_mov', 'usuario', 'origen', 'destino', 'id_paciente')
    list_filter = ('tipo_mov', 'fecha', 'origen', 'destino')
    search_fields = ('id_paciente', 'usuario__username')
    inlines = [DocumentoDetalleInline]

@admin.register(InventarioStock)
class InventarioStockAdmin(admin.ModelAdmin):
    list_display = ('ubicacion', 'medicamento', 'lote', 'fecha_vencimiento', 'cantidad_actual', 'alerta_abastecimiento')
    list_filter = ('ubicacion',)
    search_fields = ('medicamento__principio_activo', 'lote', 'medicamento__codigo')

    @admin.display(description='Estado Stock')
    def alerta_abastecimiento(self, obj):
        if obj.cantidad_actual <= 0:
            return "❌ AGOTADO"
        if obj.cantidad_actual <= obj.stock_minimo:
            return "⚠️ SOLICITAR ABASTECIMIENTO"
        return "✅ OK"


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    # Reemplazamos 'rol' por 'get_roles'
    list_display = ('usuario', 'numero_identificacion', 'get_roles', 'ubicacion_asignada')

    # Filtramos a través de la relación del usuario hacia sus grupos
    list_filter = ('ubicacion_asignada', 'usuario__groups')

    search_fields = ('usuario__username', 'numero_identificacion', 'usuario__first_name', 'usuario__last_name')

    # Función personalizada para mostrar los roles (grupos) separados por coma
    def get_roles(self, obj):
        grupos = obj.usuario.groups.values_list('name', flat=True)
        return ", ".join(grupos) if grupos else "Sin rol"

    get_roles.short_description = 'Roles Asignados'

@admin.register(Medicamento)
class MedicamentoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'principio_activo', 'forma_farmaceutica', 'registro_invima')
    search_fields = ('codigo', 'principio_activo')

admin.site.register(ConfiguracionSistema)
admin.site.register(Ubicacion)