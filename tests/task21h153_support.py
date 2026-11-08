"""Synthetic-only I/O harness; processing functions are the real application code.

No database publication is simulated by this harness. Use PG fixtures for that.
"""
import io
import zipfile
from uuid import uuid4

from app.storage.versioned_artifacts import MemoryVersionedClient, VersionedArtifacts


CSV = b'''id,city,amount,ordered_on,quantity
1, Sydney ,0.10,2026-01-01,2
1,Sydney,0.10,2026-01-01,2
2,Melbourne,9007199254740993.01,2026-01-02,3
3,,,2026-01-03,4
4,Perth,-2.50,2026-01-04,5
5,Hobart,8.25,invalid-date,6
6,Adelaide,12.50,2026-01-06,7
7,Darwin,nope,2026-01-07,8
'''


def rules():
    return [(i, 1, column, kind, config) for i, (column, kind, config) in enumerate([
        ('id', 'NOT_NULL', {'required': True}),
        ('id', 'UNIQUE', {'required': True}),
        ('city', 'NOT_NULL', {'required': True}),
        ('amount', 'DATA_TYPE', {'type': 'DECIMAL'}),
        ('amount', 'NOT_NULL', {'required': True}),
        ('amount', 'ALLOW_NEGATIVE', {'allow_negative': False}),
        ('ordered_on', 'DATA_TYPE', {'type': 'DATE'}),
        ('ordered_on', 'NOT_NULL', {'required': True}),
    ], 1)]


def workbook():
    """Small genuine XLSX container, generated with stdlib; no Excel dependency."""
    from xml.etree.ElementTree import Element, SubElement, tostring
    import csv
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet = Element('worksheet', xmlns=ns)
    data = SubElement(sheet, 'sheetData')
    for values in csv.reader(io.StringIO(CSV.decode())):
        row = SubElement(data, 'row')
        for value in values:
            cell = SubElement(row, 'c', t='inlineStr')
            SubElement(SubElement(cell, 'is'), 't').text = value
    output = io.BytesIO()
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Orders" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': tostring(sheet),
    }
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


class StageObjects:
    """Registered in-memory source/output aliases, immutable physical objects."""
    def __init__(self, payload):
        self.client = MemoryVersionedClient()
        self.store = VersionedArtifacts(self.client, 'synthetic-only')
        self.identities = {'raw/input.csv': self.client.seed('raw/input.csv', payload)}

    def get_object(self, *, Bucket, Key):
        return {'Body': io.BytesIO(self.store.read(self.identities[Key]))}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        identity = self.store.put(f'artifact-candidates/{uuid4()}/data.parquet', Body)
        self.identities[Key] = identity
        return {'VersionId': identity.version}

    def read(self, key):
        return self.store.read(self.identities[key])


def run_processors(payload, profile_lookup):
    """profile_lookup patches only DB profile retrieval, never planner/algorithms."""
    from app.processing import bronze_processor, silver_processor, gold_processor
    from app.processing_worker import io_context
    objects = StageObjects(payload)
    with io_context.using(objects):
        bronze = bronze_processor.run_bronze_stage(41, 'raw/input.csv')
        # Match the existing profile repository tuple layout; values come from
        # the actual Bronze profiler, not synthetic inferred transformations.
        profiles = [(i, 41, p['column_name'], p['inferred_type'], p['null_count'],
                     p['distinct_count'], p['duplicate_count'], p['min_value'],
                     p['max_value'], p['negative_count']) for i, p in enumerate(bronze['profiles'])]
        profile_lookup(gold_processor, profiles)
        silver = silver_processor.process_silver(41, 9, 1, 'synthetic-only', approved_rules=rules())
        gold = gold_processor.process_gold(41, 9, 1, 'synthetic-only', silver['silver_key'], 73)
    return objects, bronze, silver, gold
