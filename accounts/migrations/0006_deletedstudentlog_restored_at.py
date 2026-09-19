# No-op - superseded by the consolidated 0001_initial.py, which already
# defines User/DeletedStudentLog at the shape this migration used to build
# toward. Kept in place (rather than deleted) purely to preserve the
# dependency chain's file numbering; it makes no schema change of its own.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_deletedstudentlog'),
    ]

    operations = []
