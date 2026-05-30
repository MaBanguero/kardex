# Generated manually: remove unique constraint from Medicamento.codigo
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kardex', '0022_add_rechazo_traslado'),
    ]

    operations = [
        migrations.AlterField(
            model_name='medicamento',
            name='codigo',
            field=models.CharField(blank=True, help_text='Código ATC, CUM o interno', max_length=50, null=True),
        ),
    ]
