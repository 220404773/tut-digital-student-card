# Consolidated initial migration. accounts previously carried 12 migrations
# (0001-0012) that took the model from StudentUser through several field
# additions to a rename to User - AUTH_USER_MODEL resolves against the
# *current* setting value everywhere, including when Django rebuilds a
# historical pre-rename state for its own bookkeeping, so a plain forward
# RenameModel migration cannot coexist with history that predates it. This
# single migration defines User (and DeletedStudentLog) at their final
# shape directly, avoiding that limitation entirely. Safe to squash here
# specifically because no production deployment exists yet and the local
# dev database is disposable.

import django.contrib.auth.models
import django.contrib.auth.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('username', models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('student_number', models.CharField(max_length=20, unique=True)),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('date_of_birth', models.DateField(blank=True, null=True)),
                ('gender', models.CharField(blank=True, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], max_length=1)),
                ('registration_proof', models.FileField(blank=True, null=True, upload_to='registration_proofs/')),
                ('is_verified', models.BooleanField(default=False)),
                ('profile_picture', models.ImageField(blank=True, null=True, upload_to='profile_pictures/')),
                ('email_confirmed', models.BooleanField(default=False)),
                ('email_confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('consent_given_at', models.DateTimeField(blank=True, null=True)),
                ('registration_proof_hash', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('registration_end_date', models.DateField(blank=True, null=True)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
            ],
            options={
                'ordering': ['-date_joined'],
            },
            managers=[
                ('objects', django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name='DeletedStudentLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('student_number', models.CharField(max_length=20)),
                ('full_name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('was_verified', models.BooleanField()),
                ('had_card', models.BooleanField()),
                ('card_number', models.CharField(blank=True, default='', max_length=20)),
                ('registration_end_date', models.DateField(blank=True, null=True)),
                ('was_email_confirmed', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(auto_now_add=True)),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.user')),
                ('restored_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-deleted_at'],
            },
        ),
    ]
