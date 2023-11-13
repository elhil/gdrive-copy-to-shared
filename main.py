import os
import datetime
import argparse
import shutil
from pathlib import Path
from textwrap import wrap, dedent
import queue

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import xdg.BaseDirectory

SCOPES = ["https://www.googleapis.com/auth/drive"]

parser = argparse.ArgumentParser()
parser.add_argument(
    "--app-credentials",
    help=dedent(
        """
    client_secret.json file downloaded from https://console.cloud.google.com
    which identifies this instance of the application.

    This will be cached, so it only needs to be provided once.

    See https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred.
    You should make sure to create a "Desktop App" credential in the developer console.
    """
    ),
    type=argparse.FileType("r"),
)
parser.add_argument(
    "-c",
    "--checksum",
    help="compare files based on checksums rather; only works on real files, Google Docs don't have checksums",
    action="store_true",
)
parser.add_argument(
    "--delete", help="delete extraneous files from dest dirs", action="store_true"
)
parser.add_argument(
    "-l", "--links", help="copy shortcuts as shortcuts", action="store_true"
)
parser.add_argument(
    "source",
    help="Folder or file to copy from, e.g. https://drive.google.com/drive/folders/ 1DCwcbejwdN-Clc5bgk5CJfjo3FQaGTaQ or 1DCwcbejwdN-Clc5bgk5CJfjo3FQaGTaQ",
)
parser.add_argument(
    "dest",
    help="To team drive, e.g. https://drive.google.com/drive/folders/13R6I-wx4e4Axw5SiIaF7VjhpKXByA_Qz or 13R6I-wx4e4Axw5SiIaF7VjhpKXByA_Qz",
)


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


args = parser.parse_args()


def listdir(drive, id):
    pageToken = None
    while True:
        response = (
            drive.files()
            .list(
                q=f'"{id}" in parents and trashed = false',
                pageSize=1000,
                pageToken=pageToken,
                # by providing fields we can specify shortcutDetails which avoids an extra call when copying shortcuts
                # and modifiedTime and size which we use when deciding whether to copy
                # NB: you can set files(*) to see _all_ available fields
                fields=f"nextPageToken, files(id, name, mimeType, size, kind, modifiedTime, shortcutDetails{', sha256Checksum' if args.checksum else ''})",
            )
            .execute()
        )
        yield from response["files"]
        pageToken = response.get("nextPageToken", None)
        if not pageToken:
            break



def recurse_folders(drive, source, dest):
    q = queue.Queue()

    q.put(("./", source, dest))
    while not q.empty():
        (folder_name, source, dest) = q.get()

        for item in listdir(drive, source):
            print(os.path.join(folder_name, item["name"]))

            if item["mimeType"] == "application/vnd.google-apps.folder":
                name = item["name"]
                # TODO: use listdir() to batch this instead
                dest_items = (
                    drive.files()
                    .list(
                        q=f'"{dest}" in parents and trashed = false and name = "{name}" and mimeType = "application/vnd.google-apps.folder"',
                    )
                    .execute()
                )
                if len(dest_items["files"]):
                    dest_item = dest_items["files"][0]
                else:
                    dest_item = (
                        drive.files()
                        .create(
                            body={
                                "name": item["name"],
                                "mimeType": item["mimeType"],
                                "parents": [dest],
                            },
                            fields="id",
                        )
                        .execute()
                    )

                q.put(
                    (
                        os.path.join(
                            folder_name,
                            item["name"],
                            item["id"],
                        ),
                        item["id"],
                        dest_item["id"],
                    )
                )

            elif (
                args.links == False
                and item["mimeType"] == "application/vnd.google-apps.shortcut"
            ):
                # copy the *contents* of a symlink
                # print("symlink()")
                if (
                    item["shortcutDetails"]["targetMimeType"]
                    == "application/vnd.google-apps.folder"
                ):
                    # treat it like a regular folder
                    dest_item = (
                        drive.files()
                        .create(
                            body={
                                "name": item["name"],
                                "mimeType": item["shortcutDetails"]["targetMimeType"],
                                "parents": [dest],
                            },
                            fields="id",
                        )
                        .execute()
                    )

                    q.put(
                        (
                            os.path.join(
                                folder_name,
                                item["name"],
                                item["id"],
                            ),
                            item["shortcutDetails"]["targetId"],
                            dest_item["id"],
                        )
                    )
                else:
                    dest_item = (
                        drive.files()
                        .copy(
                            fileId=item["shortcutDetails"]["targetId"],
                            body={
                                "name": item["name"],
                                "parents": [dest],
                            },
                        )
                        .execute()
                    )
            else:
                # copy a regular file (or the target of a shortcut)
                # TODO: what happens if a shortcut points to a folder?

                # To decide if we need to copy:
                # - is the size different?
                # - is the modification time on the old file newer?

                name = item["name"]
                # TODO: use listdir() to batch this instead
                dest_items = (
                    drive.files()
                    .list(
                        q=f'"{dest}" in parents and trashed = false and name = "{name}"',
                        fields="files(id, mimeType, modifiedTime, size, sha256Checksum)",
                    )
                    .execute()
                )

                # TODO: Google Drive allows multiple files with the same name in a folder
                #   is there a way we can handle matching up which file is supposed to be which?
                dest_item = (
                    dest_items["files"][0]
                    if dest_items and len(dest_items["files"])
                    else None
                )

                item_modified = datetime.datetime.fromisoformat(item["modifiedTime"])
                dest_item_modified = (
                    datetime.datetime.fromisoformat(dest_item["modifiedTime"])
                    if dest_item
                    else None
                )

                if (
                    (not dest_item)
                    or (item["mimeType"] != dest_item["mimeType"])
                    or (
                        args.checksum
                        and "sha256Checksum" in item
                        and "sha256Checksum" in dest_item
                        and item["sha256Checksum"] != dest_item["sha256Checksum"]
                    )
                    or (item_modified > dest_item_modified)
                ):
                    print("copy()")
                    drive.files().copy(
                        fileId=item["id"],
                        body={
                            "name": item["name"],
                            "parents": [dest],
                        },
                    ).execute()

                    # delete(!) the other(s) with the same name
                    for f in dest_items["files"]:
                        print("delete()")
                        drive.files().delete(fileId=f["id"]).execute()


from_id = os.path.basename(args.source)  # TODO: input validation
to_id = os.path.basename(args.dest)

app_credentials = os.path.join(
    xdg.BaseDirectory.save_cache_path("gdrive-rync"), "client_secret.json"
)
user_credentials = os.path.join(
    xdg.BaseDirectory.save_cache_path("gdrive-rync"), "token.json"
)

# if args.app_credentials:
#    shutil.copy(args.app_credentials, app_credentials)

if args.app_credentials:
    with open(app_credentials, "w") as app_credentials_:
        shutil.copyfileobj(args.app_credentials, app_credentials_)

        # user creds are invalid if the app creds they are contained in change, so toss them
        # TODO: check if the credentials are equal before overwriting; if so, don't invalidate?
        if Path(user_credentials).exists():
            os.unlink(user_credentials)

creds = authenticate(user_credentials, app_credentials)

with build("drive", "v3", credentials=creds) as service:
    # list directory for now
    # r = list_contents_of_folder(from_id, service)
    recurse_folders(service, from_id, to_id)
