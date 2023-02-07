# Generated by Django 3.2.16 on 2023-02-05 08:31

import django_enum.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ExampleModel',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('define_field', models.CharField(choices=[('D1', 'Define 1'), ('D2', 'Define 2'), ('D3', 'Define 3')], max_length=2)),
                ('color', django_enum.fields.EnumCharField(choices=[('R', 'Red'), ('G', 'Green'), ('B', 'Blue')], default=None, max_length=1, null=True)),
                ('style', django_enum.fields.EnumPositiveSmallIntegerField(choices=[(1, 'Streets'), (2, 'Outdoors'), (3, 'Light'), (4, 'Dark'), (5, 'Satellite'), (6, 'Satellite Streets'), (7, 'Navigation Day'), (8, 'Navigation Night')], default=1)),
            ],
        ),
    ]