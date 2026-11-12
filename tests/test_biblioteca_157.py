"""Regression tests: ZimaOS saves media under Biblioteca without changing container paths."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BibliotecaPathsTest(unittest.TestCase):
    def test_all_compose_paths(self):
        for fn in ('compose.yaml','zimaos-compose.example.yaml'):
            s=(ROOT/fn).read_text()
            self.assertIn('/Biblioteca/Musicas:/music',s)
            self.assertIn('/Biblioteca/Videos:/videos',s)
            self.assertIn('/dados:/config',s)
            self.assertNotIn('/Musica:/music',s)
            self.assertNotIn('/Videos:/videos',s.replace('/Biblioteca/Videos:/videos',''))
    def test_initializer_uses_same_location(self):
        s=(ROOT/'iniciar.sh').read_text()
        self.assertIn('MUSIC_PATH="${LIBRARY_ROOT}/Biblioteca/Musicas"',s)
        self.assertIn('VIDEO_PATH="${LIBRARY_ROOT}/Biblioteca/Videos"',s)
    def test_migration_copies_legacy_paths_without_deleting(self):
        s=(ROOT/'migrar-biblioteca.sh').read_text()
        self.assertIn('cp -a -n',s)
        self.assertIn('source_dir/$old',s)
        self.assertIn('Biblioteca/Musicas',s)
        self.assertIn('Biblioteca/Videos',s)
        for script in (ROOT/'migrar-biblioteca.sh',ROOT/'criar-pastas-zimaos.sh', ROOT/'iniciar.sh'):
            p=subprocess.run(['bash','-n',str(script)],capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr.decode())

if __name__=='__main__': unittest.main()
