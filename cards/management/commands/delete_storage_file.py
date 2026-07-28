from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """One-off cleanup command for orphaned Spaces files left over from
    before the storage-cleanup signals existed. Temporary - remove after use.
    """
    help = 'Delete a single file from the default storage backend (Spaces/S3) by its relative path.'

    def add_arguments(self, parser):
        parser.add_argument('path', type=str)

    def handle(self, *args, **options):
        path = options['path']
        if default_storage.exists(path):
            default_storage.delete(path)
            self.stdout.write(self.style.SUCCESS(f'Deleted {path}'))
        else:
            self.stdout.write(self.style.WARNING(f'{path} does not exist - nothing to delete'))
