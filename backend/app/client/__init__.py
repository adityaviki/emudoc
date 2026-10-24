"""The programs Emudoc hands its members to run on their own computers: the upload client,
`emudoc_upload.py`, which sends a folder into a project or goes on sending what a scanner saves
to one, through the API with a key. The app serves it for download (`GET
/api/help/emudoc_upload.py`, app/api/help.py), and the help names its figures from it.

It is a script of the standard library alone, for any Python from 3.9 (ruff checks it as 3.9,
`per-file-target-version` in pyproject.toml), so it imports nothing of the app.
"""

from pathlib import Path

UPLOAD_CLIENT = Path(__file__).with_name("emudoc_upload.py")
