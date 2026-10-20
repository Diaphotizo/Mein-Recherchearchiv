import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from .storage import atomic_json,read_json
from .paths import COURT_DATA

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_base=Path(__file__).resolve().parents[1]/'.test-tmp'
        self.temp_base.mkdir(exist_ok=True)
    def test_status_survives_sync_rename_lock(self):
        with tempfile.TemporaryDirectory(dir=self.temp_base,prefix='storage-test-') as folder:
            assert Path(folder).resolve().is_relative_to(self.temp_base.resolve())
            target=Path(folder)/'update_status.json'
            target.write_text('{"phase":"running"}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('sync lock')),patch('research_portal.storage.time.sleep'):
                atomic_json(target,{'phase':'complete'})
            self.assertEqual(read_json(target)['phase'],'complete')
    def test_catalog_never_falls_back_to_truncation(self):
        with tempfile.TemporaryDirectory(dir=self.temp_base,prefix='storage-test-') as folder:
            assert Path(folder).resolve().is_relative_to(self.temp_base.resolve())
            target=Path(folder)/'additions.json'
            target.write_text('{"documents":["old"]}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('sync lock')),patch('research_portal.storage.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(target,{'documents':['new']})
            self.assertEqual(read_json(target)['documents'],['old'])
    def test_status_reader_retries_partial_write(self):
        with patch.object(Path,'read_text',side_effect=['{','{"phase":"complete"}']),patch('research_portal.storage.time.sleep'):
            self.assertEqual(read_json('update_status.json')['phase'],'complete')

if __name__=='__main__':unittest.main()

