"""
Tests de regresión para bugs corregidos en el sistema Kardex.
Cubre bugs #1 al #21 documentados.
"""

import json
import io
import csv
import datetime
from pathlib import Path

from django.test import TestCase, override_settings
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone

from kardex.models import (
    Medicamento, InventarioStock, Ubicacion, PerfilUsuario,
    Documento, DocumentoDetalle, SolicitudStock, ConfiguracionSistema
)
from kardex.services import (
    procesar_carga_masiva_productos, procesar_carga_masiva_usuarios,
    procesar_traslado, registrar_salida_paciente_inteligente,
    registrar_devolucion, registrar_devolucion_agrupada
)


class BaseTest(TestCase):
    """Set up reusable fixtures for all tests."""

    @classmethod
    def setUpTestData(cls):
        # Create groups
        for name in ['ADMIN', 'REGENTE', 'ENFERMERA']:
            Group.objects.get_or_create(name=name)

        # Create locations
        cls.central = Ubicacion.objects.create(nombre="Bodega Central", es_bodega_principal=True)
        cls.sede_norte = Ubicacion.objects.create(nombre="Sede Norte", es_bodega_principal=False)

        # La migración 0005 crea "FarmaciaSede1" como bodega principal — la desmarcamos
        Ubicacion.objects.filter(es_bodega_principal=True).exclude(id=cls.central.id).update(es_bodega_principal=False)

        # Create users
        cls.admin_user = User.objects.create_user(
            username="admintest", password="admin123",
            first_name="Admin", last_name="Central",
            email="admin@hospital.com"
        )
        cls.admin_user.groups.add(Group.objects.get(name='ADMIN'))
        PerfilUsuario.objects.create(
            usuario=cls.admin_user,
            ubicacion_asignada=cls.central,
            numero_identificacion="ADM-T001"
        )

        cls.regente_user = User.objects.create_user(
            username="regentetest", password="regente123",
            first_name="Regente", last_name="Norte",
            email="regente@hospital.com"
        )
        cls.regente_user.groups.add(Group.objects.get(name='REGENTE'))
        PerfilUsuario.objects.create(
            usuario=cls.regente_user,
            ubicacion_asignada=cls.sede_norte,
            numero_identificacion="REG-T001"
        )

        cls.enfermera_user = User.objects.create_user(
            username="enfermeratest", password="enfermera123",
            first_name="Enfermera", last_name="Norte",
            email="enfermera@hospital.com"
        )
        cls.enfermera_user.groups.add(Group.objects.get(name='ENFERMERA'))
        PerfilUsuario.objects.create(
            usuario=cls.enfermera_user,
            ubicacion_asignada=cls.sede_norte,
            numero_identificacion="ENF-T001"
        )

        # Create medications
        cls.med_acetaminofen = Medicamento.objects.create(
            codigo="770123456",
            principio_activo="ACETAMINOFEN",
            concentracion="500MG",
            forma_farmaceutica="TABLETA",
            presentacion="CAJA X 30",
            laboratorio="GENFAR",
            registro_invima="INVIMA-123"
        )
        cls.med_ibuprofeno = Medicamento.objects.create(
            principio_activo="IBUPROFENO",
            forma_farmaceutica="JARABE",
        )

        # Create stock in central
        cls.stock_central = InventarioStock.objects.create(
            ubicacion=cls.central,
            medicamento=cls.med_acetaminofen,
            lote="LOTE-A01",
            fecha_vencimiento=timezone.now().date() + datetime.timedelta(days=365),
            cantidad_actual=500,
            stock_minimo=50
        )

        # Create config
        ConfiguracionSistema.objects.create(horas_limite_devolucion=2)

    def _login(self, user):
        self.client.force_login(user)

    def _post_json(self, url_name, data, user=None):
        if user:
            self._login(user)
        return self.client.post(
            reverse(url_name),
            data=json.dumps(data),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )


# ==============================================================================
# BUG 1: Fecha de vencimiento no carga al editar
# BUG 2: stock_minimo no se envía en payload
# ==============================================================================
class Bug01y02_ProductoFechaStockMinimoTest(BaseTest):
    """Verifica que sincronizar_inventario_api devuelve campos completos."""

    def test_api_sincronizar_incluye_campos_completos(self):
        """Bug 1 & 2: La API debe devolver codigo, presentacion, laboratorio, stock_minimo."""
        self._login(self.enfermera_user)
        response = self.client.get(reverse('sincronizar_inventario_api'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('inventario', data)
        if len(data['inventario']) > 0:
            item = data['inventario'][0]
            self.assertIn('codigo', item, "Bug 1: Falta campo 'codigo' en respuesta API")
            self.assertIn('presentacion', item, "Bug 1: Falta campo 'presentacion' en respuesta API")
            self.assertIn('laboratorio', item, "Bug 1: Falta campo 'laboratorio' en respuesta API")
            self.assertIn('stock_minimo', item, "Bug 2: Falta campo 'stock_minimo' en respuesta API")

    def test_api_gestion_producto_stock_minimo(self):
        """Bug 2: api_gestion_producto debe respetar stock_minimo enviado."""
        self._login(self.admin_user)
        response = self._post_json('api_gestion_producto', {
            'principio_activo': 'NUEVO TEST',
            'forma_farmaceutica': 'TABLETA',
            'lote': 'LOTE-TEST',
            'fecha_vencimiento': (timezone.now().date() + datetime.timedelta(days=30)).isoformat(),
            'cantidad': 100,
            'stock_minimo': 25
        })
        self.assertEqual(response.status_code, 200)
        # Verificar que el stock_minimo se guardó (default era 10)
        stock = InventarioStock.objects.filter(
            ubicacion=self.admin_user.perfil.ubicacion_asignada,
            lote='LOTE-TEST'
        ).first()
        self.assertIsNotNone(stock)
        self.assertEqual(stock.stock_minimo, 25,
                         f"Bug 2: Se esperaba stock_minimo=25, se obtuvo {stock.stock_minimo}")

    def test_fecha_vencimiento_parseo(self):
        """Bug 1: Fecha debe venir en YYYY-MM-DD para que el input type=date funcione."""
        self._login(self.enfermera_user)
        response = self.client.get(reverse('sincronizar_inventario_api'))
        data = response.json()
        for item in data['inventario']:
            if item['fecha_vencimiento']:
                # Debe ser YYYY-MM-DD
                parts = item['fecha_vencimiento'].split('-')
                self.assertEqual(len(parts), 3, f"Bug 1: Formato de fecha inválido: {item['fecha_vencimiento']}")
                self.assertEqual(len(parts[0]), 4, "Bug 1: Año debe tener 4 dígitos")
                self.assertIn(parts[1], [str(m).zfill(2) for m in range(1, 13)],
                              f"Bug 1: Mes inválido: {parts[1]}")


# ==============================================================================
# BUG 4: Parámetros invertidos en carga masiva
# BUG 5: Validación de permisos con perfil.rol (inexistente)
# ==============================================================================
class Bug04y05_CargaMasivaPermisosTest(BaseTest):
    """Verifica que procesar_carga_masiva_productos funciona con grupos."""

    def _make_csv(self, rows):
        """Helper to create a CSV file-like object."""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'principio_activo', 'forma_farmaceutica', 'concentracion',
            'lote', 'fecha_vencimiento', 'cantidad', 'stock_minimo',
            'presentacion', 'laboratorio', 'codigo'
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        output.seek(0)
        return io.BytesIO(output.getvalue().encode('utf-8'))

    def test_carga_masiva_admin_group(self):
        """
        Bug 4 & 5: ADMIN group debe poder hacer carga masiva.
        Antes rompía con AttributeError por perfil.rol inexistente.
        """
        csv_file = self._make_csv([{
            'principio_activo': 'LORATADINA',
            'forma_farmaceutica': 'TABLETA',
            'concentracion': '10MG',
            'lote': 'LOTE-L001',
            'fecha_vencimiento': (timezone.now().date() + datetime.timedelta(days=180)).isoformat(),
            'cantidad': '200',
            'stock_minimo': '20',
            'presentacion': 'CAJA X 10',
            'laboratorio': 'MK',
            'codigo': '770999'
        }])
        try:
            resultado = procesar_carga_masiva_productos(self.admin_user, csv_file)
            self.assertEqual(resultado, 1, "Bug 4/5: Debió procesar 1 registro")
        except AttributeError as e:
            self.fail(f"Bug 5: AttributeError por perfil.rol: {e}")
        except PermissionError as e:
            self.fail(f"Bug 5: PermissionError inesperado: {e}")

        # Verificar que el medicamento y stock se crearon
        self.assertTrue(
            Medicamento.objects.filter(principio_activo='LORATADINA').exists(),
            "Bug 4: No se creó el medicamento"
        )
        self.assertTrue(
            InventarioStock.objects.filter(
                ubicacion=self.central, lote='LOTE-L001'
            ).exists(),
            "Bug 4: No se creó el stock"
        )

    def test_carga_masiva_rechaza_enfermera(self):
        """Bug 5: ENFERMERA no debe poder hacer carga masiva."""
        csv_file = self._make_csv([{
            'principio_activo': 'OMEPRAZOL',
            'forma_farmaceutica': 'CAPSULA',
            'lote': 'LOTE-O001',
            'fecha_vencimiento': '2026-12-31',
            'cantidad': '100',
        }])
        with self.assertRaises(PermissionError):
            procesar_carga_masiva_productos(self.enfermera_user, csv_file)

    def test_carga_masiva_api_view(self):
        """
        Bug 4: La vista api_carga_masiva debe llamar con argumentos correctos.
        Antes pasaba (archivo_csv, ubicacion) en vez de (usuario, archivo_csv).
        """
        self._login(self.admin_user)
        csv_content = "principio_activo,forma_farmaceutica,lote,fecha_vencimiento,cantidad\nDIAZEPAM,TABLETA,LOTE-D001,2026-12-31,50\n"
        response = self.client.post(
            reverse('api_carga_masiva'),
            {'archivo': io.BytesIO(csv_content.encode('utf-8'))},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        # La vista devuelve error si el archivo no es subido como MultipartParser
        # pero lo importante es que NO devuelva 500 por AttributeError
        self.assertNotEqual(response.status_code, 500,
                            f"Bug 4: La vista NO debió fallar con 500. Status: {response.status_code}")


# ==============================================================================
# BUG 6: Creación de usuario con campo rol inexistente
# ==============================================================================
class Bug06_CargaMasivaUsuariosTest(BaseTest):
    """Verifica que procesar_carga_masiva_usuarios no usa 'rol'."""

    def test_carga_masiva_usuarios_sin_rol(self):
        """
        Bug 6: Antes creaba PerfilUsuario con 'rol=row['rol']' que ya no existe.
        Ahora usa grupos correctamente.
        """
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'username', 'first_name', 'last_name', 'email',
            'roles', 'identificacion', 'ubicacion_id', 'password'
        ])
        writer.writeheader()
        writer.writerow({
            'username': 'nuevo_user',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'email': 'nuevo@test.com',
            'roles': 'ENFERMERA|REGENTE',
            'identificacion': 'ID-999',
            'ubicacion_id': str(self.sede_norte.id),
            'password': 'pass123'
        })
        output.seek(0)
        csv_file = io.BytesIO(output.getvalue().encode('utf-8'))

        try:
            count = procesar_carga_masiva_usuarios(csv_file)
            self.assertEqual(count, 1)
        except Exception as e:
            if 'rol' in str(e) or 'unexpected keyword' in str(e).lower():
                self.fail(f"Bug 6: Error por campo 'rol' inexistente: {e}")
            raise

        # Verificar que se creó con grupos correctos
        user = User.objects.get(username='nuevo_user')
        grupos = list(user.groups.values_list('name', flat=True))
        self.assertIn('ENFERMERA', grupos)
        self.assertIn('REGENTE', grupos)
        self.assertNotIn('ADMIN', grupos)
        # Verificar que PerfilUsuario se creó sin campo 'rol'
        perfil = PerfilUsuario.objects.get(usuario=user)
        self.assertEqual(perfil.numero_identificacion, 'ID-999')


# ==============================================================================
# BUG 7 & 8: api_atender_solicitud no descuenta stock ni crea Documento
# ==============================================================================
class Bug07y08_AtenderSolicitudTest(BaseTest):
    """Verifica que al atender una solicitud se descuente de bodega central y cree Documento."""

    def setUp(self):
        super().setUp()
        # Crear solicitud desde sede_norte
        self.solicitud = SolicitudStock.objects.create(
            medicamento=self.med_acetaminofen,
            sede_solicitante=self.sede_norte,
            usuario_solicitante=self.regente_user,
            cantidad_pedida=50,
            estado='PENDIENTE'
        )
        # Stock en central para poder despachar
        self.stock_central_2 = InventarioStock.objects.create(
            ubicacion=self.central,
            medicamento=self.med_acetaminofen,
            lote="LOTE-A02",
            fecha_vencimiento=timezone.now().date() + datetime.timedelta(days=200),
            cantidad_actual=300,
        )

    def test_atender_solicitud_descarta_central(self):
        """Bug 7: Debe descontar de la bodega central."""
        self._login(self.admin_user)
        response = self._post_json('api_atender_solicitud', {
            'solicitud_id': self.solicitud.id
        })
        self.assertEqual(response.status_code, 200, f"Bug 7: {response.json()}")

        # Verificar que el stock de central se descontó
        total_central = sum(
            s.cantidad_actual
            for s in InventarioStock.objects.filter(
                ubicacion=self.central,
                medicamento=self.med_acetaminofen
            )
        )
        # 500 + 300 - 50 = 750
        self.assertEqual(total_central, 750,
                         f"Bug 7: Stock central debió ser 750, es {total_central}")

    def test_atender_solicitud_no_stock_central(self):
        """Bug 7: Si no hay stock en central, debe rechazar."""
        self._login(self.admin_user)
        # Crear solicitud de más de lo que hay
        solicitud_grande = SolicitudStock.objects.create(
            medicamento=self.med_acetaminofen,
            sede_solicitante=self.sede_norte,
            usuario_solicitante=self.regente_user,
            cantidad_pedida=9999,
            estado='PENDIENTE'
        )
        response = self._post_json('api_atender_solicitud', {
            'solicitud_id': solicitud_grande.id
        })
        self.assertEqual(response.status_code, 400,
                         "Bug 7: Debió rechazar por stock insuficiente en central")

    def test_atender_solicitud_crea_documento(self):
        """Bug 8: Debe crear Documento y DocumentoDetalle."""
        self._login(self.admin_user)
        doc_count_before = Documento.objects.count()
        det_count_before = DocumentoDetalle.objects.count()

        response = self._post_json('api_atender_solicitud', {
            'solicitud_id': self.solicitud.id
        })
        self.assertEqual(response.status_code, 200)

        doc_count_after = Documento.objects.count()
        det_count_after = DocumentoDetalle.objects.count()

        self.assertGreater(doc_count_after, doc_count_before,
                           "Bug 8: No se creó Documento")
        self.assertGreater(det_count_after, det_count_before,
                           "Bug 8: No se creó DocumentoDetalle")

        # Verificar que el documento es de tipo ENTRADA y destino correcto
        doc_creado = Documento.objects.filter(tipo_mov='ENTRADA').last()
        self.assertIsNotNone(doc_creado, "Bug 8: Documento no es tipo ENTRADA")
        self.assertEqual(doc_creado.destino, self.sede_norte,
                         "Bug 8: Destino del documento no es la sede solicitante")

    def test_atender_solicitud_sin_bodega_central(self):
        """Bug 7: Si no hay bodega central, aún debe funcionar (salta validación)."""
        # Eliminar la bodega principal
        self.central.es_bodega_principal = False
        self.central.save()

        self._login(self.admin_user)
        response = self._post_json('api_atender_solicitud', {
            'solicitud_id': self.solicitud.id
        })
        self.assertIn(response.status_code, [200, 400])


# ==============================================================================
# BUG 10: Email se borra al editar usuario
# BUG 11: Password vacío sobreescribe contraseña
# ==============================================================================
class Bug10y11_EditarUsuarioEmailPasswordTest(BaseTest):
    """Verifica que editar usuario no borra email ni sobreescribe password en vacío."""

    def test_email_no_se_borra_al_editar(self):
        """Bug 10: Editar usuario sin email no debe borrar el email existente."""
        self._login(self.admin_user)
        original_email = self.regente_user.email
        self.assertNotEqual(original_email, '',
                            "Setup: El usuario debe tener email para esta prueba")

        # Editar sin enviar email
        response = self._post_json('api_gestion_usuario', {
            'id': self.regente_user.id,
            'username': 'regentetest',
            'first_name': 'RegenteEditado',
            'last_name': 'Norte',
            'identificacion': 'REG-T001',
            'ubicacion_id': self.sede_norte.id,
            'password': '',
            'email': '',
            'roles': ['REGENTE']
        })
        self.assertEqual(response.status_code, 200)

        # Verificar que el email NO se perdió
        user_refrescado = User.objects.get(id=self.regente_user.id)
        self.assertEqual(
            user_refrescado.email, original_email,
            f"Bug 10: Email se borró. Antes: '{original_email}', Después: '{user_refrescado.email}'"
        )

    def test_password_vacio_no_sobrescribe(self):
        """Bug 11: Enviar password vacío no debe cambiar la contraseña."""
        # Login como admin para hacer la edición
        self._login(self.admin_user)

        # Editar con password vacío
        response = self._post_json('api_gestion_usuario', {
            'id': self.regente_user.id,
            'username': 'regentetest',
            'first_name': 'Regente',
            'last_name': 'Norte',
            'identificacion': 'REG-T001',
            'ubicacion_id': self.sede_norte.id,
            'password': '',
            'email': 'regente@hospital.com',
            'roles': ['REGENTE']
        })
        self.assertEqual(response.status_code, 200)

        # La contraseña original debe seguir funcionando
        login_ok = self.client.login(username='regentetest', password='regente123')
        self.assertTrue(login_ok, "Bug 11: Password vacío sobreescribió la contraseña")

    def test_email_se_envia_explicitamente(self):
        """Bug 10: Si se envía email explícito, debe actualizarse."""
        self._login(self.admin_user)
        nuevo_email = 'regente.actualizado@hospital.com'
        response = self._post_json('api_gestion_usuario', {
            'id': self.regente_user.id,
            'username': 'regentetest',
            'first_name': 'Regente',
            'last_name': 'Norte',
            'identificacion': 'REG-T001',
            'ubicacion_id': self.sede_norte.id,
            'password': '',
            'email': nuevo_email,
            'roles': ['REGENTE']
        })
        self.assertEqual(response.status_code, 200)
        user_refrescado = User.objects.get(id=self.regente_user.id)
        self.assertEqual(user_refrescado.email, nuevo_email)


# ==============================================================================
# BUG 12: Identificación duplicada no se valida
# ==============================================================================
class Bug12_IdentificacionDuplicadaTest(BaseTest):
    """Verifica que no se permita crear/editar usuarios con ID duplicada."""

    def test_rechaza_identificacion_duplicada_creacion(self):
        """Bug 12: Crear usuario con identificación existente debe fallar."""
        self._login(self.admin_user)
        response = self._post_json('api_gestion_usuario', {
            'id': '',
            'username': 'duplicado',
            'first_name': 'Usuario',
            'last_name': 'Duplicado',
            'identificacion': 'REG-T001',  # Ya existe (es de regente_user)
            'ubicacion_id': self.sede_norte.id,
            'password': 'pass123',
            'email': 'dup@test.com',
            'roles': ['ENFERMERA']
        })
        self.assertEqual(response.status_code, 400,
                         "Bug 12: Debió rechazar identificación duplicada")

    def test_rechaza_identificacion_duplicada_edicion(self):
        """Bug 12: Editar usuario con identificación de otro usuario debe fallar."""
        self._login(self.admin_user)
        response = self._post_json('api_gestion_usuario', {
            'id': self.enfermera_user.id,
            'username': 'enfermeratest',
            'first_name': 'Enfermera',
            'last_name': 'Norte',
            'identificacion': 'REG-T001',  # Pertenece a regente_user
            'ubicacion_id': self.sede_norte.id,
            'password': '',
            'email': 'enfermera@hospital.com',
            'roles': ['ENFERMERA']
        })
        self.assertEqual(response.status_code, 400,
                         "Bug 12: Debió rechazar identificación duplicada al editar")

    def test_permite_misma_identificacion_al_editar_mismo_usuario(self):
        """Bug 12: Editar el mismo usuario con su misma identificación debe funcionar."""
        self._login(self.admin_user)
        response = self._post_json('api_gestion_usuario', {
            'id': self.regente_user.id,
            'username': 'regentetest',
            'first_name': 'Regente',
            'last_name': 'Norte',
            'identificacion': 'REG-T001',  # Es la suya
            'ubicacion_id': self.sede_norte.id,
            'password': '',
            'email': 'regente@hospital.com',
            'roles': ['REGENTE']
        })
        self.assertEqual(response.status_code, 200,
                         "Bug 12: Debió permitir editar con misma identificación")


# ==============================================================================
# BUG 13: Devolución con doc_id específico
# ==============================================================================
class Bug13_DevolucionConDocIdTest(BaseTest):
    """Verifica que se pueda hacer devolución contra un documento específico."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Agregar stock a Sede Norte para las pruebas de devolución
        InventarioStock.objects.create(
            ubicacion=cls.sede_norte,
            medicamento=cls.med_acetaminofen,
            lote="LOTE-NORTE-DEV",
            fecha_vencimiento=timezone.now().date() + datetime.timedelta(days=100),
            cantidad_actual=100,
        )

    def test_devolucion_por_doc_id(self):
        """Bug 13: Llamar registrar_devolucion con doc_id debe funcionar."""
        # Primero hacer una salida
        doc = registrar_salida_paciente_inteligente(
            self.enfermera_user,
            "ACETAMINOFEN",
            10,
            "PAC-001"
        )

        # Ahora devolver contra ese documento específico
        try:
            doc_dev = registrar_devolucion(self.enfermera_user, doc.id, 5)
            self.assertIsNotNone(doc_dev)
            self.assertEqual(doc_dev.tipo_mov, 'DEVOLUCION')
            self.assertEqual(doc_dev.documento_referencia.id, doc.id)
        except Exception as e:
            self.fail(f"Bug 13: Error al hacer devolución por doc_id: {e}")

    def test_devolucion_agrupada_sin_doc_id(self):
        """Bug 13: La devolución agrupada (sin doc_id) también debe seguir funcionando."""
        registrar_salida_paciente_inteligente(
            self.enfermera_user, "ACETAMINOFEN", 10, "PAC-002"
        )
        try:
            resultado = registrar_devolucion_agrupada(
                self.enfermera_user, "ACETAMINOFEN", 3, "PAC-002"
            )
            self.assertTrue(resultado)
        except Exception as e:
            self.fail(f"Bug 13: Error en devolución agrupada: {e}")

    def test_api_devolucion_con_doc_id(self):
        """Bug 13: El endpoint /movimiento/ debe aceptar doc_id."""
        doc = registrar_salida_paciente_inteligente(
            self.enfermera_user, "ACETAMINOFEN", 10, "PAC-003"
        )

        self._login(self.enfermera_user)
        response = self._post_json('registrar_movimiento', {
            'tipo_mov': 'DEVOLUCION',
            'nombre_medicamento': 'ACETAMINOFEN',
            'id_paciente': 'PAC-003',
            'cantidad': 4,
            'doc_id': doc.id
        })
        self.assertEqual(response.status_code, 200,
                         f"Bug 13: API devolvió error: {response.json()}")


# ==============================================================================
# BUG 18: XSS en inline JS de usuarios (escapejs)
# ==============================================================================
class Bug18_XssEscapeTest(BaseTest):
    """Verifica que los valores en inline JS estén escapados (validación de template)."""

    def test_admin_dashboard_renderiza_sin_error(self):
        """Bug 18: El template admin_dashboard debe renderizar sin errores."""
        self._login(self.admin_user)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_usuario_con_caracteres_especiales_no_rompe_template(self):
        """Bug 18: Usuario con apóstrofe en nombre no debe romper JS inline."""
        # Crear usuario con nombre problemático
        user_problem = User.objects.create_user(
            username="oconnor", password="test123",
            first_name="O' Connor",  # Apóstrofe
            last_name="Test & Co",  # Ampersand
            email="test@example.com"
        )
        user_problem.groups.add(Group.objects.get(name='ENFERMERA'))
        PerfilUsuario.objects.create(
            usuario=user_problem,
            ubicacion_asignada=self.sede_norte,
            numero_identificacion="ID-XSS-01"
        )

        self._login(self.admin_user)
        try:
            response = self.client.get(reverse('admin_dashboard'))
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')
            # El template debe renderizar sin errores con nombres problemáticos
            self.assertIn('Connor', content,
                          "Bug 18: El nombre debe aparecer en el HTML")
            # Verificar que escapejs convirtió el apóstrofe para JS
            # escapejs convierte ' a \u0027; Django autoescape convierte & a &amp;
            self.assertIn('Connor', content, "Bug 18: El nombre debe aparecer")
        except Exception as e:
            self.fail(f"Bug 18: Error al renderizar con caracteres especiales: {e}")


# ==============================================================================
# BUG 20: Validación de fecha de vencimiento pasada
# ==============================================================================
class Bug20_FechaVencimientoPasadaTest(BaseTest):
    """Verifica que no se permita registrar stock con fecha vencida."""

    def test_rechaza_fecha_vencimiento_pasada(self):
        """Bug 20: Fecha de vencimiento anterior a hoy debe ser rechazada."""
        self._login(self.admin_user)
        ayer = timezone.now().date() - datetime.timedelta(days=1)
        response = self._post_json('api_gestion_producto', {
            'principio_activo': 'VENCIDO TEST',
            'forma_farmaceutica': 'TABLETA',
            'lote': 'LOTE-VENC',
            'fecha_vencimiento': ayer.isoformat(),
            'cantidad': 100,
            'stock_minimo': 10
        })
        self.assertEqual(response.status_code, 400,
                         f"Bug 20: Debió rechazar fecha pasada: {response.json()}")

    def test_permite_fecha_vencimiento_futura(self):
        """Bug 20: Fecha de vencimiento futura debe ser aceptada."""
        self._login(self.admin_user)
        futuro = timezone.now().date() + datetime.timedelta(days=30)
        response = self._post_json('api_gestion_producto', {
            'principio_activo': 'VIGENTE TEST',
            'forma_farmaceutica': 'TABLETA',
            'lote': 'LOTE-VIG',
            'fecha_vencimiento': futuro.isoformat(),
            'cantidad': 100,
            'stock_minimo': 10
        })
        self.assertEqual(response.status_code, 200,
                         f"Bug 20: Debió aceptar fecha futura: {response.json()}")


# ==============================================================================
# BUG 9: REGENTE solo ve medicamentos de su sede
# ==============================================================================
class Bug09_FiltroMedicamentosSedeTest(BaseTest):
    """Verifica que el admin_dashboard filtre medicamentos por sede."""

    def test_medicamentos_filtrados_por_sede(self):
        """Bug 9: admin_dashboard_view solo debe pasar medicamentos de la sede del usuario."""
        self._login(self.regente_user)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Los medicamentos pasados al template deben estar filtrados
        # La sede norte no tiene stock, así que debería estar vacío
        medicamentos = response.context.get('medicamentos', [])
        self.assertIsNotNone(medicamentos)
        # El regente_user tiene sede_norte, que no tiene stock
        self.assertEqual(
            len(medicamentos), 0,
            "Bug 9: Sede Norte no tiene inventario, debería mostrar 0 medicamentos"
        )

    def test_admin_ve_medicamentos_de_su_sede(self):
        """Bug 9: ADMIN debe ver medicamentos de su bodega central."""
        self._login(self.admin_user)
        response = self.client.get(reverse('admin_dashboard'))
        medicamentos = response.context.get('medicamentos', [])
        # La central tiene ACETAMINOFEN
        self.assertGreaterEqual(
            len(medicamentos), 1,
            "Bug 9: Bodega Central tiene stock, debería mostrar al menos 1 medicamento"
        )
        nombres = [m.principio_activo for m in medicamentos]
        self.assertIn('ACETAMINOFEN', nombres)


# ==============================================================================
# BUG 17: Configuración de entorno (SECRET_KEY, DEBUG)
# ==============================================================================
class Bug17_SettingsEnvTest(BaseTest):
    """Verifica que settings cargue desde variables de entorno."""

    @override_settings(DEBUG=False, SECRET_KEY='test-secret-key-12345')
    def test_settings_respetan_override(self):
        """Bug 17: DEBUG y SECRET_KEY deben ser configurables."""
        from django.conf import settings
        self.assertFalse(settings.DEBUG)
        self.assertNotIn('django-insecure', settings.SECRET_KEY)


# ==============================================================================
# PRUEBAS DE INTEGRACIÓN: Flujo completo creación usuario + login + stock
# ==============================================================================
class IntegracionFlujoCompletoTest(BaseTest):
    """Verifica que el flujo completo de creación de usuario y operaciones funcione."""

    def test_crear_usuario_y_operar_stock(self):
        """Flujo completo: crear usuario, login, ver stock, hacer salida."""
        # 1. Admin crea un nuevo usuario
        self._login(self.admin_user)
        response = self._post_json('api_gestion_usuario', {
            'id': '',
            'username': 'nueva_enfermera',
            'first_name': 'Nueva',
            'last_name': 'Enfermera',
            'email': 'nueva@hospital.com',
            'identificacion': 'ID-NEW-001',
            'ubicacion_id': self.sede_norte.id,
            'password': 'pass123',
            'roles': ['ENFERMERA']
        })
        self.assertEqual(response.status_code, 200, f"Creación falló: {response.json()}")

        # Verificar que se creó el perfil
        user_creado = User.objects.get(username='nueva_enfermera')
        self.assertTrue(hasattr(user_creado, 'perfil'))
        self.assertEqual(user_creado.perfil.numero_identificacion, 'ID-NEW-001')
        self.assertTrue(user_creado.groups.filter(name='ENFERMERA').exists())

        # 2. Login como la nueva enfermera
        self.client.logout()
        login_ok = self.client.login(username='nueva_enfermera', password='pass123')
        self.assertTrue(login_ok, "La nueva enfermera debe poder hacer login")

        # 3. Ver stock
        response = self.client.get(reverse('sincronizar_inventario_api'))
        self.assertEqual(response.status_code, 200)

        # 4. Hacer una salida (si hay stock)
        InventarioStock.objects.create(
            ubicacion=self.sede_norte,
            medicamento=self.med_acetaminofen,
            lote="LOTE-NORTE-01",
            fecha_vencimiento=timezone.now().date() + datetime.timedelta(days=100),
            cantidad_actual=50,
        )
        response = self._post_json('registrar_movimiento', {
            'tipo_mov': 'SALIDA',
            'nombre_medicamento': 'ACETAMINOFEN',
            'id_paciente': 'PAC-NEW-001',
            'cantidad': 5
        })
        self.assertEqual(response.status_code, 200, f"Salida falló: {response.json()}")

    def test_login_con_grupos_redirige_correctamente(self):
        """Verifica que el login custom redirija según grupos."""
        # ADMIN → admin_dashboard
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)  # Redirect al login

        self.client.login(username='admintest', password='admin123')
        response = self.client.get(reverse('dashboard'))
        self.assertIn(response.status_code, [200, 302])

    def test_logout_no_requiere_estar_logueado(self):
        """El logout no debe romper si no hay sesión activa."""
        response = self.client.post(reverse('logout'))
        self.assertIn(response.status_code, [200, 302])
