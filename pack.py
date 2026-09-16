#!/usr/bin/env python3
"""Compile an ESP32 sketch and create a verified ZIP for Nanolab (Python 3.9+)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

DEFAULT_FQBN = (
    'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=8M,'
    'PSRAM=disabled,FlashMode=dio,CPUFreq=240,PartitionScheme=default'
)
ESP_INDEX = 'https://espressif.github.io/arduino-esp32/package_esp32_index.json'
MIN_CLI = (1, 3, 0)
SUPPORT = 'See docs/TROUBLESHOOTING.md.'


def cli_candidates(system=None, home=None, environ=None):
    """Known locations, including both Windows Arduino IDE installers."""
    system = system or sys.platform
    home = home or Path.home()
    environ = os.environ if environ is None else environ
    executable = 'arduino-cli.exe' if system == 'win32' else 'arduino-cli'
    candidates = [shutil.which(executable), str(Path(__file__).resolve().parent / executable)]
    if system == 'darwin':
        candidates.append('/Applications/arduino-cli')
        for root in (Path('/Applications'), home / 'Applications'):
            candidates.append(str(root / 'Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli'))
    elif system == 'win32':
        local = Path(environ.get('LOCALAPPDATA') or home / 'AppData/Local')
        roots = [local / 'Programs']
        roots += [Path(environ[key]) for key in ('ProgramFiles', 'ProgramFiles(x86)')
                  if environ.get(key)]
        for root in roots:
            for app in ('Arduino IDE', 'arduino-ide'):
                candidates.append(str(root / app / 'resources/app/lib/backend/resources/arduino-cli.exe'))
    return list(dict.fromkeys(str(p) for p in candidates if p))


def select_cli(explicit=None):
    problems = []
    for candidate in ([str(Path(explicit).expanduser())] if explicit else cli_candidates()):
        executable = shutil.which(candidate) or candidate
        if not Path(executable).is_file():
            continue
        try:
            result = subprocess.run([executable, 'version', '--json'], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=15)
            if result.returncode:
                raise ValueError((result.stderr or result.stdout).strip())
            info = json.loads(result.stdout)
            version = info.get('VersionString', info.get('version', ''))
            match = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
            if not match or tuple(map(int, match.groups())) < MIN_CLI:
                raise ValueError(f'CLI {version or "unknown"}; version 1.3.0 or newer is required')
            return str(Path(executable).resolve()), version
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            problems.append(f'{candidate}: {error}')
    detail = '\n' + '\n'.join(problems) if problems else ''
    raise RuntimeError('Arduino CLI 1.3.0 or newer was not found or could not run. '
                       'Install a recent Arduino IDE or Arduino CLI.' + detail)


def ide_version(cli):
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        try:
            with (root / 'Arduino IDE.app/Contents/Info.plist').open('rb') as stream:
                return plistlib.load(stream)['CFBundleShortVersionString']
        except (OSError, KeyError, plistlib.InvalidFileException):
            pass
    # Windows IDE bundles its version in resources/app/package.json.
    executable = Path(cli)
    if executable.parent.name == 'resources' and executable.parent.parent.name == 'backend':
        try:
            return json.loads((executable.parents[3] / 'package.json').read_text(encoding='utf-8'))['version']
        except (OSError, KeyError, ValueError):
            pass
    return None


def select_config(explicit=None):
    if explicit:
        config = Path(explicit).expanduser().resolve()
        if not config.is_file():
            raise RuntimeError(f'Configuration file not found: {config}')
        return config
    # Match the IDE's sketchbook location, including redirected Documents/OneDrive.
    config = Path.home() / '.arduinoIDE/arduino-cli.yaml'
    return config if config.is_file() else None


def compile_json(base, arguments, log):
    print('Compiling; this can take several minutes...', flush=True)
    with log.open('w', encoding='utf-8') as stdout, log.with_suffix('.stderr').open('w', encoding='utf-8') as stderr:
        process = subprocess.Popen(base + ['compile', '--json'] + arguments,
                                   stdout=stdout, stderr=stderr)
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('  Compilation still running...', flush=True)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
    try:
        result = json.loads(log.read_text(encoding='utf-8', errors='replace'))
    except ValueError as error:
        raise RuntimeError(f'CLI returned no valid build result. Read {log} and {log.with_suffix(".stderr")}') from error
    if code or not result.get('success'):
        diagnostic = '\n'.join(str(result.get(key, '')) for key in ('error', 'compiler_err', 'compiler_out'))
        hint = ''
        if 'bad CPU type' in diagnostic:
            hint = '\nAn Arduino tool is Intel-only. See "Bad CPU type in executable" in the troubleshooting guide for a native ARM fix.'
        raise RuntimeError(f'Compilation failed; no ZIP created.\n{diagnostic[-6000:]}{hint}\nFull log: {log}')
    return result['builder_result']


def copy_tree(source, target, sketch=False):
    def ignore(directory, names):
        excluded = {'.git', '.DS_Store', '__pycache__'}
        if sketch and Path(directory) == source:
            excluded |= {'sketch.yaml', 'build'}
        return excluded.intersection(names)
    # Copy linked files, not links to another computer's filesystem.
    shutil.copytree(source, target, ignore=ignore, symlinks=False)


def platforms_from(builder, indices):
    platforms = {}
    for key in ('board_platform', 'build_platform'):
        platform = builder.get(key)
        if not platform:
            continue
        identifier, version = platform['id'], platform['version']
        if not version:
            raise RuntimeError('Cannot pin a platform without its version: ' + identifier)
        if identifier in platforms and platforms[identifier]['version'] != version:
            raise RuntimeError('Conflicting platform versions: ' + identifier)
        if not identifier.startswith('arduino:') and identifier not in indices:
            raise RuntimeError(f'Provide --platform-index {identifier}=https://.../package_index.json')
        platforms[identifier] = platform
    if not platforms:
        raise RuntimeError('CLI result did not identify the build platform.')
    return list(platforms.values())


def render_yaml(profile, fqbn, notes, platforms, indices, libraries):
    def quote(value):
        return json.dumps(str(value), ensure_ascii=True)
    lines = ['profiles:', f'  {profile}:', f'    notes: {quote(notes)}',
             f'    fqbn: {quote(fqbn)}', '    platforms:']
    for platform in platforms:
        identifier = platform['id']
        lines.append('      - platform: ' + quote(f"{identifier} ({platform['version']})"))
        if identifier in indices:
            lines.append('        platform_index_url: ' + quote(indices[identifier]))
    if libraries:
        lines.append('    libraries:')
        lines += ['      - dir: ' + quote(lib['bundle_path']) for lib in libraries]
    else:
        lines.append('    libraries: []')
    lines.append(f'default_profile: {profile}')
    return '\n'.join(lines) + '\n'


def new_output(sketch, explicit):
    if explicit:
        return explicit.expanduser().resolve()
    output = sketch.with_name(sketch.name + '-nanolab')
    number = 2
    while output.exists():
        output = sketch.with_name(f'{sketch.name}-nanolab-{number}')
        number += 1
    return output


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog='Normal use: python pack.py "path/to/sketch"')
    parser.add_argument('sketch', type=Path, help='Folder containing <folder-name>.ino')
    parser.add_argument('--output', type=Path, help='New output directory; defaults to <sketch>-nanolab (numbered on repeat runs)')
    parser.add_argument('--fqbn', default=DEFAULT_FQBN, help='Override the default ESP32-S3 board and menu selections')
    parser.add_argument('--profile', help='Profile name; defaults to the sketch name')
    parser.add_argument('--cli', help='Path to Arduino CLI; automatically searches PATH and standard IDE locations')
    parser.add_argument('--config-file', type=Path, help='Override automatic use of the Arduino IDE configuration')
    parser.add_argument('--platform-index', action='append', default=[], metavar='ID=URL', help='Additional platform index (repeatable)')
    parser.add_argument('--library', action='append', default=[], type=Path, help='Additional single library directory (repeatable)')
    parser.add_argument('--libraries', action='append', default=[], type=Path, help='Additional library collection (repeatable)')
    parser.add_argument('--skip-verify', action='store_true', help='Skip the second, isolated profile compile; initial compile still required')
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return
    args = parser.parse_args(arguments)
    sketch = args.sketch.expanduser().resolve()
    if not sketch.is_dir() or not (sketch / (sketch.name + '.ino')).is_file():
        parser.error('Expected a sketch folder containing <folder-name>.ino. ' + SUPPORT)
    if (sketch / 'data/profile-libraries').exists():
        parser.error('This is already a packaged sketch. Select the original source folder.')
    output = new_output(sketch, args.output)
    if output.exists():
        parser.error(f'Output already exists: {output}')
    if output == sketch or sketch in output.parents:
        parser.error('The output directory must be outside the source sketch.')
    profile = args.profile or re.sub(r'[^A-Za-z0-9_.-]', '_', sketch.name)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', profile):
        parser.error('Profile names may contain only letters, digits, dot, underscore, dash.')
    indices = {'esp32:esp32': ESP_INDEX}
    for item in args.platform_index:
        identifier, separator, url = item.partition('=')
        if not separator or not re.fullmatch(r'[^:]+:[^:]+', identifier) or not url.startswith(('https://', 'http://')):
            parser.error('--platform-index requires vendor:architecture=https://...')
        indices[identifier] = url
    cli, version = select_cli(args.cli)
    base = [cli]
    config = select_config(args.config_file)
    if config:
        base += ['--config-file', str(config)]
    print(f'Using Arduino CLI {version}: {cli}', flush=True)
    if config:
        print(f'Using Arduino settings: {config}', flush=True)
    output.mkdir(parents=True, exist_ok=False)
    details = output / 'details'
    log_dir = details / 'logs'
    log_dir.mkdir(parents=True)
    print(f'Build logs: {log_dir}', flush=True)
    # A short temporary path reduces Windows toolchain path-length problems.
    with tempfile.TemporaryDirectory(prefix='nl-') as temporary:
        bundle = Path(temporary).resolve() / sketch.name
        copy_tree(sketch, bundle, sketch=True)
        compile_args = ['--fqbn', args.fqbn]
        for flag in ('library', 'libraries'):
            for path in getattr(args, flag):
                compile_args += ['--' + flag, str(path.expanduser().resolve())]
        builder = compile_json(base, compile_args + [str(bundle)], log_dir / 'discovery.json')
        platforms = platforms_from(builder, indices)
        library_root = bundle / 'data/profile-libraries'
        libraries, platform_libraries, seen = [], [], set()
        for lib in builder.get('used_libraries', []):
            if lib.get('location') == 'platform' or lib.get('container_platform'):
                platform_libraries.append({'name': lib['name'], 'version': lib.get('version', '')})
                continue
            source = Path(lib['install_dir']).resolve()
            if source in seen:
                continue
            seen.add(source)
            folder = f'{len(libraries)+1:02d}-' + re.sub(r'[^A-Za-z0-9_.-]', '_', source.name)
            target = library_root / folder
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_tree(source, target)
            libraries.append({'name': lib['name'], 'version': lib.get('version', 'unknown'),
                              'bundle_path': target.relative_to(bundle).as_posix()})
            print(f"Bundled {lib['name']} {lib.get('version', '')}", flush=True)
        ide = ide_version(cli)
        notes = ([f'Arduino IDE {ide}'] if ide else []) + [f'Arduino CLI {version}']
        notes += [f"{'ESP32 core' if p['id'] == 'esp32:esp32' else p['id']} {p['version']}" for p in platforms]
        (bundle / 'sketch.yaml').write_text(render_yaml(profile, args.fqbn, '; '.join(notes),
                                                     platforms, indices, libraries), encoding='utf-8')
        verified = not args.skip_verify
        if verified:
            print('Verifying the isolated profile; missing platform packages will download.', flush=True)
            compile_json(base, ['--profile', profile, str(bundle)], log_dir / 'verification.json')
        manifest = {'profile': profile, 'fqbn': args.fqbn, 'cli_version': version,
                    'source_compile_succeeded': True, 'profile_compile_succeeded': verified,
                    'libraries': libraries, 'platform_libraries': platform_libraries,
                    'platforms': [{'id': p['id'], 'version': p['version']} for p in platforms],
                    'sha256': {p.relative_to(bundle).as_posix(): sha256(p)
                               for p in sorted(bundle.rglob('*')) if p.is_file()}}
        (details / 'dependency-report.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        copy_tree(bundle, details / sketch.name)
        staged_zip = details / 'package.tmp'
        with zipfile.ZipFile(staged_zip, 'w', zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle.parent).as_posix())
        archive_path = output / (sketch.name + '.zip')
        staged_zip.rename(archive_path)
    print('Both builds passed.' if verified else 'Initial build passed; isolated verification was skipped.')
    print(f'Send this ZIP to nanolab@maxiq.space:\n{archive_path}')
    print(f'Profile and diagnostics: {details}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f'Error: {error}\n{SUPPORT}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Cancelled; no ZIP created.', file=sys.stderr)
        sys.exit(130)
