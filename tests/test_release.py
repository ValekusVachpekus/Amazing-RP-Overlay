"""The publisher must preserve personal notes and never add them to its manifest."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publish_release', Path(__file__).resolve().parents[1] / 'scripts/publish_release.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ReleaseTest(unittest.TestCase):
    def test_personal_files_survive_republish_and_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, release = root / 'bundle', root / 'Release'
            bundle.mkdir()
            for name in publisher.REQUIRED:
                (bundle / name).write_bytes(b'fake runtime')
            for name in ('_internal', 'licenses'):
                (bundle / name).mkdir()
                (bundle / name / 'dependency.txt').write_text('dependency')
            with patch.object(publisher, 'RELEASE', release):
                publisher.publish(bundle)
                (release / 'Notes').mkdir()
                personal = release / 'Notes/Мои записи.txt'
                personal.write_text('Личный текст', encoding='utf-8')
                (release / '.env').write_text('AI_API_KEY=private-test-key')
                publisher.publish(bundle)
                publisher.verify()
                self.assertEqual(personal.read_text(encoding='utf-8'), 'Личный текст')
                manifest = json.loads((release / 'manifest.json').read_text(encoding='utf-8'))
                self.assertFalse(any(name.startswith('Notes/') or name == '.env' for name in manifest['files']))
                with self.assertRaises(ValueError):
                    publisher.safe_path(release, 'Notes/Мои записи.txt')
