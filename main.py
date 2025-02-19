import os
import argparse
from pathlib import Path
from textwrap import wrap, dedent
import queue

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate(filename_token, filename_credentials):
    if not Path(filename_credentials).exists():
        raise PermissionError(
            "You must provide a Google API client_secret.json file via --app-credentials. See --help for guidance."
        )

    creds = None
    if Path(filename_token).exists():
        creds = Credentials.from_authorized_user_file(filename_token, SCOPES)
    if not creds or not creds.valid:
        if creds and not creds.expired and creds.refresh_token:
            creds.Refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                filename_credentials, scopes=SCOPES
            )
            creds = flow.run_local_server(open_browser=False)
            with open(filename_token, "w") as token:
                token.write(creds.to_json())
    return creds


class DriveFiles:
    """Use this wrapper class to ensure proper flags are set on all requests"""

    def __init__(self, drive_service):
        self.drive = drive_service

    def list(self, *args, **kwargs):
        return (
            self.drive.files()
            .list(*args, **{**kwargs, "supportsAllDrives": True})
            .execute()
        )

    def copy(self, *args, **kwargs):
        return (
            self.drive.files()
            .copy(*args, **{**kwargs, "supportsAllDrives": True})
            .execute()
        )

    def create(self, *args, **kwargs):
        return (
            self.drive.files()
            .create(*args, **{**kwargs, "supportsAllDrives": True})
            .execute()
        )

    def get(self, *args, **kwargs):
        return (
            self.drive.files()
            .get(*args, **{**kwargs, "supportsAllDrives": True})
            .execute()
        )

    def update(self, *args, **kwargs):
        return (
            self.drive.files()
            .update(*args, **{**kwargs, "supportsAllDrives": True})
            .execute()
        )


def listdir(drive, parent_id):
    page_token = None
    while True:
        response = drive.list(
            q=f'"{parent_id}" in parents and trashed = false',
            pageSize=1000,
            pageToken=page_token,
            # by providing fields we can specify shortcutDetails which avoids an extra call when copying shortcuts
            # and modifiedTime and size which we use when deciding whether to copy
            # NB: you can set files(*) to see _all_ available fields
            fields="nextPageToken, files(*)",
        )
        yield from response["files"]
        page_token = response.get("nextPageToken", None)
        if not page_token:
            break


def get_one(drive, q):
    try:
        print(q)
        return drive.list(q=q)["files"][0]
    except IndexError:
        return None


def run(drive, source_root, dest_root):
    q = queue.Queue()

    for item in listdir(drive, source_root):
        q.put(("/", item, source_root, dest_root))

    while not q.empty():
        (folder_name, item, parent_id, dest) = q.get()

        item_path = os.path.join(folder_name, item["name"])

        # if folder, create destination folder and add children to queue
        if item["mimeType"] == "application/vnd.google-apps.folder":
            maybe_f = get_one(
                drive,
                (
                    f'"{dest}" in parents and trashed = false "'
                    f'and name = "{item["name"]}" and mimeType = "{item["mimeType"]}"'
                ),
            )
            if maybe_f:
                folder = maybe_f
            else:
                print(f"Create folder {item_path}")
                folder = drive.create(
                    body={
                        **{key: item[key] for key in ["name", "mimeType"]},
                        "parents": [dest],
                    },
                )

            for child_item in listdir(drive, item["id"]):
                q.put(
                    (
                        os.path.join(item_path, child_item["id"]),
                        child_item,
                        item["id"],
                        folder["id"],
                    )
                )

            continue

        # else, reparent
        # TODO: handle errors, optionally creating shortcuts on certain
        drive.update(fileId=item["id"], addParents=dest, removeParents=parent_id)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app-credentials",
        help=dedent(
            """
        path to client_secret.json (default ./client_secret.json)
        See https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred.
        You should make sure to create a "Desktop App" credential in the developer console.
        """
        ),
        type=argparse.FileType("r"),
        default=os.path.join(os.getcwd(), "client_secret.json"),
    )

    parser.add_argument(
        "source",
        help=(
            "Folder or file to copy from, e.g. "
            "https://drive.google.com/drive/folders/1DCwcbejwdN-Clc5bgk5CJfjo3FQaGTaQ"
            " or 1DCwcbejwdN-Clc5bgk5CJfjo3FQaGTaQ",
        ),
    )
    parser.add_argument(
        "dest",
        help=(
            "To team drive, e.g. "
            "https://drive.google.com/drive/folders/13R6I-wx4e4Axw5SiIaF7VjhpKXByA_Qz"
            " or 13R6I-wx4e4Axw5SiIaF7VjhpKXByA_Qz"
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()

    from_id = os.path.basename(args.source)
    to_id = os.path.basename(args.dest)
    user_credentials = os.path.join(os.getcwd(), "token.json")

    creds = authenticate(user_credentials, args.app_credentials.name)

    with build("drive", "v3", credentials=creds) as service:
        run(DriveFiles(service), from_id, to_id)


if __name__ == "__main__":
    main()
