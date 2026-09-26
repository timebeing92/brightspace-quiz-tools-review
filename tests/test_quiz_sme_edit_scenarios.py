"""Reviewer actions against either this checkout or QUIZ_TOOLS_REPO.

Set QUIZ_TOOLS_REPO to exercise the public snapshot with the same synthetic
inputs. These tests use no real course material.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

import pytest
from openpyxl import load_workbook

TEST_ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = Path(os.environ.get('QUIZ_TOOLS_REPO', TEST_ROOT)).resolve()
sys.path.insert(0, str(TOOL_ROOT / 'scripts'))
from quiz_contracts import validate_contract
from quiz_assessment_review import (
    materialize_assessment_decisions, prepare_review_packet, validate_packet,
)
from quiz_review_workbook_reingest import ReingestError
from quiz_promote_revisions import promote_revisions


@pytest.fixture(scope='module')
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp('sme-edit-scenarios')
    source = root / 'source'
    shutil.copytree(TEST_ROOT / 'tests/fixtures/quiz_source_choice_fidelity', source)
    quiz_path = source / 'quiz_d2l_choices.xml'
    tree = ET.parse(quiz_path)
    extension = ET.SubElement(tree.find('.//assessment'), 'assess_procextension')
    ET.SubElement(extension, '{http://desire2learn.com/xsd/d2lcp_v2p0}time_limit').text = '20'
    tree.write(quiz_path, encoding='utf-8', xml_declaration=True)
    extraction = root / 'extraction'
    result = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/extract_quiz_pool_review.py'),
        str(source), '--asset-mode', 'copy', '--output-dir', str(extraction)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    # Give the synthetic fixture a target draw with one plain-text question
    # and one image question for review, packaging, and later additions.
    model_path = next(extraction.glob('*.model.json'))
    model = json.loads(model_path.read_text())
    quiz = model['quizzes'][0]
    image_question = next(q for q in model['questions'] if q.get('title') == 'IMAGES')
    plain_question = next(q for q in model['questions'] if q.get('title') == 'DUPLICATE')
    image_question['identity']['permanent_code'] = 'SYN-IMAGE-Q001'
    plain_question['identity']['permanent_code'] = 'SYN-PLAIN-Q001'
    pool_key = 'cc:structure:synthetic-review:existing-pool'
    draw_key = 'cc:structure:synthetic-review:existing-draw'
    model['structures'].append({'entity_key': pool_key,
        'identity': {'strategy': 'permanent_code', 'permanent_code': 'SYNTHETIC-POOL',
                     'source_aliases': [], 'content_fingerprints': [], 'extensions': {}},
        'kind': 'pool', 'source_kind': 'synthetic reviewer test pool', 'title': 'Synthetic existing pool',
        'ordinal': 1, 'selection': {'mode': 'all', 'requested_count': None,
            'available_count': 2, 'extensions': {}},
        'source_evidence_keys': [], 'diagnostic_ids': [], 'extensions': {}})
    model['structures'].append({'entity_key': draw_key,
        'identity': {'strategy': 'permanent_code', 'permanent_code': 'SYNTHETIC-DRAW',
                     'source_aliases': [], 'content_fingerprints': [], 'extensions': {}},
        'kind': 'draw', 'source_kind': 'synthetic reviewer test draw', 'title': 'Synthetic target draw',
        'ordinal': 1000, 'selection': {'mode': 'random', 'requested_count': 1,
            'available_count': 2, 'extensions': {'points_per_question': 2}},
        'source_evidence_keys': [], 'diagnostic_ids': [], 'extensions': {}})
    model['relationships'].append({'relationship_key': 'cc:relationship:synthetic-review:draw-pool',
        'kind': 'draws_from', 'source_kind': 'synthetic review fixture',
        'from_entity_key': draw_key, 'to_entity_key': pool_key, 'status': 'resolved',
        'ordinal': 1, 'attributes': {}, 'candidates': [], 'source_evidence_keys': [],
        'diagnostic_ids': [], 'extensions': {}})
    model['relationships'].append({'relationship_key': 'cc:relationship:synthetic-review:quiz-draw',
        'kind': 'contains', 'source_kind': 'synthetic review fixture',
        'from_entity_key': quiz['entity_key'], 'to_entity_key': draw_key, 'status': 'resolved',
        'ordinal': 1000, 'attributes': {}, 'candidates': [], 'source_evidence_keys': [],
        'diagnostic_ids': [], 'extensions': {}})
    model['relationships'].append({'relationship_key': 'cc:relationship:synthetic-review:image-pool-member',
        'kind': 'member_of', 'source_kind': 'synthetic review fixture',
        'from_entity_key': image_question['entity_key'], 'to_entity_key': pool_key, 'status': 'resolved',
        'ordinal': 1, 'attributes': {}, 'candidates': [], 'source_evidence_keys': [],
        'diagnostic_ids': [], 'extensions': {}})
    model['relationships'].append({'relationship_key': 'cc:relationship:synthetic-review:plain-pool-member',
        'kind': 'member_of', 'source_kind': 'synthetic review fixture',
        'from_entity_key': plain_question['entity_key'], 'to_entity_key': pool_key,
        'status': 'resolved', 'ordinal': 2, 'attributes': {}, 'candidates': [],
        'source_evidence_keys': [], 'diagnostic_ids': [], 'extensions': {}})
    assert not validate_contract(model, mode='transform')
    model_path.write_text(json.dumps(model, indent=2) + '\n')
    packet = root / 'packet'
    prepare_review_packet(next(extraction.glob('*reviewer.xlsx')), next(extraction.glob('*.model.json')), packet)
    return packet


@pytest.fixture
def packet(template, tmp_path):
    result = tmp_path / 'packet'
    shutil.copytree(template, result)
    return result


def row_for(packet, title='DUPLICATE'):
    spec = validate_packet(packet)
    model = json.loads((packet / 'model.json').read_text())
    key = next(q['entity_key'] for q in model['questions'] if q['title'] == title)
    book = load_workbook(packet / 'reviewer_working.xlsx')
    for tab in spec['sheets']:
        sheet = book[tab['title']]
        columns = {c.value: c.column for c in sheet[2]}
        for row in tab['rows']:
            if sheet.cell(row['row'], columns['question_entity_key']).value == key:
                return book, sheet, columns, row['row'], key
    raise AssertionError('Missing synthetic question')


def collect(packet):
    return materialize_assessment_decisions(packet / 'model.json', packet / 'reviewer_baseline_DO_NOT_EDIT.xlsx',
                                            packet / 'reviewer_working.xlsx', metadata_policy='optional')


def promote(packet, overlay):
    path = packet / 'decisions.json'
    path.write_text(json.dumps(overlay))
    return promote_revisions(packet / 'model.json', path,
        TOOL_ROOT / 'workspace/reference/schemas/quiz/quiz_build_capabilities.json')


def package_candidate(packet, output, receipt, quiz_key, question_keys, asset_roots=()):
    from quiz_phase5_authorization import create_candidate_authorization

    model_path = packet / 'promoted.model.json'
    receipt_path = packet / 'promotion.receipt.json'
    model_path.write_text(json.dumps(output, indent=2, sort_keys=True) + '\n')
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    quiz_draws = {r['to_entity_key'] for r in output['relationships']
                  if r['kind'] == 'contains' and r['from_entity_key'] == quiz_key
                  and r['status'] == 'resolved'}
    pool_keys = {r['to_entity_key'] for r in output['relationships']
                 if r['kind'] == 'draws_from' and r['from_entity_key'] in quiz_draws
                 and r['status'] == 'resolved'}
    member_keys = {r['from_entity_key'] for r in output['relationships']
                   if r['kind'] == 'member_of' and r['to_entity_key'] in pool_keys
                   and r['status'] == 'resolved'}
    question_keys = sorted(set(question_keys) | member_keys)
    registry = TOOL_ROOT / 'workspace/reference/schemas/quiz/quiz_build_capabilities.json'
    approved = datetime.now(timezone.utc)
    authorization = create_candidate_authorization(
        model_path=model_path, promotion_receipt_path=receipt_path, registry_path=registry,
        quiz_entity_key=quiz_key, question_entity_keys=question_keys,
        approved_by='Synthetic reviewer', approved_at=approved.isoformat(),
        valid_until=(approved + timedelta(hours=1)).isoformat(),
        activation_base_commit=receipt['activation_base_commit'])
    authorization_path = packet / 'candidate.authorization.json'
    authorization_path.write_text(json.dumps(authorization, indent=2, sort_keys=True) + '\n')
    package_dir, zip_path = packet / 'package', packet / 'candidate.zip'
    command = [sys.executable, str(TOOL_ROOT / 'scripts/build_quiz_package_from_workbook.py'),
               str(model_path), '--output-dir', str(package_dir), '--zip-output', str(zip_path),
               '--quiz-entity-key', quiz_key, '--promotion-receipt', str(receipt_path),
               '--phase5-candidate-authorization', str(authorization_path)]
    for root in asset_roots:
        command.extend(['--asset-root', str(root)])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    conformance = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/check_package_conformance.py'),
                                 str(zip_path)], capture_output=True, text=True)
    assert conformance.returncode == 0, conformance.stdout + conformance.stderr
    validation = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/validate_quiz_package.py'),
        str(package_dir), '--model', str(model_path), '--promotion-receipt', str(receipt_path),
        '--phase5-candidate-authorization', str(authorization_path), '--quiz-entity-key', quiz_key,
        '--zip', str(zip_path), '--strict-closure',
        *sum((['--asset-root', str(root)] for root in asset_roots), [])], capture_output=True, text=True)
    assert validation.returncode == 0, validation.stdout + validation.stderr
    return package_dir, zip_path


def tiny_png(color=(200, 40, 30)):
    import struct
    import zlib
    def chunk(kind, value):
        import binascii
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', binascii.crc32(kind + value) & 0xffffffff)
    raw = b'\x00' + bytes(color)
    return (b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw))
        + chunk(b'IEND', b''))


def complete_review_metadata(sheet, cols, row, reason):
    sheet.cell(row, cols['revision_reason'], reason)
    sheet.cell(row, cols['proposed_by'], 'Synthetic SME')
    sheet.cell(row, cols['proposed_at'], '2026-09-25T22:00:00Z')
    sheet.cell(row, cols['approved_by'], 'Synthetic reviewer')
    sheet.cell(row, cols['approved_at'], '2026-09-25T22:05:00Z')


@pytest.mark.parametrize('field,value', [
    ('revised_question_text', 'A clearly revised SME prompt.'),
    ('revised_response_options', '["First revised option", "Second revised option", "Third revised option"]'),
    ('revised_answer_key', '{"correct_option_keys": ["B"]}'),
])
def test_accepted_plain_question_edits_reach_promoted_content(packet, field, value):
    book, sheet, cols, row, key = row_for(packet)
    sheet.cell(row, cols[field], value)
    sheet.cell(row, cols['approval_status'], 'accepted')
    book.save(packet / 'reviewer_working.xlsx')
    output, receipt = promote(packet, collect(packet))
    assert receipt['summary']['applied_change_count'] == 1, receipt['excluded']
    question = next(q for q in output['questions'] if q['entity_key'] == key)
    assert question['build_support']['level'] == 'extraction_only'
    if field == 'revised_question_text':
        assert question['prompt']['content'] == value
    elif field == 'revised_response_options':
        assert [o['content']['content'] for o in question['type_payload']['options']] == json.loads(value)
    else:
        assert [o['option_key'] for o in question['type_payload']['options'] if o['correct']] == ['B']


def test_accepted_prompt_options_and_key_survive_authorized_package_roundtrip(packet):
    book, sheet, cols, row, key = row_for(packet, 'DUPLICATE')
    edits = {
        'revised_question_text': 'Synthetic revised prompt for package roundtrip.',
        'revised_response_options': '["Roundtrip option A", "Roundtrip option B", "Roundtrip option C"]',
        'revised_answer_key': '{"correct_option_keys": ["B"]}',
    }
    for field, value in edits.items():
        sheet.cell(row, cols[field], value)
    sheet.cell(row, cols['approval_status'], 'accepted')
    complete_review_metadata(sheet, cols, row, 'Synthetic package roundtrip')
    book.save(packet / 'reviewer_working.xlsx')
    output, receipt = promote(packet, collect(packet))
    assert receipt['summary']['applied_change_count'] == len(edits), receipt['excluded']
    promoted = next(q for q in output['questions'] if q['entity_key'] == key)
    assert promoted['build_support']['level'] == 'extraction_only'

    original = json.loads((packet / 'model.json').read_text())
    source_assets = packet / 'Roundtrip Asset Root'
    source_assets.mkdir()
    for asset in original['assets']:
        source_name = Path(asset['source_path']).name
        copied_source = next((packet / 'Images').glob('*-' + source_name))
        shutil.copy2(copied_source, source_assets / source_name)
    target = validate_packet(packet)['question_additions']['target_pools'][0]
    package_dir, zip_path = package_candidate(
        packet, output, receipt, target['quiz_key'], [key], asset_roots=(source_assets, packet)
    )
    assert zip_path.is_file() and (package_dir / 'imsmanifest.xml').is_file()
    reexport = packet / 'accepted-text-reexport'
    unbind = subprocess.run(
        [sys.executable, str(TOOL_ROOT / 'scripts/quiz_unbind.py'), str(zip_path),
         '--output-dir', str(reexport)], capture_output=True, text=True,
    )
    assert unbind.returncode == 0, unbind.stdout + unbind.stderr
    roundtrip = json.loads(next((reexport / 'extraction').glob('*.model.json')).read_text())
    returned = next(q for q in roundtrip['questions'] if q.get('title') == 'DUPLICATE')
    assert returned['prompt']['content'] == edits['revised_question_text']
    assert [option['content']['content'] for option in returned['type_payload']['options']] == [
        'Roundtrip option A', 'Roundtrip option B', 'Roundtrip option C',
    ]
    assert [option['option_key'] for option in returned['type_payload']['options'] if option['correct']] == ['B']


@pytest.mark.parametrize('status', ['open', 'rejected'])
def test_unaccepted_edits_do_not_change_content(packet, status):
    book, sheet, cols, row, key = row_for(packet)
    sheet.cell(row, cols['revised_question_text'], 'DO NOT APPLY')
    sheet.cell(row, cols['approval_status'], status)
    book.save(packet / 'reviewer_working.xlsx')
    output, receipt = promote(packet, collect(packet))
    assert receipt['summary']['applied_change_count'] == 0
    question = next(q for q in output['questions'] if q['entity_key'] == key)
    assert 'DO NOT APPLY' not in question['prompt']['content']


def test_replacing_original_image_file_is_rejected(packet):
    spec = validate_packet(packet)
    (packet / spec['assets'][0]['path']).write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Replacement</text></svg>')
    with pytest.raises(ReingestError, match='missing or changed'):
        collect(packet)


def test_linking_a_new_image_in_source_image_cell_is_rejected(packet):
    book, sheet, cols, row, _ = row_for(packet, 'IMAGES')
    new_image = packet / 'Images/replacement.svg'
    new_image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Replacement</text></svg>')
    cell = sheet.cell(row, cols['image_link'])
    cell.value = 'New image'
    cell.hyperlink = 'Images/replacement.svg'
    book.save(packet / 'reviewer_working.xlsx')
    with pytest.raises(ReingestError):
        collect(packet)


def test_image_replacement_note_records_request_without_replacing_content(packet):
    book, sheet, cols, row, key = row_for(packet, 'IMAGES')
    note = f'New image uploaded to Images/replacement.svg; requested for question in row {row}.'
    (packet / 'Images/replacement.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Replacement</text></svg>')
    sheet.cell(row, cols['reviewer_note'], note)
    book.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)
    assert note in json.dumps(overlay)
    output, receipt = promote(packet, overlay)
    original = json.loads((packet / 'model.json').read_text())
    assert output['assets'] == original['assets']
    assert next(q for q in output['questions'] if q['entity_key'] == key)['type_payload'] == next(q for q in original['questions'] if q['entity_key'] == key)['type_payload']
    assert receipt['summary']['applied_change_count'] == 0


@pytest.mark.parametrize('failure', ['missing_relationship', 'escaped_parent_folder'])
def test_excluded_image_replacement_leaves_authored_content_and_assets_unchanged(packet, failure):
    spec = validate_packet(packet)
    original = json.loads((packet / 'model.json').read_text())
    question_key = next(q['entity_key'] for q in original['questions'] if q['title'] == 'IMAGES')
    target = next(row for row in spec['image_replacements']['rows']
                  if row['question_entity_key'] == question_key)
    book = load_workbook(packet / 'reviewer_working.xlsx')
    sheet = book['Image Replacements']
    cols = {cell.value: cell.column for cell in sheet[1]}
    image_dir = packet / 'Replacement Images'
    image_dir.mkdir(exist_ok=True)
    (image_dir / 'replacement.png').write_bytes(tiny_png())
    sheet.cell(target['row'], cols['replacement_file'], 'Replacement Images/replacement.png')
    sheet.cell(target['row'], cols['approval_status'], 'accepted')
    complete_review_metadata(sheet, cols, target['row'], 'Synthetic rejection test')
    book.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)

    if failure == 'missing_relationship':
        # Exercise the standalone promoter with a schema-valid source and an
        # exact overlay, but no resolved binding for the requested replacement.
        original['relationships'] = [r for r in original['relationships']
            if not (r['kind'] == 'uses_asset' and r['from_entity_key'] == question_key
                    and r['to_entity_key'] == target['asset_entity_key'])]
        source = packet / 'model.json'
        source.write_text(json.dumps(original))
        overlay['source_model']['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    else:
        # A folder moved after collection must not redirect promotion outside
        # the packet, even when the replacement bytes still match their hash.
        outside = packet.parent / 'outside-images'
        image_dir.rename(outside)
        image_dir.symlink_to(outside, target_is_directory=True)

    output, receipt = promote(packet, overlay)
    assert receipt['summary']['applied_change_count'] == 0
    assert receipt['summary']['excluded_change_count'] == 1
    for field in ('questions', 'assets', 'relationships'):
        assert output[field] == original[field]


def test_accepted_replacement_image_is_copied_and_linked_to_the_exact_question(packet):
    spec = validate_packet(packet)
    image_rows = spec['image_replacements']['rows']
    assert image_rows
    target = next(item for item in image_rows if item['question_entity_key'] ==
                  next(q['entity_key'] for q in json.loads((packet / 'model.json').read_text())['questions']
                       if q['title'] == 'IMAGES'))
    sheet = load_workbook(packet / 'reviewer_working.xlsx')
    tab = sheet['Image Replacements']
    cols = {cell.value: cell.column for cell in tab[1]}
    path = packet / 'Replacement Images/replacement.png'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(tiny_png())
    tab.cell(target['row'], cols['replacement_file'], 'Replacement Images/replacement.png')
    tab.cell(target['row'], cols['approval_status'], 'accepted')
    complete_review_metadata(tab, cols, target['row'], 'Synthetic replacement test')
    sheet.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)
    output, receipt = promote(packet, overlay)
    assert receipt['summary']['applied_change_count'] == 1
    original = json.loads((packet / 'model.json').read_text())
    q_after = next(q for q in output['questions'] if q['entity_key'] == target['question_entity_key'])
    q_before = next(q for q in original['questions'] if q['entity_key'] == target['question_entity_key'])
    assert q_after['build_support'] == q_before['build_support']
    before_content = json.dumps([q_before['prompt'], q_before['type_payload']['options']])
    after_content = json.dumps([q_after['prompt'], q_after['type_payload']['options']])
    assert target['package_path'] in before_content
    assert 'review-replacements/' in after_content and target['package_path'] not in after_content
    replacement = next(a for a in output['assets'] if a.get('extensions', {}).get('coursecraft.replacement'))
    assert replacement['fingerprint']['digest'] == overlay['annotations'][-1]['value']['sha256'] or replacement['fingerprint']['digest'] == next(a['value']['sha256'] for a in overlay['annotations'] if a['field_path'].startswith('/assets/replacements/'))
    assert any(r['kind'] == 'uses_asset' and r['from_entity_key'] == target['question_entity_key']
               and r['to_entity_key'] == replacement['entity_key'] for r in output['relationships'])
    assert original['assets'] != [] and (packet / 'model.json').read_bytes()
    book, question_sheet, question_cols, question_row, _ = row_for(packet, 'IMAGES')
    question_sheet.cell(question_row, question_cols['proposed_permanent_code'], 'SYN-IMAGE-Q001')
    question_sheet.cell(question_row, question_cols['approval_status'], 'accepted')
    complete_review_metadata(question_sheet, question_cols, question_row,
                             'Synthetic image package test')
    book.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)
    output, receipt = promote(packet, overlay)
    assert receipt['summary']['applied_change_count'] == 2, receipt['excluded']
    source_assets = packet / 'Original Asset Root'
    source_assets.mkdir()
    for asset in original['assets']:
        source_name = Path(asset['source_path']).name
        copied_source = next((packet / 'Images').glob('*-' + source_name))
        shutil.copy2(copied_source, source_assets / source_name)
    package_dir, zip_path = package_candidate(packet, output, receipt, target['quiz_key'],
                                               [target['question_entity_key']],
                                               asset_roots=(source_assets, packet))
    assert zip_path.is_file() and (package_dir / 'questiondb.xml').is_file()
    reexport = packet / 'image-reexport'
    unbind = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/quiz_unbind.py'),
        str(zip_path), '--output-dir', str(reexport)], capture_output=True, text=True)
    assert unbind.returncode == 0, unbind.stdout + unbind.stderr
    roundtrip = json.loads(next((reexport / 'extraction').glob('*.model.json')).read_text())
    returned = next(q for q in roundtrip['questions'] if q.get('title') == 'IMAGES')
    assert 'review-replacements/' in json.dumps(returned)
    digest = next(a['value']['sha256'] for a in overlay['annotations']
                  if a['field_path'].startswith('/assets/replacements/'))
    assert any(a.get('fingerprint', {}).get('digest') == digest for a in roundtrip['assets'])


def test_accepted_question_row_and_responses_join_a_selected_source_pool(packet):
    spec = validate_packet(packet)
    book = load_workbook(packet / 'reviewer_working.xlsx')
    questions = book['New Questions']
    responses = book['New Responses']
    qcols = {cell.value: cell.column for cell in questions[1]}
    rcols = {cell.value: cell.column for cell in responses[1]}
    target = spec['question_additions']['target_pools'][0]
    values = {'question_code': 'SME-NEW-Q001', 'question_type': 'Multiple Choice',
              'question_text': 'Synthetic newly authored question?', 'points': 2,
              'target_quiz_entity_key': target['quiz_key'], 'target_pool_entity_key': target['pool_key'],
              'content_format': 'plain_text', 'approval_status': 'accepted',
              'revision_reason': 'Synthetic new question test', 'proposed_by': 'Synthetic SME',
              'proposed_at': '2026-09-25T22:00:00Z', 'approved_by': 'Synthetic reviewer',
              'approved_at': '2026-09-25T22:05:00Z'}
    for name, value in values.items():
        questions.cell(2, qcols[name], value)
    for row, response in enumerate([
        ['SME-NEW-Q001', 'A', 'option', 'Correct synthetic answer', 'plain_text', True],
        ['SME-NEW-Q001', 'B', 'option', 'Incorrect synthetic answer', 'plain_text', False],
    ], 2):
        for name, value in zip(['question_code', 'response_key', 'role', 'text', 'content_format', 'correct'], response):
            responses.cell(row, rcols[name], value)
    book.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)
    output, receipt = promote(packet, overlay)
    assert receipt['summary']['applied_change_count'] == 1, receipt['excluded']
    added = next(q for q in output['questions'] if q['identity'].get('permanent_code') == 'SME-NEW-Q001')
    assert added['build_support']['level'] == 'extraction_only'
    membership = next(r for r in output['relationships'] if r['kind'] == 'member_of' and r['from_entity_key'] == added['entity_key'])
    assert membership['to_entity_key'] == target['pool_key']
    pool = next(s for s in output['structures'] if s['entity_key'] == target['pool_key'])
    assert pool['selection']['available_count'] == next(s for s in json.loads((packet / 'model.json').read_text())['structures'] if s['entity_key'] == target['pool_key'])['selection']['available_count'] + 1
    authorization_scope = {q['entity_key'] for q in output['questions'] if q['entity_key'] in {added['entity_key']}}
    assert authorization_scope == {added['entity_key']}
    image_question = next(q for q in output['questions'] if q.get('title') == 'IMAGES')
    source_assets = packet / 'Original Asset Root'
    source_assets.mkdir()
    for asset in json.loads((packet / 'model.json').read_text())['assets']:
        source_name = Path(asset['source_path']).name
        copied_source = next((packet / 'Images').glob('*-' + source_name))
        shutil.copy2(copied_source, source_assets / source_name)
    package_dir, zip_path = package_candidate(packet, output, receipt, target['quiz_key'],
                                              [added['entity_key'], image_question['entity_key']],
                                              asset_roots=(source_assets, packet))
    assert zip_path.is_file() and (package_dir / 'questiondb.xml').is_file()
    reexport = packet / 'reexport'
    unbind = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/quiz_unbind.py'),
        str(zip_path), '--output-dir', str(reexport)], capture_output=True, text=True)
    assert unbind.returncode == 0, unbind.stdout + unbind.stderr
    roundtrip = json.loads(next((reexport / 'extraction').glob('*.model.json')).read_text())
    returned = next(q for q in roundtrip['questions'] if q.get('title') == 'SME-NEW-Q001')
    returned_options = returned['type_payload']['options']
    assert [option['option_key'] for option in returned_options] == ['A', 'B']
    assert [option['correct'] for option in returned_options] == [True, False]
    returned_content = json.dumps([returned['prompt'], returned_options])
    assert 'Correct synthetic answer' in returned_content
    assert 'Incorrect synthetic answer' in returned_content


@pytest.mark.parametrize('kind', ['question', 'image_instruction'])
def test_new_rows_in_extracted_assessment_are_rejected(packet, kind):
    book, sheet, cols, row, _ = row_for(packet)
    target = sheet.max_row + 1
    if kind == 'question':
        sheet.cell(target, cols['revised_question_text'], 'A newly added question')
        sheet.cell(target, cols['approval_status'], 'accepted')
    else:
        sheet.cell(target, cols['reviewer_note'], 'New image uploaded to Images/replacement.svg')
    book.save(packet / 'reviewer_working.xlsx')
    with pytest.raises(ReingestError):
        collect(packet)


@pytest.mark.parametrize('field,value', [('source_points', 9), ('source_feedback', 'Changed feedback'), ('answer_key', 'B')])
def test_source_fields_are_protected(packet, field, value):
    book, sheet, cols, row, _ = row_for(packet)
    sheet.cell(row, cols[field], value)
    book.save(packet / 'reviewer_working.xlsx')
    with pytest.raises(ReingestError):
        collect(packet)


def test_invalid_option_count_is_reported_instead_of_applied(packet):
    book, sheet, cols, row, _ = row_for(packet)
    sheet.cell(row, cols['revised_response_options'], '["Only one option"]')
    sheet.cell(row, cols['approval_status'], 'accepted')
    book.save(packet / 'reviewer_working.xlsx')
    _, receipt = promote(packet, collect(packet))
    assert receipt['summary']['applied_change_count'] == 0
    assert receipt['summary']['excluded_change_count'] == 1


@pytest.mark.parametrize('status', ['open', 'accepted', 'rejected'])
def test_quiz_setting_proposal_uses_explicit_acceptance(packet, status):
    book = load_workbook(packet / 'reviewer_working.xlsx')
    sheet = book['Quiz Settings']
    cols = {c.value: c.column for c in sheet[1]}
    row = next(r for r in range(2, sheet.max_row + 1)
               if sheet.cell(r, cols['setting']).value == 'time_limit')
    sheet.cell(row, cols['proposed_value'], '45')
    sheet.cell(row, cols['approval_status'], status)
    book.save(packet / 'reviewer_working.xlsx')
    overlay = collect(packet)
    assert len(overlay['settings_decisions']) == 1
    assert len(overlay['settings_inputs']) == (status == 'accepted')
    if status == 'accepted':
        assert overlay['settings_inputs'][0]['value'] == 45


def test_new_question_rows_in_blank_drafting_template_are_ingested(tmp_path):
    from quiz_draft_intake import ingest, read_workbook

    source = TOOL_ROOT / 'workspace/reference/examples/quiz_draft/blank.xlsx'
    before = source.read_bytes()
    book = load_workbook(source)
    config = {'lineage_code': 'sme-synthetic-additions', 'quiz_code': 'new-rows', 'quiz_title': 'Synthetic new question test'}
    for row in book['Config']:
        if row[0].value in config:
            row[1].value = config[row[0].value]
    for code, prompt in [('NEW-Q1', 'First newly drafted question.'), ('NEW-Q2', 'Second newly drafted question.')]:
        book['Questions'].append([code, 'Multiple Choice', prompt, 2, 'NEW-P1', None, None, 'plain_text'])
        book['Responses'].append([code, 'A', 'option', 'Correct', 'plain_text', True])
        book['Responses'].append([code, 'B', 'option', 'Incorrect', 'plain_text', False])
    book['Pools'].append(['NEW-P1', 'New questions'])
    book['Draws'].append(['NEW-D1', 'NEW-P1', 'Two new questions', 2, 2])
    path = tmp_path / 'new-questions.xlsx'
    book.save(path)
    model, report = ingest(read_workbook(path), source_name=path.name, input_format='xlsx')
    assert report['valid_intake'] and not report['ready_to_build']
    assert len(model['questions']) == 2
    assert {q['prompt']['content'] for q in model['questions']} == {'First newly drafted question.', 'Second newly drafted question.'}
    assert {q['identity']['permanent_code'] for q in model['questions']} == {'NEW-Q1', 'NEW-Q2'}
    assert source.read_bytes() == before


def test_real_extraction_exposes_exact_source_pool_for_additions(tmp_path):
    """Exercise actual emitted itemrefs, without inventing authoring graph edges."""
    source = TEST_ROOT / 'tests/fixtures/quiz_authoring'
    package = tmp_path / 'source-package'
    build = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/build_quiz_package_from_workbook.py'),
        str(source / 'buildable_quiz.model.json'), '--asset-root', str(source),
        '--output-dir', str(package)], capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    extraction = tmp_path / 'extract'
    extract = subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/extract_quiz_pool_review.py'),
        str(package), '--asset-mode', 'copy', '--output-dir', str(extraction)], capture_output=True, text=True)
    assert extract.returncode == 0, extract.stdout + extract.stderr
    model_path = next(extraction.glob('*.model.json'))
    original = model_path.read_bytes()
    packet = tmp_path / 'packet'
    prepare_review_packet(next(extraction.glob('*reviewer.xlsx')), model_path, packet)
    spec = validate_packet(packet)
    assert len(spec['question_additions']['target_pools']) == 1
    # Reuse the complete accepted-addition/package/re-extraction scenario on
    # this ordinary extraction, instead of its hand-assembled unit fixture.
    target = spec['question_additions']['target_pools'][0]
    model = json.loads(model_path.read_text())
    assert not any(r['kind'] == 'draws_from' for r in model['relationships'])
    from quiz_build_support import resolve_projection_relationships
    resolved, derivations = resolve_projection_relationships(model, target['quiz_key'])
    assert any(d['kind'] == 'draw_draws_from_pool' for d in derivations)
    book = load_workbook(packet / 'reviewer_working.xlsx')
    code_index = 0
    scoring_edits = 0
    for tab in spec['sheets']:
        sheet = book[tab['title']]; columns = {c.value:c.column for c in sheet[2]}
        for row_info in tab['rows']:
            if sheet.cell(row_info['row'], columns['question_type']).value == 'Multi-Select':
                sheet.cell(row_info['row'], columns['proposed_scoring_mode'], 'all_or_nothing')
                scoring_edits += 1
            code_index += 1
            sheet.cell(row_info['row'], columns['proposed_permanent_code'], f'SYN-EXACT-{code_index:03d}')
            sheet.cell(row_info['row'], columns['approval_status'], 'accepted')
    qsheet = book['New Questions']; cols = {c.value:c.column for c in qsheet[1]}
    for name, value in dict(question_code='SYN-NEW-EXACT', question_type='Multiple Choice',
        question_text='Synthetic added question?', points=1, content_format='plain_text',
        target_quiz_entity_key=target['quiz_key'], target_pool_entity_key=target['pool_key'],
        approval_status='accepted').items(): qsheet.cell(2, cols[name], value)
    rsheet = book['New Responses']; cols = {c.value:c.column for c in rsheet[1]}
    for row, (key, correct) in enumerate([('A', True), ('B', False)], 2):
        for name, value in dict(question_code='SYN-NEW-EXACT', response_key=key,
            role='option', text='Synthetic '+key, content_format='plain_text', correct=correct).items():
            rsheet.cell(row, cols[name], value)
    book.save(packet / 'reviewer_working.xlsx')
    output, receipt = promote(packet, collect(packet))
    assert receipt['summary']['applied_change_count'] == 1 + code_index + scoring_edits
    pool = next(s for s in output['structures'] if s['entity_key'] == target['pool_key'])
    assert pool['selection']['available_count'] == 5
    assert model_path.read_bytes() == original
    package_dir, _ = package_candidate(packet, output, receipt, target['quiz_key'],
        [q['entity_key'] for q in output['questions']], asset_roots=(extraction, packet))
    assert 'SYN-NEW-EXACT' in (package_dir / 'questiondb.xml').read_text()
