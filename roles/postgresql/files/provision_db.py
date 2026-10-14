#!/usr/bin/env python3
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Provision a PostgreSQL database and user.
Usage: python3 provision_db.py <db> <user> <admin> <base64-password>
Password is passed as base64 to avoid all shell/shlex quoting issues.
"""
import base64
import subprocess
import sys

# Absolute path, not a PATH lookup. This runs inside the CNPG postgres image,
# where /usr/bin/psql is the postgresql-common wrapper.
PSQL = "/usr/bin/psql"

db = sys.argv[1]
user = sys.argv[2]
admin = sys.argv[3]
pw = base64.b64decode(sys.argv[4]).decode()

if not pw:
    print("ERROR: password is empty", file=sys.stderr)
    sys.exit(1)


def q(sql, d="postgres", variables=None):
    cmd = [PSQL, "-U", admin, "-d", d, "-tA"]
    if variables:
        for k, v in variables.items():
            cmd.extend(["-v", f"{k}={v}"])
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise Exception(f"Query failed: {r.stderr}")
    return r.stdout.strip()


def run(sql, d="postgres"):
    result = subprocess.run(
        [PSQL, "-U", admin, "-d", d, "-c", sql],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise Exception(f"SQL failed: {result.stderr}")


# Create database if not exists
if "1" not in q(
    "SELECT 1 FROM pg_database WHERE datname=:'dbname'",
    variables={"dbname": db},
):
    run(f'CREATE DATABASE "{db}"')

# Create or update user — format('%I/%L') handles identifier/literal escaping
if "1" not in q(
    "SELECT 1 FROM pg_roles WHERE rolname=:'uname'",
    variables={"uname": user},
):
    run(f"DO $$ BEGIN EXECUTE format('CREATE USER %I WITH PASSWORD %L', "
        f"'{user}', '{pw.replace(chr(39), chr(39)*2)}'); END $$")
else:
    run(f"DO $$ BEGIN EXECUTE format('ALTER USER %I WITH PASSWORD %L', "
        f"'{user}', '{pw.replace(chr(39), chr(39)*2)}'); END $$")

# Grant privileges
run(f'GRANT ALL PRIVILEGES ON DATABASE "{db}" TO "{user}"')
run(f'GRANT ALL ON SCHEMA public TO "{user}"', d=db)

# Verify
if "1" not in q(
    "SELECT 1 FROM pg_roles WHERE rolname=:'uname'",
    variables={"uname": user},
):
    raise Exception(f"User {user} was not created")

print(f"OK: {db}/{user}")
