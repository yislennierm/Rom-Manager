#!/usr/bin/env python3
import os
import json
from jsonschema import validate, ValidationError

# Resolve base directories dynamically
BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # project root (one level up from /utils)
DATA_DIR = os.path.join(BASE_DIR, "data")

# Define absolute file paths
PROVIDER_FILE = os.path.join(DATA_DIR, "providers","providers.json")
SCHEMA_FILE = os.path.join(DATA_DIR, "schema", "provider_schema.json")

# Load and validate provider data
try:
    with open(PROVIDER_FILE) as f:
        providers = json.load(f)
    with open(SCHEMA_FILE) as f:
        schema = json.load(f)
except FileNotFoundError as e:
    print(f"❌ Missing file: {e.filename}")
    exit(1)

try:
    validate(instance=providers, schema=schema)
    print("✅ providers.json is valid.")
except ValidationError as e:
    print("❌ Validation error:")
    print("Path:", list(e.path))
    print("Message:", e.message)
    exit(1)
