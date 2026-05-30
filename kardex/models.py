import uuid

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class SolicitudStock(models.Model):
    ESTADOS = (
        ('PENDIENTE', 'Pendiente (En Revisión)'),
        ('SOLICITADO', 'Solicitado (Despachado)'),
    )

    medicamento = models.ForeignKey('Medicamento', on_delete=models.CASCADE, related_name='solicitudes')
    sede_solicitante = models.ForeignKey('Ubicacion', on_delete=models.CASCADE, related_name='pedidos_realizados')
    usuario_solicitante = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    cantidad_pedida = models.PositiveIntegerField()
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    grupo_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pedido de {self.medicamento.principio_activo} - {self.sede_solicitante.nombre} ({self.estado})"

    
class PerfilUsuario(models.Model):
    # ¡Eliminamos la lista de ROLES y el campo rol!
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    ubicacion_asignada = models.ForeignKey(
        'Ubicacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Ubicación física principal del usuario."
    )
    numero_identificacion = models.CharField(max_length=50, unique=True)
    must_change_password = models.BooleanField(
        default=True,
        help_text="Indica si el usuario debe cambiar su contraseña en el próximo inicio de sesión."
    )
    last_password_change = models.DateTimeField(
        null=True, blank=True,
        help_text="Fecha y hora del último cambio de contraseña."
    )

    def __str__(self):
        return self.usuario.get_full_name()


class ConfiguracionSistema(models.Model):
    horas_limite_devolucion = models.PositiveIntegerField(
        default=2,
        help_text="Horas máximas para devolver un medicamento antes de considerarse aplicado."
    )
    alertas_habilitadas = models.BooleanField(
        default=True,
        help_text="Si está desactivado, no se muestran alertas de stock crítico ni semáforos de vencimiento."
    )

    class Meta:
        verbose_name_plural = "Configuración del Sistema"

    def __str__(self):
        estado_alerta = "ON" if self.alertas_habilitadas else "OFF"
        return f"Devolución: {self.horas_limite_devolucion}h | Alertas: {estado_alerta}"


class Ubicacion(models.Model):
    nombre = models.CharField(max_length=100)
    es_bodega_principal = models.BooleanField(default=False)

    def __str__(self):
        return self.nombre


class Medicamento(models.Model):
    TIPOS = (
        ('MEDICAMENTO', 'Medicamento'),
        ('DISPOSITIVO', 'Dispositivo Médico'),
    )

    tipo = models.CharField(max_length=20, choices=TIPOS, default='MEDICAMENTO')
    codigo = models.CharField(max_length=50, null=True, blank=True, help_text="Código ATC, CUM o interno")
    principio_activo = models.CharField(max_length=150, verbose_name="Nombre / Principio Activo")
    concentracion = models.CharField(max_length=100, null=True, blank=True, help_text="Ej: 500mg, 0.3%, 1g")
    forma_farmaceutica = models.CharField(max_length=100, help_text="Ej: Tableta, Solución, Jarabe")
    presentacion = models.CharField(max_length=100, null=True, blank=True, help_text="Ej: Caja x 30, Frasco x 100ml")
    laboratorio = models.CharField(max_length=150, null=True, blank=True)
    registro_invima = models.CharField(max_length=100, null=True, blank=True)
    vida_util = models.CharField(max_length=50, null=True, blank=True,
                                 help_text="Ej: 5 años, 3 AÑOS, 1 año")
    clasificacion_riesgo = models.CharField(max_length=10, null=True, blank=True,
                                            choices=[('I', 'I - Bajo'), ('IIa', 'IIa - Medio'),
                                                     ('IIb', 'IIb - Medio-Alto'), ('III', 'III - Alto')],
                                            help_text="Clasificación de riesgo INVIMA: I, IIa, IIb, III")
    cups_codigo = models.CharField(max_length=20, null=True, blank=True,
                                   help_text="Código CUPS del RIPS (ej: 70005, 70174). Se usa para conciliación automática.")

    def __str__(self):
        conc = f" {self.concentracion}" if self.concentracion else ""
        return f"{self.principio_activo}{conc} - {self.forma_farmaceutica}"


class InventarioStock(models.Model):
    ubicacion = models.ForeignKey(Ubicacion, on_delete=models.CASCADE)
    medicamento = models.ForeignKey(Medicamento, on_delete=models.CASCADE)
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField()
    cantidad_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=10)

    class Meta:
        unique_together = ('ubicacion', 'medicamento', 'lote')

    def __str__(self):
        return f"{self.medicamento.codigo} | Lote: {self.lote} | Qty: {self.cantidad_actual}"


class Documento(models.Model):
    TIPOS_MOVIMIENTO = [
        ('ENTRADA', 'Ingreso por Compra'),
        ('SALIDA', 'Salida a Paciente'),
        ('TRASLADO', 'Traslado entre Bodegas'),
        ('DEVOLUCION', 'Devolución de Paciente'),
    ]

    tipo_mov = models.CharField(max_length=20, choices=TIPOS_MOVIMIENTO)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)

    origen = models.ForeignKey(Ubicacion, related_name='salidas', on_delete=models.PROTECT, null=True, blank=True)
    destino = models.ForeignKey(Ubicacion, related_name='entradas', on_delete=models.PROTECT, null=True, blank=True)

    id_paciente = models.CharField(max_length=50, null=True, blank=True)
    documento_referencia = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Para devoluciones, apunta a la salida original"
    )

    # Aceptación / Rechazo de Traslado (firma digital)
    aceptado = models.BooleanField(default=False, verbose_name="Aceptado por enfermera")
    rechazado = models.BooleanField(default=False, verbose_name="Rechazado por enfermera")
    motivo_rechazo = models.TextField(null=True, blank=True, verbose_name="Motivo de rechazo")
    firma_nombre = models.CharField(max_length=200, null=True, blank=True, verbose_name="Nombre de quien firma")
    firma_cedula = models.CharField(max_length=50, null=True, blank=True, verbose_name="Cédula de quien firma")
    fecha_aceptacion = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de aceptación o rechazo")

    def tiempo_agotado_para_devolucion(self):
        if self.tipo_mov != 'SALIDA':
            return True
        config = ConfiguracionSistema.objects.first()
        limite = config.horas_limite_devolucion if config else 2
        return timezone.now() > (self.fecha + timedelta(hours=limite))

    def __str__(self):
        return f"Doc {self.id} - {self.tipo_mov} - {self.fecha.strftime('%Y-%m-%d %H:%M')}"


class DocumentoDetalle(models.Model):
    documento = models.ForeignKey(Documento, related_name='detalles', on_delete=models.CASCADE)
    medicamento = models.ForeignKey(Medicamento, on_delete=models.PROTECT)
    lote = models.CharField(max_length=50)
    cantidad = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.cantidad}x {self.medicamento.codigo} (Lote: {self.lote})"


class MapeoRIPSMedicamento(models.Model):
    """
    Mapea un medicamento del Kardex a uno o más códigos CUPS/nombres del RIPS.
    Permite que la conciliación automática sepa qué medicamento del Kardex
    corresponde a qué procedimiento en el reporte RIPS.
    """
    medicamento = models.ForeignKey(
        'Medicamento', on_delete=models.CASCADE, related_name='mapeos_rips'
    )
    cups_codigo = models.CharField(
        max_length=20, blank=True,
        help_text="Código CUPS del RIPS (ej: 70005, 70174)"
    )
    nombre_rips = models.CharField(
        max_length=500, blank=True,
        help_text="Nombre del procedimiento en RIPS (ej: ACETAMINOFEN 500 MG TABLETA)"
    )
    gruposervicio = models.CharField(
        max_length=50, blank=True,
        help_text="Grupo de servicio (MEDICAMENTO, SERVICIO FARMACEUTICO, INSUMOS)"
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Mapeo RIPS - Medicamento'
        verbose_name_plural = 'Mapeos RIPS - Medicamentos'
        unique_together = ('medicamento', 'cups_codigo', 'nombre_rips')

    def __str__(self):
        return f"{self.medicamento.principio_activo} -> {self.nombre_rips or self.cups_codigo}"


class CargaRIPS(models.Model):
    """
    Registro de cada archivo RIPS (reporte201) cargado al sistema.
    """
    ESTADOS = [
        ('CARGADA', 'Cargada'),
        ('CONCILIADA', 'Conciliada'),
        ('ERROR', 'Error'),
    ]

    archivo = models.CharField(max_length=500, help_text="Nombre del archivo original")
    fecha_carga = models.DateTimeField(auto_now_add=True)
    periodo_inicio = models.DateField(help_text="Fecha inicio del reporte")
    periodo_fin = models.DateField(help_text="Fecha fin del reporte")
    total_registros = models.PositiveIntegerField(default=0)
    registros_medicamentos = models.PositiveIntegerField(default=0, help_text="Solo MEDICAMENTO + SERVICIO FARMACEUTICO + INSUMOS")
    estado = models.CharField(max_length=20, choices=ESTADOS, default='CARGADA')

    class Meta:
        verbose_name = 'Carga RIPS'
        verbose_name_plural = 'Cargas RIPS'
        ordering = ['-fecha_carga']

    def __str__(self):
        return f"RIPS {self.periodo_inicio} a {self.periodo_fin} ({self.total_registros} registros)"


class RegistroRIPS(models.Model):
    """
    Cada fila del reporte201 que corresponde a medicamentos/insumos.
    """
    carga = models.ForeignKey(CargaRIPS, on_delete=models.CASCADE, related_name='registros')

    # Datos del CSV
    gruposervicio = models.CharField(max_length=100, db_index=True)
    cupscodigo = models.CharField(max_length=20, db_index=True)
    nombreprocedimiento = models.CharField(max_length=500)
    cantidad = models.PositiveIntegerField(default=1)
    valorunitario = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valortotal = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    identificacion_paciente = models.CharField(max_length=50, db_index=True)
    nombre_paciente = models.CharField(max_length=300, blank=True, default='')
    identificacion_profesional = models.CharField(max_length=50, blank=True, default='')
    nombre_profesional = models.CharField(max_length=300, blank=True, default='')
    especialidad = models.CharField(max_length=200, blank=True, default='')
    fechaprocedimiento = models.DateTimeField(null=True, blank=True, db_index=True)
    numerofactura = models.CharField(max_length=100, blank=True, default='')
    sede = models.CharField(max_length=200, blank=True, default='')
    admision = models.CharField(max_length=100, blank=True, default='')
    modalidad = models.CharField(max_length=50, blank=True, default='')
    diagnostico = models.CharField(max_length=20, blank=True, default='')
    diagnosticonombre = models.CharField(max_length=300, blank=True, default='')

    # Para conciliación
    medicamento_mapeado = models.ForeignKey(
        Medicamento, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Medicamento del Kardex con el que se mapeó automáticamente"
    )

    class Meta:
        verbose_name = 'Registro RIPS'
        verbose_name_plural = 'Registros RIPS'
        indexes = [
            models.Index(fields=['identificacion_paciente', 'fechaprocedimiento']),
        ]

    def __str__(self):
        return f"{self.nombreprocedimiento} - {self.nombre_paciente} ({self.fechaprocedimiento.date() if self.fechaprocedimiento else '?'})"


class Conciliacion(models.Model):
    """
    Resultado de la conciliación entre Kardex (SALIDAS) y RIPS.
    """
    carga_rips = models.ForeignKey(CargaRIPS, on_delete=models.CASCADE, related_name='conciliaciones')
    fecha_conciliacion = models.DateTimeField(auto_now_add=True)
    periodo_inicio = models.DateField()
    periodo_fin = models.DateField()

    # Totales
    total_salidas_kardex = models.PositiveIntegerField(default=0, help_text="Total de SALIDAS en el Kardex")
    total_medicamentos_kardex = models.PositiveIntegerField(default=0, help_text="Suma de cantidades de SALIDAS")
    total_registros_rips = models.PositiveIntegerField(default=0)
    total_cantidad_rips = models.PositiveIntegerField(default=0)

    # Resultados
    coincidencias = models.PositiveIntegerField(default=0)
    no_facturados = models.PositiveIntegerField(default=0, help_text="En Kardex pero no en RIPS")
    no_despachados = models.PositiveIntegerField(default=0, help_text="En RIPS pero no en Kardex")

    class Meta:
        verbose_name = 'Conciliación'
        verbose_name_plural = 'Conciliaciones'
        ordering = ['-fecha_conciliacion']

    def __str__(self):
        return f"Conciliación {self.periodo_inicio} a {self.periodo_fin} - {self.coincidencias} OK, {self.no_facturados} no facturados"


class DetalleConciliacion(models.Model):
    """
    Cada ítem de la conciliación (coincidencia o discrepancia).
    """
    ESTADOS = [
        ('COINCIDE', '✅ Coincide'),
        ('NO_FACTURADO', '❌ No Facturado'),
        ('NO_DESPACHADO', '⚠️ No Despachado'),
        ('CANTIDAD_DIF', '🔄 Cantidad Diferente'),
    ]

    conciliacion = models.ForeignKey(Conciliacion, on_delete=models.CASCADE, related_name='detalles')
    estado = models.CharField(max_length=20, choices=ESTADOS, db_index=True)

    # Referencias
    documento_salida = models.ForeignKey(Documento, on_delete=models.SET_NULL, null=True, blank=True)
    registro_rips = models.ForeignKey(RegistroRIPS, on_delete=models.SET_NULL, null=True, blank=True)
    medicamento = models.ForeignKey(Medicamento, on_delete=models.SET_NULL, null=True, blank=True)

    # Datos para reporte
    medicamento_nombre = models.CharField(max_length=300)
    paciente_identificacion = models.CharField(max_length=50, blank=True, default='')
    paciente_nombre = models.CharField(max_length=300, blank=True, default='')
    cantidad_kardex = models.PositiveIntegerField(default=0)
    cantidad_rips = models.PositiveIntegerField(default=0)
    fecha = models.DateTimeField()
    sede = models.CharField(max_length=200, blank=True, default='')
    profesional = models.CharField(max_length=300, blank=True, default='')
    observacion = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Detalle de Conciliación'
        verbose_name_plural = 'Detalles de Conciliación'

    def __str__(self):
        return f"{self.get_estado_display()} - {self.medicamento_nombre} ({self.paciente_nombre})"


class TurnoEnfermera(models.Model):
    """
    Control de turno único por sede para enfermeras.
    Solo UNA enfermera puede estar activa por turno de 12h.
    """
    enfermera = models.ForeignKey(User, on_delete=models.CASCADE, related_name='turnos_enfermera')
    sede = models.ForeignKey(Ubicacion, on_delete=models.CASCADE, related_name='turnos_enfermera')
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_expiracion = models.DateTimeField()
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Turno de Enfermera'
        verbose_name_plural = 'Turnos de Enfermeras'
        ordering = ['-fecha_inicio']

    def expires_in_minutes(self):
        """Minutos restantes del turno"""
        if not self.activo:
            return 0
        remaining = (self.fecha_expiracion - timezone.now()).total_seconds() / 60
        return max(0, int(remaining))

    def is_expired(self):
        return timezone.now() >= self.fecha_expiracion

    def __str__(self):
        return f"Turno {self.enfermera.get_full_name()} en {self.sede.nombre} ({'Activo' if self.activo else 'Inactivo'})"