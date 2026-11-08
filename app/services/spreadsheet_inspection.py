"""Bounded XLSX security preflight only; no cell parser or ingestion authority.

An approved parsing dependency and sheet-to-dataset contract are still required.
Passing inspection must NEVER be used to enable upload/processing by itself.
"""
from dataclasses import dataclass, field
import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
import zipfile
from xml.etree import ElementTree as ET

MAX_BYTES = 16*1024*1024
MAX_EXPANDED = 64*1024*1024
MAX_PART = 16*1024*1024
MAX_ENTRIES = 128
MAX_SHEETS = 8
MAX_ROWS = 100000
MAX_COLUMNS = 128


class SpreadsheetRejected(ValueError):
    """Message is an allowlisted policy code; never workbook content."""


@dataclass(frozen=True)
class SpreadsheetInspection:
    sha256: str
    size_bytes: int
    sheet_rows: tuple
    ingestion_supported: bool = field(default=False, init=False)
    blocker: str = field(default='APPROVED_PARSER_AND_MAPPING_REQUIRED', init=False)


def inspect_xlsx(payload, filename):
    if (not isinstance(filename, str) or not re.fullmatch(r'[^/\\\x00]{1,200}\.xlsx', filename, re.I)
        or '..' in filename or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
        raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BYTES:
        raise SpreadsheetRejected('SPREADSHEET_BYTE_LIMIT')
    try:
        return _inspect(payload)
    except SpreadsheetRejected:
        raise
    except Exception:
        raise SpreadsheetRejected('MALFORMED_SPREADSHEET') from None


def _inspect(payload):
    allowed = re.compile(r'(?:\[Content_Types\]\.xml|_rels/\.rels|docProps/(?:core|app)\.xml|xl/(?:workbook\.xml|_rels/workbook\.xml\.rels|styles\.xml|sharedStrings\.xml|theme/theme1\.xml|worksheets/sheet[1-9][0-9]*\.xml))')
    parts, folded = {}, set()
    xml_nodes = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise SpreadsheetRejected('SPREADSHEET_ENTRY_LIMIT')
        if sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise SpreadsheetRejected('SPREADSHEET_EXPANSION_LIMIT')
        for entry in entries:
            name = entry.filename
            mode = (entry.external_attr >> 16) & 0o170000
            if (not allowed.fullmatch(name) or PurePosixPath(name).is_absolute()
                or mode not in (0, stat.S_IFREG) or entry.flag_bits & 1
                or name.casefold() in folded):
                raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_PART')
            folded.add(name.casefold())
            if (entry.file_size > MAX_PART or entry.file_size > max(1, entry.compress_size)*100
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise SpreadsheetRejected('SPREADSHEET_EXPANSION_LIMIT')
            raw = archive.read(entry)  # CRC verified by zipfile; bounded by policy above.
            if len(raw) != entry.file_size:
                raise SpreadsheetRejected('MALFORMED_SPREADSHEET')
            text = raw.decode('utf-8-sig')
            if '<!' in text:  # Reject DTD/entities/CDATA/comments; conservative subset.
                raise SpreadsheetRejected('UNSUPPORTED_XML_DECLARATION')
            parser = ET.iterparse(io.StringIO(text), events=('start', 'end'))
            depth = 0
            for event, node in parser:
                if event == 'start':
                    xml_nodes += 1
                    depth += 1
                    if xml_nodes > 250000 or depth > 32 or len(node.attrib) > 16:
                        raise SpreadsheetRejected('SPREADSHEET_XML_LIMIT')
                else:
                    depth -= 1
                    if len(node.text or '') > 65536:
                        raise SpreadsheetRejected('SPREADSHEET_XML_LIMIT')
            parts[name] = parser.root
    required = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'}
    if not required <= parts.keys():
        raise SpreadsheetRejected('INCOMPLETE_SPREADSHEET')
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    office_rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package_rel = 'http://schemas.openxmlformats.org/package/2006/relationships'
    content = 'http://schemas.openxmlformats.org/package/2006/content-types'
    if parts['[Content_Types].xml'].tag != '{'+content+'}Types':
        raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    for node in parts['[Content_Types].xml']:
        kind = node.get('ContentType', '').lower()
        if ('macro' in kind or 'vba' in kind or 'ole' in kind or not kind.endswith(('+xml', '/xml'))):
            raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    declarations = {}
    for node in parts['[Content_Types].xml'].findall('{'+content+'}Override'):
        part = node.get('PartName', '')
        if part in declarations:
            raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
        declarations[part] = node.get('ContentType')
    if declarations.get('/xl/workbook.xml') != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml':
        raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    for part in parts:
        if part.startswith('xl/worksheets/') and declarations.get('/'+part) != 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml':
            raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    relations = {}
    for path in ('_rels/.rels', 'xl/_rels/workbook.xml.rels'):
        root = parts[path]
        if root.tag != '{'+package_rel+'}Relationships':
            raise SpreadsheetRejected('MALFORMED_RELATIONSHIP')
        seen = set()
        for node in root:
            target = node.get('Target', '')
            relation = node.get('Type', '').removeprefix(office_rel+'/')
            if (node.get('TargetMode', 'Internal') != 'Internal' or relation not in
                {'officeDocument', 'worksheet', 'styles', 'sharedStrings', 'theme',
                 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties', 'extended-properties'}
                or not target or '\\' in target or ':' in target or '..' in PurePosixPath(target).parts):
                raise SpreadsheetRejected('EXTERNAL_OR_UNSUPPORTED_RELATIONSHIP')
            name = target.lstrip('/') if target.startswith('/') else ('xl/' if path.startswith('xl/') else '')+target
            if name not in parts or not node.get('Id') or node.get('Id') in seen:
                raise SpreadsheetRejected('MALFORMED_RELATIONSHIP')
            seen.add(node.get('Id'))
            if path.startswith('xl/'):
                if relation not in {'worksheet', 'styles', 'sharedStrings', 'theme'}:
                    raise SpreadsheetRejected('MALFORMED_RELATIONSHIP')
                relations[node.get('Id')] = (relation, name)
            elif ((relation == 'officeDocument' and name != 'xl/workbook.xml')
                  or (relation != 'officeDocument' and not name.startswith('docProps/'))):
                raise SpreadsheetRejected('MALFORMED_RELATIONSHIP')
        if path == '_rels/.rels' and sum(n.get('Type') == office_rel+'/officeDocument' for n in root) != 1:
            raise SpreadsheetRejected('MALFORMED_RELATIONSHIP')
    workbook = parts['xl/workbook.xml']
    if workbook.tag != '{'+main+'}workbook':
        raise SpreadsheetRejected('UNSUPPORTED_SPREADSHEET_FORMAT')
    sheets = workbook.find('{'+main+'}sheets')
    if sheets is None or not 1 <= len(sheets) <= MAX_SHEETS:
        raise SpreadsheetRejected('SPREADSHEET_SHEET_LIMIT')
    rows, used, names = [], set(), set()
    for node in sheets:
        relation = relations.get(node.get('{'+office_rel+'}id'))
        name = node.get('name', '')
        if (not name or name.casefold() in names or node.get('state', 'visible') != 'visible'
            or not relation or relation[0] != 'worksheet' or relation[1] in used):
            raise SpreadsheetRejected('UNSUPPORTED_SHEET_MAPPING')
        names.add(name.casefold())
        used.add(relation[1])
        sheet = parts[relation[1]]
        if sheet.tag != '{'+main+'}worksheet':
            raise SpreadsheetRejected('MALFORMED_SPREADSHEET')
        data = sheet.find('{'+main+'}sheetData')
        if data is None or len(data) > MAX_ROWS+1:
            raise SpreadsheetRejected('SPREADSHEET_ROW_LIMIT')
        for row in data:
            if len(row) > MAX_COLUMNS or int(row.get('r', '1')) > MAX_ROWS+1:
                raise SpreadsheetRejected('SPREADSHEET_DIMENSION_LIMIT')
            for cell in row:
                coordinate = cell.get('r')
                if coordinate:
                    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', coordinate)
                    if not match:
                        raise SpreadsheetRejected('SPREADSHEET_DIMENSION_LIMIT')
                    column = 0
                    for char in match[1]:
                        column = column*26+ord(char)-64
                    if column > MAX_COLUMNS or int(match[2]) > MAX_ROWS+1:
                        raise SpreadsheetRejected('SPREADSHEET_DIMENSION_LIMIT')
        rows.append(max(0, len(data)-1))  # Inspection count, not parsed data rows.
    if used != {name for name in parts if name.startswith('xl/worksheets/')}:
        raise SpreadsheetRejected('UNMAPPED_SPREADSHEET_PART')
    for root in parts.values():
        for node in root.iter():
            local = node.tag.split('}')[-1]
            if local in {'f', 'definedName', 'externalReference', 'include', 'oleObject', 'drawing', 'tablePart', 'hyperlink', 'connection'}:
                raise SpreadsheetRejected('FORMULA_OR_ACTIVE_CONTENT')
            if local == 'c' and node.get('t') == 'e':
                raise SpreadsheetRejected('SPREADSHEET_ERROR_CELL')
    return SpreadsheetInspection(hashlib.sha256(payload).hexdigest(), len(payload), tuple(rows))
