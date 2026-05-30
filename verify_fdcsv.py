import csv
import sys

with open(sys.argv[1], newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    print("Headers:", reader.fieldnames)
    for i, row in enumerate(reader):
        run_date_val = row.get('Run Date', '')
        if not run_date_val or not run_date_val.strip():
            print(f"Row {i+2}: Run Date is EMPTY | full row: {dict(row)}")
        else:
            from datetime import datetime
            try:
                datetime.strptime(run_date_val.strip(), '%m/%d/%Y')
            except ValueError:
                print(f"Row {i+2}: INVALID Run Date = {repr(run_date_val)} | full row: {dict(row)}")