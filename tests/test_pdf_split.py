from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import algocode_cli as cli


class TaskRangesTests(unittest.TestCase):
    def test_default_directory_uses_parent_name_and_avoids_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli.Path, 'cwd', return_value=Path(directory)):
            first = cli.default_download_directory()
            second = cli.default_download_directory()
            self.assertEqual(first, Path(directory) / f'tasks-{Path(directory).name}')
            self.assertEqual(second.name, f'tasks-{Path(directory).name}-1')

    def test_multipage_tasks_and_last_task(self):
        texts = [
            'Кружок\nЗадача A. Числа\nУсловие',
            'Кружок\nПродолжение условия A',
            'Кружок\nЗадача B. Строки\nУсловие',
            'Примеры для B',
            'Задача C. Графы\nУсловие',
        ]
        self.assertEqual(cli.task_ranges(texts), [('a', 0, 2), ('b', 2, 4), ('c', 4, 5)])

    def test_repeated_heading_belongs_to_same_task(self):
        texts = ['Задача A. Числа', 'Задача A. Числа (продолжение)', 'Задача B. Строки']
        self.assertEqual(cli.task_ranges(texts), [('a', 0, 2), ('b', 2, 3)])

    def test_cover_page_and_blank_continuation(self):
        texts = ['Обложка', 'Problem A. Numbers', None, '', 'Problem B. Strings']
        self.assertEqual(cli.task_ranges(texts), [('a', 1, 4), ('b', 4, 5)])

    def test_headings_without_spaces_and_body_reference(self):
        texts = ['ЗадачаA. Числа\nСм. также Задача B. Строки', 'Задача B. Строки']
        self.assertEqual(cli.task_ranges(texts), [('a', 0, 1), ('b', 1, 2)])

    def test_unrecognized_headings_fail(self):
        for texts in [[], [''], ['Условие без заголовка']]:
            with self.subTest(texts=texts), self.assertRaisesRegex(ValueError, 'Не найдены заголовки'):
                cli.task_ranges(texts)

    def test_two_tasks_starting_on_one_page_fail(self):
        with self.assertRaisesRegex(ValueError, 'несколько задач'):
            cli.task_ranges(['Задача A. Числа\nЗадача B. Строки'])

    def test_nonconsecutive_duplicate_fails(self):
        with self.assertRaisesRegex(ValueError, 'повторно'):
            cli.task_ranges(['Problem A. Numbers', 'Problem B. Strings', 'Problem A. Numbers'])

    def test_download_menu_splits_downloaded_file(self):
        contest = cli.Contest('Графы', None, 'https://algocode.ru/conditions.pdf')
        source = Path('/downloads/contest.pdf')
        output = io.StringIO()
        with patch('builtins.input', side_effect=['1', '0']), \
             patch.object(cli, 'download', return_value=source), \
             patch.object(cli, 'split_pdf', return_value=[source.parent / 'contest/a.pdf']) as split, \
             patch.object(cli.Path, 'unlink') as unlink, \
             redirect_stdout(output):
            cli.contest_menu(contest, source.parent)
        split.assert_called_once_with(source)
        unlink.assert_called_once_with()
        self.assertIn('contest/a.pdf', output.getvalue())


@unittest.skipUnless(importlib.util.find_spec('pypdf'), 'Требуется pypdf: установите requirements.txt')
class PdfIntegrationTests(unittest.TestCase):
    def test_download_menu_removes_source_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'contest.pdf'
            contest = cli.Contest('Графы', None, 'https://algocode.ru/conditions.pdf')
            for title, success in [('Problem A. Numbers', True), ('No headings', False)]:
                self.make_pdf(source, [title])
                with patch('builtins.input', side_effect=['1', '0']), \
                     patch.object(cli, 'download', return_value=source), \
                     patch.object(cli, 'default_download_directory', return_value=folder), \
                     redirect_stdout(io.StringIO()), patch('sys.stderr', new_callable=io.StringIO):
                    cli.contest_menu(contest, None)
                self.assertEqual(source.exists(), not success)
                if success:
                    self.assertTrue((folder / 'a.pdf').exists())

    def make_pdf(self, path, texts):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        with PdfWriter() as writer:
            for text in texts:
                page = writer.add_blank_page(width=595, height=842)
                font = DictionaryObject({
                    NameObject('/Type'): NameObject('/Font'),
                    NameObject('/Subtype'): NameObject('/Type1'),
                    NameObject('/BaseFont'): NameObject('/Helvetica'),
                })
                page[NameObject('/Resources')] = DictionaryObject({
                    NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})
                })
                stream = DecodedStreamObject()
                stream.set_data(f'BT /F1 12 Tf 50 750 Td ({text}) Tj ET'.encode('ascii'))
                page[NameObject('/Contents')] = writer._add_object(stream)
            with path.open('wb') as output:
                writer.write(output)

    def test_real_pdf_preserves_multipage_tasks_and_source(self):
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'contest.pdf'
            self.make_pdf(source, ['Problem A. Numbers', 'Continuation A', 'Problem B. Strings', 'Continuation B'])
            original = source.read_bytes()
            paths = cli.split_pdf(source)
            self.assertEqual([p.name for p in paths], ['a.pdf', 'b.pdf'])
            self.assertEqual([len(PdfReader(p).pages) for p in paths], [2, 2])
            self.assertIn('Continuation A', PdfReader(paths[0]).pages[1].extract_text())
            self.assertIn('Continuation B', PdfReader(paths[1]).pages[1].extract_text())
            self.assertEqual(source.read_bytes(), original)
            second = cli.split_pdf(source)
            self.assertNotEqual(paths[0].parent, second[0].parent)
            self.assertTrue(paths[0].exists())

    def test_unrecognized_pdf_creates_no_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'contest.pdf'
            self.make_pdf(source, ['No headings'])
            with self.assertRaises(ValueError):
                cli.split_pdf(source)
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_split_directly_beside_source_and_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'contest.pdf'
            self.make_pdf(source, ['Problem A. Numbers', 'Problem B. Strings'])
            existing = folder / 'b.pdf'
            existing.write_bytes(b'existing task')
            with self.assertRaises(FileExistsError):
                cli.split_pdf(source, destination=folder)
            self.assertTrue(source.exists())
            self.assertFalse((folder / 'a.pdf').exists())
            self.assertEqual(existing.read_bytes(), b'existing task')
            existing.unlink()
            paths = cli.split_pdf(source, destination=folder)
            self.assertEqual(paths, [folder / 'a.pdf', folder / 'b.pdf'])

    def test_write_failure_cleans_output_directory(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'contest.pdf'
            self.make_pdf(source, ['Problem A. Numbers', 'Problem B. Strings'])
            with patch.object(PdfWriter, 'write', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):
                    cli.split_pdf(source)
            self.assertEqual(list(Path(directory).iterdir()), [source])


if __name__ == '__main__':
    unittest.main()
