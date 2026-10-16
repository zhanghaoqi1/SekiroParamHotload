import os
from pathlib import Path
import subprocess
import tempfile
import unittest


EXE = Path(__file__).resolve().parents[1]/'build/native-tests/validation_test.exe'


@unittest.skipUnless(os.name == 'nt' and EXE.is_file(), 'Build native/test-validation.ps1 first')
class NativeValidation(unittest.TestCase):
    def test_long_string_area_and_malformed_type_names(self):
        result = subprocess.run([str(EXE)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS',result.stdout)

    def test_rejected_user_journal_snapshots(self):
        import base64
        import json
        import struct
        root = EXE.parents[2]
        # The actual failure record remains local; never bundle game data as fixtures.
        records = sorted((root/'state/journals').glob('*-native-*.json'),reverse=True)
        found = set()
        with tempfile.TemporaryDirectory() as folder:
            for path in records:
                if path.name.endswith('.result.json'):
                    continue
                doc = json.loads(path.read_text(encoding='utf-8'))
                for table in doc.get('tables',[]):
                    name = table['name']
                    if name not in ('BehaviorParam','Bullet') or name in found:
                        continue
                    data = base64.b64decode(table['before'])
                    size = struct.unpack_from('<I',data)[0]
                    type_offset = struct.unpack_from('<Q',data,16+0x10)[0]
                    if size-type_offset <= 65536:
                        continue
                    fixture = Path(folder)/(name+'.bin')
                    fixture.write_bytes(data)
                    result = subprocess.run([str(EXE),str(fixture),str(table['width'])],capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,name+': '+result.stdout+result.stderr)
                    found.add(name)
                if len(found) == 2:
                    break
        if not found:
            self.skipTest('Local failure journals not available')
        self.assertEqual(found,{'BehaviorParam','Bullet'})
