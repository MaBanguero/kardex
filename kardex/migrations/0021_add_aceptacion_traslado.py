# Generated manually: adds acceptance fields for traslado signing
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kardex', '0020_add_alertas_habilitadas_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='documento',
            name='aceptado',
            field=models.BooleanField(default=False, verbose_name='Aceptado por enfermera'),
        ),
        migrations.AddField(
            model_name='documento',
            name='firma_nombre',
            field=models.CharField(blank=True, max_length=200, null=True, verbose_name='Nombre de quien firma'),
        ),
        migrations.AddField(
            model_name='documento',
            name='firma_cedula',
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name='Cédula de quien firma'),
        ),
        migrations.AddField(
            model_name='documento',
            name='fecha_aceptacion',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de aceptación'),
        ),
    ]
