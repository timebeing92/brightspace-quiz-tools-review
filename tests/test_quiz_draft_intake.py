from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from quiz_draft_intake import (
    ingest, IntakeError, read_json, read_workbook, template_spec,
    INPUT_SCHEMA_PATH, REPORT_SCHEMA_PATH, validate_shape,
)
from quiz_question_types import question_type_catalog, canonical_question_type
from quiz_build_support import question_projection_issues
from quiz_authoring_readiness import analyze_authoring_readiness


def draft(kind='multiple_choice'):
    return {'config': {'schema': 'coursecraft.quiz_draft/1', 'lineage_code': 'demo', 'quiz_code': 'quiz1', 'quiz_title': 'Synthetic drafting example'},
            'Questions': [{'question_code': 'Q1', 'question_type': kind, 'question_text': 'Synthetic prompt', 'points': 2, 'pool_code': 'P1', 'content_format': 'plain_text'}],
            'Responses': [{'question_code': 'Q1', 'response_key': 'A', 'role': 'option', 'text': 'First', 'content_format': 'plain_text', 'correct': True},
                          {'question_code': 'Q1', 'response_key': 'B', 'role': 'option', 'text': 'Second', 'content_format': 'plain_text', 'correct': False}],
            'Pools': [{'pool_code': 'P1', 'title': 'Synthetic pool'}], 'Draws': [{'draw_code': 'D1', 'pool_code': 'P1', 'title': 'Draw one', 'draw_count': 1, 'points_per_question': 3}]}


def example(kind):
    d = draft(kind)
    rs = d['Responses']
    if kind == 'true_false':
        for r, key, text in zip(rs, ['T', 'F'], ['True', 'False']):
            r.update(response_key=key, text=text)
    elif kind == 'long_answer':
        d['Responses'] = []
        d['Questions'][0]['answer_key'] = 'Evaluator guidance'
    elif kind in ('short_answer', 'multi_short_answer', 'fill_in_blanks'):
        for i, r in enumerate(rs):
            r.pop('correct'); r.update(role='accepted', case_sensitive=False)
            if kind != 'short_answer':
                r['group_key'] = f'b{i+1}'
        if kind == 'fill_in_blanks':
            d['Questions'][0]['question_text'] = 'A [[blank:b1]] and [[blank:b2]].'
    elif kind == 'ordering':
        for i, r in enumerate(rs):
            r.pop('correct'); r['position'] = i+1
    elif kind == 'matching':
        right = []
        for r in rs:
            r.pop('correct'); r['role'] = 'left'; r['match_key'] = r['response_key']+'R'
            right.append(dict(r, role='right', response_key=r['match_key']))
            right[-1].pop('match_key')
        rs.extend(right)
    return d


@pytest.mark.parametrize('kind', [r['kind'] for r in question_type_catalog()])
def test_nine_types_canonical_without_evidence_promotion(kind):
    model, report = ingest(example(kind))
    assert report['valid_intake'] and not report['ready_to_build']
    assert model['questions'][0]['kind'] == kind
    assert model['questions'][0]['build_support']['level'] == 'extraction_only'
    assert canonical_question_type(next(r['label'] for r in question_type_catalog() if r['kind'] == kind)) == kind


def test_identity_survives_wording_and_math_feedback_are_exact():
    original = draft()
    first, _ = ingest(original)
    original['Questions'][0].update(question_text='<p><math><mi>x</mi></math></p>', content_format='html', feedback='<p>Feedback <math><mi>y</mi></math></p>')
    original['Responses'][0].update(text=r'\frac{1}{2}', content_format='latex')
    second, _ = ingest(original)
    assert first['questions'][0]['entity_key'] == second['questions'][0]['entity_key']
    assert first['source']['fingerprint'] != second['source']['fingerprint']
    q = second['questions'][0]
    assert q['prompt']['content'] == original['Questions'][0]['question_text']
    assert q['feedback'][0]['content']['content'] == original['Questions'][0]['feedback']
    assert q['type_payload']['options'][0]['content']['content'] == r'\frac{1}{2}'
    codes = {x[0] for x in question_projection_issues(q, randomize_answers=False)}
    assert {'feedback_projection_not_supported', 'content_encoding_requires_conversion'} <= codes


@pytest.mark.parametrize('change', [
    lambda d: d['Questions'].append(deepcopy(d['Questions'][0])),
    lambda d: d['Questions'][0].update(question_type='Essay maybe'),
    lambda d: d['Questions'][0].update(points=float('nan')),
    lambda d: d['Questions'][0].update(points=True),
    lambda d: d['Questions'][0].update(pool_code='missing'),
    lambda d: d['Questions'][0].update(answer_key='A'),
    lambda d: d['Responses'][1].update(correct=True),
    lambda d: d['Responses'][0].update(correct='maybe'),
    lambda d: d['Responses'][0].update(response_key='B'),
    lambda d: d['Responses'][0].update(question_code='missing'),
    lambda d: d['Responses'][0].update(position=1),
    lambda d: d['Draws'][0].update(draw_count=2),
    lambda d: d['Draws'][0].update(draw_count=1.5),
    lambda d: d['Draws'][0].update(points_per_question=0),
    lambda d: d['Questions'][0].update(question_type='Long Answer'),
    lambda d: d['Questions'][0].update(content_format='guess'),
])
def test_ambiguous_or_dangling_inputs_refused(change):
    d = draft(); change(d)
    with pytest.raises(ValueError):
        ingest(d)


@pytest.mark.parametrize('kind,change', [
    ('true_false', lambda d: d['Responses'][0].update(text='Yes')),
    ('short_answer', lambda d: d['Responses'].clear()),
    ('multi_short_answer', lambda d: d['Responses'][1].update(group_key='b1')),
    ('fill_in_blanks', lambda d: d['Questions'][0].update(question_text='No named blank')),
    ('matching', lambda d: d['Responses'][0].update(match_key='missing')),
    ('ordering', lambda d: d['Responses'][0].update(position=2)),
])
def test_type_specific_negative_cases(kind, change):
    d=example(kind); change(d)
    with pytest.raises(ValueError):
        ingest(d)


def test_library_only_and_draw_point_semantics():
    d = draft(); model, _ = ingest(d)
    assert model['questions'][0]['scoring']['maximum_points'] == 2
    assert model['structures'][-1]['selection']['extensions']['points_per_question'] == 3
    d['config'].update(quiz_code=None, quiz_title=None); d['Draws'] = []
    model, _ = ingest(d)
    assert not model['quizzes'] and model['structures'][0]['kind'] == 'pool'


def test_fresh_draft_is_blocked_by_existing_readiness(tmp_path):
    model, _ = ingest(draft())
    path=tmp_path/'quiz.model.json'; path.write_text(json.dumps(model))
    report=analyze_authoring_readiness(path)
    assert not report['ready']
    assert 'question_not_approved_for_build' in {x['code'] for x in report['issues']}


def test_saved_xlsx_matches_json_and_rejects_formula_cells(tmp_path):
    from quiz_draft_intake import read_workbook
    from zipfile import ZipFile
    import xml.etree.ElementTree as ET
    root=Path(__file__).resolve().parents[1]/'workspace/reference/examples/quiz_draft'
    data=read_workbook(root/'nine_types.xlsx')
    model,_=ingest(data)
    expected,_=ingest(json.loads((root/'nine_types.json').read_text()))
    for collection in ('questions','structures','relationships'):
        assert model[collection]==expected[collection]
    blank=read_workbook(root/'blank.xlsx')
    assert all(not blank[table] for table in ('Questions','Responses','Pools','Draws'))
    # Malformed input fixture: replace a data cell in the already authored file
    # with a formula. The reader must not consume a cached display value.
    path=tmp_path/'formula.xlsx'
    with ZipFile(root/'nine_types.xlsx') as source, ZipFile(path,'w') as target:
        changes=0
        for name in source.namelist():
            value=source.read(name)
            if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                root=ET.fromstring(value)
                cell=root.find(f'.//{ns}c[@r="A2"]')
                if cell is not None:
                    cell.clear();cell.set('r','A2')
                    ET.SubElement(cell,ns+'f').text='1+1'
                    ET.SubElement(cell,ns+'v').text='2'
                    value=ET.tostring(root);changes+=1
            target.writestr(name,value)
    assert changes
    with pytest.raises(IntakeError, match='formulas'):
        read_workbook(path)


@pytest.mark.parametrize('field', ['answer_key', 'feedback'])
@pytest.mark.parametrize('value', [0, False, 1, [], {}])
def test_populated_nontext_optional_content_is_refused_with_field(field, value):
    d = example('long_answer')
    d['Questions'][0][field] = value
    with pytest.raises(IntakeError, match=rf'Questions\[0\].{field}'):
        ingest(d)


@pytest.mark.parametrize('value', ['0', 'False', ' 0 ', '<math><mn>0</mn></math>'])
def test_literal_zero_and_false_content_is_preserved_verbatim(value):
    d = example('long_answer')
    d['Questions'][0].update(answer_key=value, feedback=value)
    model, report = ingest(d)
    question = model['questions'][0]
    assert report['valid_intake'] is True
    assert question['type_payload']['manual_answer_key']['content'] == value
    assert question['feedback'][0]['content']['content'] == value


@pytest.mark.parametrize('data', [None, [], 'draft', 123, {'config': None}])
def test_malformed_root_and_config_shapes_fail_as_intake_errors(data):
    with pytest.raises(IntakeError):
        ingest(data)


@pytest.mark.parametrize('change,location', [
    (lambda d: d.update(config=[]), 'config'),
    (lambda d: d.update(Questions={}), 'Questions'),
    (lambda d: d['Questions'].__setitem__(0, []), 'Questions'),
    (lambda d: d['Questions'][0].update(question_type={}), 'question_type'),
    (lambda d: d['Questions'][0].update(pool_code=[]), 'pool_code'),
    (lambda d: d['Questions'][0].update(content_format=[]), 'content_format'),
    (lambda d: d['Responses'][0].update(question_code={}), 'question_code'),
    (lambda d: d['Responses'][0].update(role=[]), 'role'),
    (lambda d: d['config'].update(quiz_title=False), 'quiz_title'),
])
def test_malformed_nested_shapes_fail_with_field(change, location):
    d = draft(); change(d)
    with pytest.raises(IntakeError, match=location):
        ingest(d)


@pytest.mark.parametrize('value', [2**53, '9007199254740993', '0.0000000001', '0.10000000000000001',
                                  '100000000.123456789', '1e999', '1e-999', float('inf')])
def test_numeric_precision_loss_is_refused_without_rounding(value):
    d = draft(); d['Questions'][0]['points'] = value
    with pytest.raises(IntakeError, match='points'):
        ingest(d)


@pytest.mark.parametrize('value,expected', [('0.1', 0.1), ('1e-9', 1e-9), ('2.0000000000', 2), (2**53-1, 2**53-1)])
def test_supported_decimal_and_integer_values_preserve_json_value(value, expected):
    d = draft(); d['Questions'][0]['points'] = value
    model, _ = ingest(d)
    assert model['questions'][0]['scoring']['maximum_points'] == expected


def test_json_reader_preserves_decimal_tokens_until_validation(tmp_path):
    path = tmp_path/'precise.json'
    text = json.dumps(draft()).replace('"points": 2', '"points": 0.10000000000000001')
    path.write_text(text)
    data = read_json(path)
    with pytest.raises(IntakeError, match='points'):
        ingest(data)


@pytest.mark.parametrize('text', ['{"config":{},"config":{}}', '{"value":NaN}', '{"value":Infinity}'])
def test_duplicate_json_keys_and_nonstandard_numbers_are_refused(tmp_path, text):
    path = tmp_path/'invalid.json'; path.write_text(text)
    with pytest.raises(IntakeError):
        read_json(path)


def workbook_with_changed_cell(tmp_path, address, cell_type, value):
    from zipfile import ZipFile
    import xml.etree.ElementTree as ET
    root = Path(__file__).resolve().parents[1]/'workspace/reference/examples/quiz_draft'
    path = tmp_path/'changed-cell.xlsx'
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with ZipFile(root/'nine_types.xlsx') as source, ZipFile(path, 'w') as target:
        import posixpath
        workbook_xml = ET.fromstring(source.read('xl/workbook.xml'))
        sheet = next(s for s in workbook_xml.findall(f'.//{ns}sheet') if s.get('name') == 'Questions')
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        relations = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        link = next(r.get('Target') for r in relations if r.get('Id') == rid)
        question_part = link.lstrip('/') if link.startswith('/') else posixpath.normpath('xl/'+link)
        changed = 0
        for name in source.namelist():
            part = source.read(name)
            if name == question_part:
                document = ET.fromstring(part)
                cell = document.find(f'.//{ns}c[@r="{address}"]')
                assert cell is not None
                if value is None:
                    next(row for row in document.iter(ns+'row') if cell in list(row)).remove(cell)
                else:
                    cell.clear(); cell.set('r', address); cell.set('t', cell_type)
                    ET.SubElement(cell, ns+'v').text = str(value)
                part = ET.tostring(document); changed += 1
            target.writestr(name, part)
    assert changed == 1
    return path


@pytest.mark.parametrize('cell_type', ['n', 'b'])
def test_xlsx_zero_false_answer_key_reports_physical_sheet_row_field(tmp_path, cell_type):
    path = workbook_with_changed_cell(tmp_path, 'F5', cell_type, '0')
    with pytest.raises(IntakeError, match=r'Questions row 5.answer_key'):
        ingest(read_workbook(path))


def test_xlsx_stored_decimal_precision_is_checked_before_float_conversion(tmp_path):
    path = workbook_with_changed_cell(tmp_path, 'D2', 'n', '0.10000000000000001')
    with pytest.raises(IntakeError, match=r'Questions row 2.points'):
        ingest(read_workbook(path))


def test_xlsx_sparse_missing_first_cell_has_a_field_diagnostic(tmp_path):
    path = workbook_with_changed_cell(tmp_path, 'A2', 'n', None)
    with pytest.raises(IntakeError, match=r'Questions row 2.question_code'):
        ingest(read_workbook(path))


def test_input_and_report_schemas_and_provenance_are_explicit(tmp_path):
    from jsonschema import Draft202012Validator
    for path in (INPUT_SCHEMA_PATH, REPORT_SCHEMA_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text()))
    model, report = ingest(draft(), source_name='draft.json', source_digest='a'*64)
    validate_shape(report, REPORT_SCHEMA_PATH)
    assert report['source']['input_format'] == 'json'
    assert report['source']['fingerprint_basis'] == 'file_bytes'
    assert model['source']['source_kind'] == 'unknown'
    assert model['source']['extensions']['coursecraft.draft_input'] == report['source']
    workbook = Path(__file__).resolve().parents[1]/'workspace/reference/examples/quiz_draft/nine_types.xlsx'
    xmodel, xreport = ingest(read_workbook(workbook), source_name=workbook.name)
    assert xmodel['source']['source_kind'] == 'workbook'
    assert xreport['source']['input_format'] == 'xlsx'
    assert xreport['source']['fingerprint_basis'] == 'canonical_json'
    report['ready_to_build'] = True
    with pytest.raises(IntakeError, match='ready_to_build'):
        validate_shape(report, REPORT_SCHEMA_PATH)


def test_template_spec_is_generated_from_runtime_contract():
    path = Path(__file__).resolve().parents[1]/'workspace/reference/examples/quiz_draft/template-spec.json'
    assert template_spec() == json.loads(path.read_text())


def test_templates_preserve_text_entry_formats_and_native_type_dropdowns():
    import openpyxl
    root = Path(__file__).resolve().parents[1]/'workspace/reference/examples/quiz_draft'
    for name in ('blank.xlsx', 'nine_types.xlsx'):
        workbook = openpyxl.load_workbook(root/name)
        try:
            for address in ('A2', 'C2', 'F2', 'G2'):
                assert workbook['Questions'][address].number_format == '@'
            assert workbook['Responses']['D2'].number_format == '@'
            dropdowns = workbook['Questions'].data_validations.dataValidation
            type_dropdown = next(d for d in dropdowns if 'B2' in d.sqref)
            assert type_dropdown.type == 'list'
            assert all(row['label'] in type_dropdown.formula1 for row in question_type_catalog())
            assert workbook['Draws']['D2'].number_format == '0'
        finally:
            workbook.close()


def test_invalid_cli_intake_does_not_create_output_directory(tmp_path):
    import subprocess
    data = example('long_answer'); data['Questions'][0]['answer_key'] = 0
    path = tmp_path/'invalid.json'; path.write_text(json.dumps(data))
    out = tmp_path/'must-not-exist'
    script = Path(__file__).resolve().parents[1]/'scripts/quiz_draft_intake.py'
    result = subprocess.run([sys.executable, str(script), str(path), '--output-dir', str(out)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'answer_key' in result.stderr and 'Traceback' not in result.stderr
    assert not out.exists()


def test_corrupt_workbook_cli_refuses_without_a_traceback_or_output(tmp_path):
    import subprocess
    path = tmp_path/'invalid.xlsx'; path.write_bytes(b'not a ZIP workbook')
    out = tmp_path/'must-not-exist'
    script = Path(__file__).resolve().parents[1]/'scripts/quiz_draft_intake.py'
    result = subprocess.run([sys.executable, str(script), str(path), '--output-dir', str(out)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Intake refused' in result.stderr and 'Traceback' not in result.stderr
    assert not out.exists()
