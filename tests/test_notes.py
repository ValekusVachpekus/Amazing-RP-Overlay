import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from amazing_overlay.notes import Notes, EDIT_LIMIT, workspace


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.values = {}
        self.notes = Notes(self.root, self.root / 'data', lambda: self.values)

    def call(self, method, **params):
        return self.notes.dispatch('notes_' + method, params)

    def test_crud_atomic_save_search_trash_restore(self):
        self.call('create', path='Мои записи', folder=True)
        created = self.call('create', path='Мои записи/Закон.txt', text='Привет\nСтатья 12')
        saved = self.call('save', path=created['path'], text='Обновлено\nСТАТЬЯ 12', revision=created['revision'])
        self.assertFalse(saved['conflict'])
        self.assertNotEqual(saved['revision'], created['revision'])
        self.assertEqual(self.call('search', query='статья')['entries'][0]['path'], created['path'])
        self.call('move', path=created['path'], destination='Мои записи/Другая.txt')
        trash = self.call('delete', path='Мои записи')
        self.assertEqual(self.call('list')['entries'], [])
        self.assertEqual(self.call('trash')[0]['path'], 'Мои записи')
        self.call('restore', id=trash['trash_id'])
        self.assertEqual(self.call('read', path='Мои записи/Другая.txt')['text'], saved['text'])
        self.assertFalse(list((self.root / 'Notes').glob('.note-*')))

    def test_external_edit_and_two_clients_never_overwrite(self):
        first = self.call('create', path='Запись.txt', text='исходник')
        second = self.call('read', path='Запись.txt')
        self.call('save', path=first['path'], text='изменение 1', revision=first['revision'])
        conflict = self.call('save', path=second['path'], text='изменение 2', revision=second['revision'])
        self.assertTrue(conflict['conflict'])
        self.assertEqual(conflict['current']['text'], 'изменение 1')
        (self.root / 'Notes/Запись.txt').write_text('внешний редактор', encoding='utf-8')
        again = self.call('save', path=second['path'], text='не перезаписывать', revision=second['revision'])
        self.assertTrue(again['conflict'])
        self.assertEqual(self.call('read', path=second['path'])['text'], 'внешний редактор')

    def test_failure_preserves_last_file(self):
        value = self.call('create', path='Заметка.txt', text='последняя версия')
        with patch('amazing_overlay.notes.os.replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                self.call('save', path=value['path'], text='новый текст', revision=value['revision'])
        self.assertEqual(self.call('read', path=value['path'])['text'], value['text'])
        self.assertEqual(list((self.root / 'Notes').glob('.note-*')), [])

    def test_windows_names_and_traversal(self):
        for name in ('../outside.txt', '/outside.txt', 'a//b.txt', 'C:/outside.txt', 'a\\b.txt',
                     'CON.txt', 'nul', 'LPT1.txt', 'a/../../outside.txt', '.trash/item.txt', 'note?.txt', 'file.txt ', 'a./x.txt'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.call('create', path=name)
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_symlink_and_junction_escape(self):
        self.call('info')
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'Notes/link'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('symlinks unavailable')
            import subprocess
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest('junction unavailable')
        try:
            with self.assertRaises(ValueError):
                self.call('create', path='link/escape.txt')
            self.assertEqual(self.call('list')['entries'], [])
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_encodings_newlines_and_size(self):
        self.call('info')
        path = self.root / 'Notes/Русский.txt'
        for encoding in ('utf-8', 'utf-8-sig', 'cp1251'):
            with self.subTest(encoding=encoding):
                path.write_bytes('Привет\r\nЁж'.encode(encoding))
                value = self.call('read', path=path.name)
                self.assertEqual(value['text'], 'Привет\nЁж')
                self.call('save', path=path.name, text='Закон\nЁж', revision=value['revision'])
                self.assertEqual(path.read_bytes(), 'Закон\r\nЁж'.encode(encoding))
        path.write_bytes(b'a' * (EDIT_LIMIT + 1))
        value = self.call('read', path=path.name)
        self.assertTrue(value['readonly'])
        self.assertEqual(len(value['text']), EDIT_LIMIT + 1)
        with self.assertRaises(ValueError):
            self.call('save', path=path.name, text='short', revision=value['revision'])
        with self.assertRaises(ValueError):
            self.call('create', path='too large.txt', text='x' * (EDIT_LIMIT + 1))
        path.write_bytes(b'bad\x00data')
        with self.assertRaises(ValueError):
            self.call('read', path=path.name)

    def test_no_accidental_overwrite_move_or_restore(self):
        self.call('create', path='a.txt', text='a')
        self.call('create', path='b.txt', text='b')
        with self.assertRaises(FileExistsError):
            self.call('create', path='a.txt')
        with self.assertRaises(ValueError):
            self.call('move', path='a.txt', destination='b.txt')
        trash = self.call('delete', path='a.txt')
        self.call('create', path='a.txt', text='new')
        with self.assertRaises(ValueError):
            self.call('restore', id=trash['trash_id'])
        self.call('restore', id=trash['trash_id'], destination='recovered.txt')
        self.assertEqual(self.call('read', path='a.txt')['text'], 'new')
        self.assertEqual(self.call('read', path='recovered.txt')['text'], 'a')

    def test_folder_change_does_not_move_data_and_is_cwd_independent(self):
        self.call('create', path='original.txt', text='personal')
        previous = Path.cwd()
        try:
            os.chdir(self.root.parent)
            self.assertEqual(self.notes.root, self.root / 'Notes')
        finally:
            os.chdir(previous)
        self.values['notes_dir'] = str(self.root / 'Other notes')
        self.assertEqual(self.call('list')['entries'], [])
        self.assertTrue((self.root / 'Notes/original.txt').exists())
        self.assertEqual(workspace(self.root / 'Portable'), self.root / 'Portable')
        (self.root / 'amazing_overlay').mkdir()
        (self.root / 'start.bat').touch()
        self.assertEqual(workspace(self.root / 'Release'), self.root)

    def test_folder_change_rejects_saving_an_old_buffer_into_new_root(self):
        value = self.call('create', path='same.txt', text='original')
        previous = str(self.notes.root)
        self.values['notes_dir'] = str(self.root / 'Other')
        self.call('create', path='same.txt', text='original')
        with self.assertRaises(ValueError):
            self.call('save', path='same.txt', text='wrong folder', revision=value['revision'], root=previous)
        self.assertEqual(self.call('read', path='same.txt')['text'], 'original')


if __name__ == '__main__':
    unittest.main()
