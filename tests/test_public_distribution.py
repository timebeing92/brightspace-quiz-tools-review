"""Distribution-level checks: reviewers can reproduce the pinned source suite."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import make_release_asset as release


def test_release_archive_carries_tests_and_complete_vendor_pin(tmp_path):
    pin = release.load_pin()
    release.verify_pin(pin)
    files = release.release_files(pin)
    assert set(p['target'] for p in pin['files']) <= set(files)
    assert {'tests/conftest.py', 'tests/test_quiz_terminal_roundtrip.py',
            'tests/test_quiz_sme_edit_scenarios.py', 'tests/README.md'} <= set(files)
    archive = tmp_path / 'review.tar.gz'
    manifest = release.release_manifest('test', 'synthetic', pin, files)
    release.build_archive(archive, 'review', manifest, files, pin)
    with tarfile.open(archive) as handle:
        handle.extractall(tmp_path, filter='data')
    result = subprocess.run([sys.executable, 'scripts/vendor_from_workbench.py', '--check'],
                            cwd=tmp_path / 'review', text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    packed = json.loads((tmp_path / 'review/upstream/workbench_pin.json').read_text())
    assert len(packed['files']) == len(pin['files'])


def test_public_native_fixtures_have_no_quiz_passwords_or_course_resource_links():
    for path in (ROOT / 'tests/fixtures').rglob('*.xml'):
        for element in ET.parse(path).iter():
            if element.tag.split('}')[-1] == 'password':
                assert not (element.text or '').strip(), path
        assert 'drive.google.com/file/d/' not in path.read_text(), path
