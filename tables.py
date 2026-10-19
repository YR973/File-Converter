"""Local, value-only spreadsheet conversion; never executes formulas or macros."""
import csv
import json
from pathlib import Path

import openpyxl
from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.table import Table, TableRow, TableCell
from odf.text import P
from odf import teletype

FORMATS = {'csv', 'tsv', 'json', 'xlsx', 'ods'}


def read_tables(source: Path) -> dict[str, list[list]]:
    ext = source.suffix.lower()[1:]
    if ext == 'xlsx':
        workbook = openpyxl.load_workbook(source, read_only=True, data_only=False, keep_links=False)
        try:
            result = {}
            for sheet in workbook:
                rows = []
                for cells in sheet.iter_rows():
                    if any(cell.data_type == 'f' for cell in cells):
                        raise ValueError('Formula spreadsheets require exporting values from your spreadsheet app first.')
                    row = [cell.value for cell in cells]
                    if any(value is not None and not isinstance(value, (str, int, float, bool)) for value in row):
                        raise ValueError('Date and custom cell types require a values-only CSV export first.')
                    rows.append(row)
                result[sheet.title] = rows
            return result
        finally:
            workbook.close()
    if ext == 'ods':
        result = {}
        document = load(str(source))
        for sheet in document.spreadsheet.getElementsByType(Table):
            rows = []
            for row in sheet.getElementsByType(TableRow):
                values = []
                for cell in row.getElementsByType(TableCell):
                    if cell.getAttribute('formula'):
                        raise ValueError('Formula spreadsheets require exporting values from your spreadsheet app first.')
                    kind = cell.getAttribute('valuetype')
                    if kind in ('float', 'currency', 'percentage'):
                        value = float(cell.getAttribute('value'))
                    elif kind == 'boolean':
                        value = cell.getAttribute('booleanvalue') == 'true'
                    elif kind in ('date', 'time'):
                        raise ValueError('Date cells require a values-only CSV export first.')
                    else:
                        value = teletype.extractText(cell)
                    count = int(cell.getAttribute('numbercolumnsrepeated') or 1)
                    if count > 16384 or len(values) + count > 16384:
                        raise ValueError('Spreadsheet has too many repeated columns.')
                    values.extend([value] * count)
                count = int(row.getAttribute('numberrowsrepeated') or 1)
                if count > 100000 or len(rows) + count > 100000:
                    raise ValueError('Spreadsheet has too many repeated rows.')
                rows.extend([list(values) for _ in range(count)])
            result[sheet.getAttribute('name')] = rows
        return result
    if ext == 'json':
        records = json.loads(source.read_text(encoding='utf-8-sig'))
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError('Table conversion requires a JSON array of objects.')
        if any(isinstance(value, (dict, list)) for row in records for value in row.values()):
            raise ValueError('Nested JSON cannot be represented as a table without data loss.')
        headers = list(dict.fromkeys(key for row in records for key in row))
        if not headers:
            raise ValueError('An empty JSON table has no column headers.')
        return {'Sheet1': [headers] + [[row.get(key) for key in headers] for row in records]}
    with source.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.reader(handle, delimiter='\t' if ext == 'tsv' else ','))
    if not rows or not rows[0] or len(set(rows[0])) != len(rows[0]):
        raise ValueError('A table must have unique column headers.')
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError('Table rows must match the header column count.')
    return {'Sheet1': rows}


def convert_table(source: Path, destination: Path) -> None:
    tables = read_tables(source)
    target = destination.suffix.lower()[1:]
    if target == 'xlsx':
        workbook = openpyxl.Workbook()
        workbook.remove(workbook.active)
        for name, rows in tables.items():
            sheet = workbook.create_sheet(name)
            for row in rows:
                sheet.append(row)
                # Strings beginning '=' stay literal, rather than becoming executable formulas.
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'
        if not workbook.sheetnames:
            raise ValueError('The spreadsheet has no sheets.')
        workbook.save(destination)
    elif target == 'ods':
        document = OpenDocumentSpreadsheet()
        for name, rows in tables.items():
            sheet = Table(name=name)
            for values in rows:
                row = TableRow()
                for value in values:
                    if isinstance(value, bool):
                        cell = TableCell(valuetype='boolean', booleanvalue=str(value).lower())
                    elif isinstance(value, (float, int)):
                        cell = TableCell(valuetype='float', value=value)
                    else:
                        cell = TableCell(valuetype='string')
                    cell.addElement(P(text='' if value is None else str(value)))
                    row.addElement(cell)
                sheet.addElement(row)
            document.spreadsheet.addElement(sheet)
        document.save(str(destination))
    else:
        if len(tables) != 1:
            raise ValueError('Flat formats require exactly one sheet; export sheets separately first.')
        rows = next(iter(tables.values()))
        if not rows:
            raise ValueError('The spreadsheet is empty.')
        if target == 'json':
            headers = rows[0]
            if any(not isinstance(key, str) or not key for key in headers) or len(set(headers)) != len(headers):
                raise ValueError('JSON export requires unique nonempty text headers in the first row.')
            records = [dict(zip(headers, row + [None] * (len(headers) - len(row)))) for row in rows[1:]]
            destination.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            with destination.open('w', encoding='utf-8', newline='') as handle:
                csv.writer(handle, delimiter='\t' if target == 'tsv' else ',').writerows(rows)
