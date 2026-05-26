from django.contrib import admin
from .models import (
    PerfilUsuario, ConfiguracionSistema, Medicamento,
    Ubicacion, InventarioStock, Documento, DocumentoDetalle, TurnoEnfermera,
    CargaRIPS, RegistroRIPS, Conciliacion, DetalleConciliacion, MapeoRIPSMedicamento
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


# ==============================================================================
# RIPS y Conciliación
# ==============================================================================


@admin.register(MapeoRIPSMedicamento)
class MapeoRIPSMedicamentoAdmin(admin.ModelAdmin):
    list_display = ('medicamento', 'cups_codigo', 'nombre_rips', 'gruposervicio', 'activo')
    list_filter = ('activo', 'gruposervicio')
    search_fields = ('medicamento__principio_activo', 'cups_codigo', 'nombre_rips')
    autocomplete_fields = ['medicamento']


@admin.register(RegistroRIPS)
class RegistroRIPSAdmin(admin.ModelAdmin):
    list_display = ('nombreprocedimiento', 'gruposervicio', 'identificacion_paciente',
                    'nombre_paciente', 'fechaprocedimiento', 'medicamento_mapeado')
    list_filter = ('gruposervicio', 'carga', 'fechaprocedimiento')
    search_fields = ('nombreprocedimiento', 'identificacion_paciente', 'nombre_paciente')
    date_hierarchy = 'fechaprocedimiento'


@admin.register(CargaRIPS)
class CargaRIPSAdmin(admin.ModelAdmin):
    list_display = ('archivo', 'fecha_carga', 'periodo_inicio', 'periodo_fin',
                    'total_registros', 'registros_medicamentos', 'estado')
    list_filter = ('estado', 'fecha_carga')
    date_hierarchy = 'fecha_carga'


class DetalleConciliacionInline(admin.TabularInline):
    model = DetalleConciliacion
    extra = 0
    can_delete = False
    fields = ('estado', 'medicamento_nombre', 'paciente_identificacion', 'cantidad_kardex', 'cantidad_rips', 'observacion')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conciliacion)
class ConciliacionAdmin(admin.ModelAdmin):
    list_display = ('fecha_conciliacion', 'periodo_inicio', 'periodo_fin',
                    'coincidencias', 'no_facturados', 'no_despachados', 'resumen')
    list_filter = ('fecha_conciliacion',)
    date_hierarchy = 'fecha_conciliacion'
    inlines = [DetalleConciliacionInline]

    @admin.display(description='Resumen')
    def resumen(self, obj):
        total = obj.coincidencias + obj.no_facturados + obj.no_despachados
        if total == 0:
            return "Sin datos"
        ok = obj.coincidencias * 100 // total if total else 0
        color = "🟢" if ok >= 90 else "🟡" if ok >= 70 else "🔴"
        return f"{color} {ok}% de acierto"


@admin.register(TurnoEnfermera)
class TurnoEnfermeraAdmin(admin.ModelAdmin):
    list_display = ('enfermera', 'sede', 'fecha_inicio', 'fecha_expiracion', 'activo', 'estado_turno')
    list_filter = ('activo', 'sede', 'fecha_inicio')
    search_fields = ('enfermera__username', 'enfermera__first_name', 'enfermera__last_name', 'sede__nombre')
    date_hierarchy = 'fecha_inicio'

    @admin.display(description='Estado')
    def estado_turno(self, obj):
        if obj.is_expired():
            return "⏰ Expirado"
        if not obj.activo:
            return "❌ Inactivo"
        mins = obj.expires_in_minutes()
        return f"✅ Activo ({mins} min restantes)"