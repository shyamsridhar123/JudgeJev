"""Run the local app, optionally accepting the existing key without echoing it."""
import argparse
import os
from getpass import getpass
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env', override=False)

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8780)
parser.add_argument("--ask-key", action="store_true")
options = parser.parse_args()
if options.ask_key and not (os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY")):
    key = getpass("Existing TypeSafe key (not saved): ").strip()
    if key:
        os.environ["TYPESAFE_API_KEY"] = key
    key = None
uvicorn.run("app:app", host="127.0.0.1", port=options.port, access_log=False, log_level="warning")
