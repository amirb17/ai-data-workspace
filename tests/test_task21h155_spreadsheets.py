"""Security preflight is not an XLSX parser, upload acceptance or source pin."""
import io
import zipfile

import pytest

from app.services import spreadsheet_inspection as sheets
from tests.task21h153_support import workbook


def altered(change):
    with zipfile.ZipFile(io.BytesIO(workbook())) as archive:
        parts = {n: archive.read(n) for n in archive.namelist()}
    change(parts)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, body in parts.items(): archive.writestr(name, body)
    return output.getvalue()


def test_bounded_workbook_inspection_remains_explicitly_unsupported_for_ingestion():
    result = sheets.inspect_xlsx(workbook(), 'orders.xlsx')
    assert result.sheet_rows == (8,)
    assert not result.ingestion_supported
    assert result.blocker == 'APPROVED_PARSER_AND_MAPPING_REQUIRED'
    assert len(result.sha256) == 64


@pytest.mark.parametrize('filename', ['orders.xls', 'orders.xlsm', 'orders.xlsb', '../orders.xlsx', 'orders.csv', 'bad\x00.xlsx', 'bad\n.xlsx'])
def test_unsupported_formats_never_reach_xml(filename):
    with pytest.raises(sheets.SpreadsheetRejected, match='UNSUPPORTED_SPREADSHEET_FORMAT'):
        sheets.inspect_xlsx(workbook(), filename)


@pytest.mark.parametrize('fault', ['formula', 'external', 'macro', 'entity', 'unmapped', 'missing', 'error', 'coordinate', 'hidden', 'malformed', 'wrong-main', 'wrong-type', 'xinclude'])
def test_unsafe_spreadsheet_parts_are_rejected_without_payload_disclosure(fault):
    def change(parts):
        key = 'xl/worksheets/sheet1.xml'
        if fault == 'formula': parts[key] = parts[key].replace(b'<sheetData>', b'<f>SYNTHETIC_PRIVATE_FORMULA</f><sheetData>')
        elif fault == 'external': parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(b'Target=', b'TargetMode="External" Target=')
        elif fault == 'macro': parts['xl/vbaProject.bin'] = b'synthetic'
        elif fault == 'entity': parts[key] = b'<!DOCTYPE x [<!ENTITY x "SYNTHETIC_PRIVATE">]>'+parts[key]
        elif fault == 'unmapped': parts['xl/worksheets/sheet2.xml'] = parts[key]
        elif fault == 'missing': parts.pop(key)
        elif fault == 'error': parts[key] = parts[key].replace(b't="inlineStr"', b't="e"', 1)
        elif fault == 'coordinate': parts[key] = parts[key].replace(b'<c ', b'<c r="XFD1048576" ', 1)
        elif fault == 'hidden': parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace(b'<sheet name=', b'<sheet state="hidden" name=')
        elif fault == 'wrong-main': parts['_rels/.rels'] = parts['_rels/.rels'].replace(b'xl/workbook.xml', b'xl/worksheets/sheet1.xml')
        elif fault == 'wrong-type': parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(b'spreadsheetml.sheet.main+xml', b'spreadsheetml.worksheet+xml')
        elif fault == 'xinclude': parts[key] = parts[key].replace(b'<sheetData>', b'<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="https://example.invalid"/><sheetData>')
        else: parts[key] = b'<broken'
    with pytest.raises(sheets.SpreadsheetRejected) as failure:
        sheets.inspect_xlsx(altered(change), 'orders.xlsx')
    assert 'SYNTHETIC_PRIVATE' not in str(failure.value)


@pytest.mark.parametrize('limit', ['MAX_BYTES', 'MAX_ROWS', 'MAX_COLUMNS', 'MAX_ENTRIES', 'MAX_PART', 'MAX_EXPANDED', 'MAX_SHEETS'])
def test_resource_limits_are_enforced(monkeypatch, limit):
    monkeypatch.setattr(sheets, limit, 0)
    with pytest.raises(sheets.SpreadsheetRejected): sheets.inspect_xlsx(workbook(), 'orders.xlsx')


def test_duplicate_archive_members_and_traversal_rejected():
    for name in ('xl/workbook.xml', '../escape.xml'):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr(name, '<workbook/>')
            if not name.startswith('..'):
                with pytest.warns(UserWarning): archive.writestr(name, '<workbook/>')
        with pytest.raises(sheets.SpreadsheetRejected): sheets.inspect_xlsx(output.getvalue(), 'orders.xlsx')


def test_upload_ownership_precedes_xlsx_capability_rejection(monkeypatch):
    from app.services import upload_session_service as upload
    events = []
    monkeypatch.setattr(upload, 'validate_upload_context', lambda *a: events.append('authorized'))
    monkeypatch.setattr(upload, 'generate_presigned_upload_url', lambda **kw: pytest.fail('unsupported XLSX was signed'))
    with pytest.raises(ValueError, match='approved parser'):
        upload.initiate_upload_session(1, 2, 3, 'orders.xlsx', 10)
    assert events == ['authorized']
    def forbidden(*args): raise PermissionError('denied')
    monkeypatch.setattr(upload, 'validate_upload_context', forbidden)
    with pytest.raises(PermissionError): upload.initiate_upload_session(1, 2, 3, 'orders.xlsx', 10)


@pytest.mark.parametrize('fault', ['zip-bomb', 'symlink', 'deep-xml', 'oversized-text', 'utf16', 'not-zip'])
def test_additional_archive_and_xml_limits(fault):
    if fault == 'not-zip': body = b'not a ZIP'
    elif fault == 'symlink':
        output = io.BytesIO()
        info = zipfile.ZipInfo('xl/workbook.xml')
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(output, 'w') as archive: archive.writestr(info, b'synthetic')
        body = output.getvalue()
    elif fault == 'zip-bomb':
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('xl/sharedStrings.xml', b' '*100000)
        body = output.getvalue()
    else:
        def change(parts):
            if fault == 'deep-xml': parts['xl/styles.xml'] = b'<x>'*40+b'</x>'*40
            elif fault == 'oversized-text': parts['xl/styles.xml'] = b'<x>'+b'a'*65537+b'</x>'
            else: parts['xl/styles.xml'] = '<x/>'.encode('utf-16')
        body = altered(change)
    with pytest.raises(sheets.SpreadsheetRejected): sheets.inspect_xlsx(body, 'orders.xlsx')
