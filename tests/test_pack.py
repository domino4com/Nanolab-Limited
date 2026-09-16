"""Run with: python -m unittest discover -s tests -v"""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nanolab_pack', ROOT / 'pack.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nanolab tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sketch = self.root / 'MySketch'
        self.sketch.mkdir()
        self.code = '// saved sketch\nvoid setup() {}\nvoid loop() {}\n'
        (self.sketch / 'MySketch.ino').write_text(self.code, encoding='utf-8')
        (self.sketch / 'sketch.yaml').write_text('original: untouched\n', encoding='utf-8')
        library = self.root / 'Libraries with spaces' / 'Custom Library'
        library.mkdir(parents=True)
        self.library = library
        (library / 'Custom.h').write_text('// local modification: café\n', encoding='utf-8')
        (library / 'LICENSE').write_text('retain this license', encoding='utf-8')
        self.builder = {
            'board_platform': {'id': 'esp32:esp32', 'version': '3.3.11'},
            'build_platform': {'id': 'esp32:esp32', 'version': '3.3.11'},
            'used_libraries': [
                {'name': 'Custom Library', 'version': '1.2.3', 'install_dir': str(library), 'location': 'user'},
                {'name': 'Wire', 'version': '3.3.11', 'location': 'platform'},
            ],
        }
        self.calls = []
        for name, value in (('select_cli', ('cli', '1.5.1')), ('select_config', None), ('ide_version', '2.3.10')):
            patcher = patch.object(pack, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def compile(self, base, args, log):
        bundle = Path(args[-1])
        self.calls.append(args)
        if '--fqbn' in args:
            self.assertFalse((bundle / 'sketch.yaml').exists())
        else:
            self.assertTrue((bundle / 'sketch.yaml').is_file())
            self.assertIn('--profile', args)
        log.write_text('{}', encoding='utf-8')
        return self.builder

    def run_pack(self, *extra, compile_result=None):
        with patch.object(pack, 'compile_json', side_effect=compile_result or self.compile):
            with redirect_stdout(io.StringIO()):
                pack.main([str(self.sketch), *extra])
        return self.sketch.with_name('MySketch-nanolab')

    def test_one_argument_builds_and_verifies_portable_zip(self):
        output = self.run_pack()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual({p.name for p in output.iterdir()}, {'MySketch.zip', 'details'})
        self.assertEqual((self.sketch / 'MySketch.ino').read_text(encoding='utf-8'), self.code)
        self.assertEqual((self.sketch / 'sketch.yaml').read_text(encoding='utf-8'), 'original: untouched\n')
        with zipfile.ZipFile(output / 'MySketch.zip') as archive:
            names = archive.namelist()
            self.assertTrue(all(n.startswith('MySketch/') for n in names))
            self.assertTrue(all('\\' not in n for n in names))
            self.assertFalse(any('/logs/' in n for n in names))
            yaml = archive.read('MySketch/sketch.yaml').decode('utf-8')
            self.assertIn('dir: "data/profile-libraries/01-Custom_Library"', yaml)
            self.assertNotIn(str(self.root), yaml)
            self.assertNotIn('Wire', yaml)
            self.assertEqual(yaml.count('- platform:'), 1)
            library = 'MySketch/data/profile-libraries/01-Custom_Library'
            self.assertEqual(archive.read(library + '/Custom.h'), (self.library / 'Custom.h').read_bytes())
            self.assertIn(library + '/LICENSE', names)
            relocated = self.root / 'received somewhere else'
            archive.extractall(relocated)
            self.assertTrue((relocated / library / 'Custom.h').is_file())
        report = json.loads((output / 'details/dependency-report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['profile_compile_succeeded'])
        self.assertEqual(report['platform_libraries'][0]['name'], 'Wire')
        self.assertEqual(report['sha256']['MySketch.ino'], pack.sha256(self.sketch / 'MySketch.ino'))

    def test_repeated_runs_get_numbered_outputs(self):
        output = self.run_pack()
        original_zip = (output / 'MySketch.zip').read_bytes()
        self.run_pack()
        self.assertTrue((self.root / 'MySketch-nanolab-2/MySketch.zip').is_file())
        self.assertEqual((output / 'MySketch.zip').read_bytes(), original_zip)

    def test_discovery_failure_leaves_diagnostics_but_no_zip(self):
        def fail(base, args, log):
            log.write_text('diagnostic', encoding='utf-8')
            raise RuntimeError('discovery failed')
        with self.assertRaisesRegex(RuntimeError, 'discovery failed'):
            self.run_pack(compile_result=fail)
        output = self.root / 'MySketch-nanolab'
        self.assertEqual(list(output.glob('*.zip')), [])
        self.assertTrue((output / 'details/logs/discovery.json').exists())
        self.assertEqual((self.sketch / 'MySketch.ino').read_text(encoding='utf-8'), self.code)

    def test_verification_failure_does_not_publish_zip(self):
        def fail_second(base, args, log):
            if '--profile' in args:
                raise RuntimeError('isolated build failed')
            return self.compile(base, args, log)
        with self.assertRaisesRegex(RuntimeError, 'isolated build failed'):
            self.run_pack(compile_result=fail_second)
        self.assertFalse((self.root / 'MySketch-nanolab/MySketch.zip').exists())

    def test_no_libraries_is_supported(self):
        self.builder.pop('used_libraries')
        output = self.run_pack()
        yaml = (output / 'details/MySketch/sketch.yaml').read_text(encoding='utf-8')
        self.assertIn('libraries: []', yaml)

    def test_explicit_skip_is_recorded(self):
        output = self.run_pack('--skip-verify')
        self.assertEqual(len(self.calls), 1)
        report = json.loads((output / 'details/dependency-report.json').read_text(encoding='utf-8'))
        self.assertFalse(report['profile_compile_succeeded'])

    def test_output_inside_source_is_rejected(self):
        with redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            with self.assertRaises(SystemExit):
                pack.main([str(self.sketch), '--output', str(self.sketch / 'output')])
        self.assertFalse((self.sketch / 'output').exists())

    def test_two_libraries_with_same_folder_name_are_kept(self):
        second = self.root / 'Other collection/Custom Library'
        second.mkdir(parents=True)
        (second / 'Second.h').write_text('// second library', encoding='utf-8')
        self.builder['used_libraries'].append({'name': 'Second', 'version': '2.0', 'install_dir': str(second)})
        output = self.run_pack()
        with zipfile.ZipFile(output / 'MySketch.zip') as archive:
            self.assertIn('MySketch/data/profile-libraries/02-Custom_Library/Second.h', archive.namelist())


class DiscoveryTests(unittest.TestCase):
    def test_no_arguments_displays_help_without_cli_lookup(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), patch.object(pack, 'select_cli', side_effect=AssertionError('must not run')):
            pack.main([])
        self.assertIn('--cli', stdout.getvalue())
        self.assertIn('--skip-verify', stdout.getvalue())

    def test_windows_standard_install_locations(self):
        with patch.object(pack.shutil, 'which', return_value=None):
            home = Path('Users') / 'Example User'
            local = home / 'AppData/Local'
            programs = Path('Program Files')
            candidates = pack.cli_candidates('win32', home, {'LOCALAPPDATA': str(local), 'ProgramFiles': str(programs)})
        self.assertIn(str(local / 'Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe'), candidates)
        self.assertIn(str(local / 'Programs/arduino-ide/resources/app/lib/backend/resources/arduino-cli.exe'), candidates)
        self.assertIn(str(programs / 'Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe'), candidates)
        self.assertFalse(any(candidate == '/Applications/arduino-cli' for candidate in candidates))

    def test_macos_manual_and_bundled_cli_locations(self):
        with patch.object(pack.shutil, 'which', return_value=None):
            candidates = pack.cli_candidates('darwin', Path('/Users/Example'), {})
        self.assertIn('/Applications/arduino-cli', candidates)
        self.assertIn(str(Path('/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli')), candidates)

    def test_old_and_broken_candidates_fall_back(self):
        with tempfile.TemporaryDirectory() as td:
            files = [Path(td) / name for name in ('old', 'broken', 'good')]
            for path in files:
                path.touch()
            results = [
                pack.subprocess.CompletedProcess([], 0, '{"VersionString":"1.2.2"}', ''),
                OSError('bad CPU type'),
                pack.subprocess.CompletedProcess([], 0, '{"VersionString":"1.5.1"}', ''),
            ]
            with patch.object(pack, 'cli_candidates', return_value=[str(p) for p in files]), \
                 patch.object(pack.subprocess, 'run', side_effect=results):
                cli, version = pack.select_cli()
            self.assertEqual(Path(cli), files[2].resolve())
            self.assertEqual(version, '1.5.1')

    def test_ide_configuration_preserves_custom_sketchbook_settings(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config = home / '.arduinoIDE/arduino-cli.yaml'
            config.parent.mkdir()
            config.write_text('directories:\n  user: custom\n', encoding='utf-8')
            with patch.object(pack.Path, 'home', return_value=home):
                self.assertEqual(pack.select_config(), config)

    def test_windows_ide_version_from_package(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td) / 'Arduino IDE/resources/app'
            cli = app / 'lib/backend/resources/arduino-cli.exe'
            cli.parent.mkdir(parents=True)
            cli.touch()
            (app / 'package.json').write_text('{"version":"2.3.10"}', encoding='utf-8')
            with patch.object(pack.plistlib, 'load', side_effect=OSError):
                self.assertEqual(pack.ide_version(str(cli)), '2.3.10')


class ProcessTests(unittest.TestCase):
    def test_cli_error_json_is_reported_with_native_fix_hint(self):
        # Real child process tests JSON/log handling on Windows as well as macOS.
        with tempfile.TemporaryDirectory(prefix='cli logs ') as td:
            root = Path(td)
            fake = root / 'fake cli.py'
            fake.write_text('import json,sys\nprint(json.dumps({"success":False,"error":"ctags: bad CPU type in executable"}))\nsys.exit(1)\n', encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'native ARM fix'):
                    pack.compile_json([sys.executable, str(fake)], [], root / 'result.json')
            self.assertTrue((root / 'result.stderr').exists())

    def test_non_json_failure_keeps_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / 'fake.py'
            fake.write_text('print("not JSON")\n', encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'no valid build result'):
                    pack.compile_json([sys.executable, str(fake)], [], root / 'result.json')


class DocumentationTests(unittest.TestCase):
    def test_troubleshooting_headings_are_alphabetical(self):
        text = (ROOT / 'docs/TROUBLESHOOTING.md').read_text(encoding='utf-8')
        headings = [line[3:] for line in text.splitlines() if line.startswith('## ')]
        self.assertEqual(headings, sorted(headings, key=str.casefold))

    def test_readme_has_only_three_steps_and_no_optional_flags(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        headings = [line for line in text.splitlines() if line.startswith('## ')]
        self.assertEqual(headings, ['## Step 1: Update your code', '## Step 2: Run the Python program', '## Step 3: Send your ZIP'])
        self.assertNotIn('Serial.begin', text)
        self.assertNotIn('--', text)
        self.assertIn('nanolab@maxiq.space', text)


if __name__ == '__main__':
    unittest.main()
