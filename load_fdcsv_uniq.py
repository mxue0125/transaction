#!/usr/bin/env python3

import mysql.connector
import csv
import os
import glob
import argparse
import configparser
from datetime import datetime

# Load config
config = configparser.ConfigParser()
config.read(os.path.expanduser('~/.ssh/db_config.ini'))

DB_CONFIG = {
    'host'              : config['database']['host'],
    'user'              : config['database']['user'],
    'password'          : config['database']['password'],
    'database'          : config['database']['database'],
    'allow_local_infile': True
}

def is_date_string(value):
    """Return True if the string matches a date format like M/D/YYYY or MM/DD/YY."""
    if not value:
        return False
    return bool(re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', str(value).strip()))

def parse_date(date_str):
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), '%m/%d/%Y').date()
    except ValueError:
        return None

def parse_decimal(value):
    return value.strip() if value and value.strip() else None

def is_file_loaded(cursor, source_file, table):
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE Source_File = %s",
        (source_file,)
    )
    return cursor.fetchone()[0] > 0

def load_csv_to_staging(cursor, filepath, source_file):
    rows_inserted = 0
    rows_skipped = 0
    rows_duplicate = 0
    empty_row = 0

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
## debug the data
        # for i, row in enumerate(reader):
        #     if i == 0:
        #         print(f"First row sample: {row}")   # see all keys and values
        #     run_date_str = row.get('Run Date', '')
        #     print(f"Row {i}: Run Date raw = '{run_date_str}'")
        #     run_date = parse_date(run_date_str)
        #     if run_date is None:
        #         print(f"  -> SKIPPED (invalid date)")
        #         rows_skipped += 1
        #         continue

        for row in reader:
            run_date = parse_date(row.get('Run Date', ''))
            if run_date is None:
                rows_skipped += 1
                continue

            cursor.execute("""
                INSERT IGNORE INTO fd_transaction_staging (
                    Run_Date_Raw, Account, Account_Number, Action, Symbol,      Description,
                    Type, Price, Quantity, Commission, Fees, Accrued_Interest,  Amount,
                    Settlement_Date_Raw, Source_File
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,   %s, %s)
            """, (
                row.get('Run Date', '').strip(),
                row.get('Account', '').strip() or None,
                row.get('Account Number', '').strip() or None,
                row.get('Action', '').strip() or None,
                row.get('Symbol', '').strip() or None,
                row.get('Description', '').strip() or None,
                row.get('Type', '').strip() or None,
                parse_decimal(row.get('Price ($)', '')),
                parse_decimal(row.get('Quantity', '')),
                parse_decimal(row.get('Commission ($)', '')),
                parse_decimal(row.get('Fees ($)', '')),
                parse_decimal(row.get('Accrued Interest ($)', '')),
                parse_decimal(row.get('Amount ($)', '')),
                row.get('Settlement Date', '').strip() or None,
                source_file
            ))
            if cursor.rowcount:
                rows_inserted += 1
            else:
                rows_duplicate += 1
    return rows_inserted, rows_skipped, rows_duplicate

def insert_staging_to_final(cursor, source_file):
    cursor.execute("""
        INSERT IGNORE INTO fd_transaction (
            Run_Date, Account, Account_Number, Action, Symbol, Description, Type, Price,
            Quantity, Commission, Fees, Accrued_Interest, Amount, Settlement_Date, Source_File
        )
        SELECT
            STR_TO_DATE(Run_Date_Raw, '%m/%d/%Y'),
            Account, Account_Number, Action, Symbol, Description,
            Type, Price, Quantity, Commission, Fees, Accrued_Interest, Amount,
            CASE
                WHEN Settlement_Date_Raw REGEXP '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'
                THEN STR_TO_DATE(Settlement_Date_Raw, '%m/%d/%Y')
                ELSE NULL
            END,
            Source_File
        FROM fd_transaction_staging
        WHERE Run_Date_Raw REGEXP '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'
          AND Source_File = %s
    """, (source_file,))
    return cursor.rowcount

def process_file(filepath, conn):
    source_file = os.path.basename(filepath)
    cursor = conn.cursor()

    try:
        print(f"[LOAD] Loading {source_file} into staging...")
        rows_inserted, rows_skipped, rows_duplicate = load_csv_to_staging(cursor, filepath, source_file)
        conn.commit()
        print(f"       Staging: {rows_inserted} inserted, {rows_skipped} skipped, {rows_duplicate} duplicate.")

        print(f"[INSERT] Moving {source_file} to fd_transaction...")
        final_count = insert_staging_to_final(cursor, source_file)
        conn.commit()
        print(f"         fd_transaction: {final_count} rows inserted.")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] {source_file}: {e}")
        raise

    finally:
        cursor.close()

def resolve_files(patterns):
    """Expand glob patterns and return sorted unique file paths."""
    files = []
    for pattern in patterns:
        matched = glob.glob(pattern, recursive=True)
        if not matched:
            print(f"[WARN] No files matched: {pattern}")
        files.extend(matched)

    # Deduplicate and sort
    files = sorted(set(os.path.abspath(f) for f in files if os.path.isfile(f)))
    return files

def main():
    parser = argparse.ArgumentParser(
        description='Load Fidelity CSV transaction files into MySQL.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Examples:
  # Single file
  python load_fd.py /mnt/e/statements/Accounts_History2025Q4.csv

  # Multiple files
  python load_fd.py /mnt/e/statements/Accounts_History2025Q4.csv /mnt/e/statements/Accounts_History2026Q1.csv

  # Glob pattern - all CSVs in folder
  python load_fd.py "/mnt/e/statements/*.csv"

  # Glob pattern - all CSVs including subfolders
  python load_fd.py "/mnt/e/statements/**/*.csv"

  # Mix of files and patterns
  python load_fd.py "/mnt/e/statements/2025/*.csv" "/mnt/e/statements/2026/*.csv"
        """
    )
    parser.add_argument(
        'files',
        nargs='+',
        help='One or more file paths or glob patterns (quote patterns to prevent shell expansion)'
    )
    args = parser.parse_args()

    # Resolve all patterns to actual file paths
    filepaths = resolve_files(args.files)

    if not filepaths:
        print("[ERROR] No valid CSV files found. Exiting.")
        return

    print(f"Found {len(filepaths)} file(s) to process.\n")

    # Single connection for all files
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        for filepath in filepaths:
            process_file(filepath, conn)
            print()
    finally:
        conn.close()

if __name__ == '__main__':
    main()