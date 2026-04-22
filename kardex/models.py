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

    def __str__(self):
        return self.usuario.get_full_name()


class ConfiguracionSistema(models.Model):
    horas_limite_devolucion = models.PositiveIntegerField(
        default=2,
        help_text="Horas máximas para devolver un medicamento antes de considerarse aplicado."
    )

    class Meta:
        verbose_name_plural = "Configuración del Sistema"

    def __str__(self):
        return f"Regla de Devolución: {self.horas_limite_devolucion} horas"


class Ubicacion(models.Model):
    nombre = models.CharField(max_length=100)
    es_bodega_principal = models.BooleanField(default=False)

    def __str__(self):
        return self.nombre


class Medicamento(models.Model):
    codigo = models.CharField(max_length=50, unique=True, null=True, blank=True, help_text="Código ATC, CUM o interno")
    principio_activo = models.CharField(max_length=150)
    concentracion = models.CharField(max_length=100, null=True, blank=True, help_text="Ej: 500mg, 0.3%, 1g")
    forma_farmaceutica = models.CharField(max_length=100, help_text="Ej: Tableta, Solución, Jarabe")
    presentacion = models.CharField(max_length=100, null=True, blank=True, help_text="Ej: Caja x 30, Frasco x 100ml")
    laboratorio = models.CharField(max_length=150, null=True, blank=True)
    registro_invima = models.CharField(max_length=100, null=True, blank=True)

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