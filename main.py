import os
import argparse
from pathlib import Path
from textwrap import dedent
import queue
from ssl import SSLEOFError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate(filename_token, filename_credentials):
    """Get an auth token by running a web service at localhost and clicking through an OAuth link"""

    if not Path(filename_credentials).exists():
        raise PermissionError(
            "You must provide a Google API client_secret.json file via --app-credentials. See --help for guidance."
        )

    creds = None
    if Path(filename_token).exists():
        creds = Credentials.from_authorized_user_file(filename_token, SCOPES)
    if not creds or not creds.valid:
        if creds and not creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(filename_credentials, scopes=SCOPES)
            creds = flow.run_local_server(open_browser=False)
            with open(filename_token, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
    return creds


# pylint: disable=missing-function-docstring
class DriveFiles:
    """Use this wrapper class to ensure proper flags are set & errors handled on all requests"""

    def __init__(self, auth_f):
        self.auth_f = auth_f

        self._build_drive()

    def _build_drive(self):
        creds = self.auth_f()
        self.drive = build("drive", "v3", credentials=creds)

    def _wrapmethod(self, method_generator, *args, **kwargs):
        """
        Wrap googleapiclient methods, injecting flags which, when missed, cause silent failure
        to list anything in a shared drive. Also handle SSL timeout errors. To achieve this,
        methd_generator must be a lambda or function that takes a drive service and returns the
        method to be called. This is needed because the service is regenerated on SSL re-auth
        """
        kwargs_wrapped = {**kwargs, "supportsAllDrives": True}
        try:
            return method_generator(self.drive)(*args, **kwargs_wrapped).execute()
        except SSLEOFError:
            print("Auth token expired. Reauthenticating...")
            self._build_drive()
            return method_generator(self.drive)(*args, **kwargs_wrapped).execute()

    def list(self, *args, **kwargs):
        return self._wrapmethod(
            lambda drive: drive.files().list,
            *args,
            **{
                **kwargs,
                "includeItemsFromAllDrives": True,
            },
        )

    def copy(self, *args, **kwargs):
        return self._wrapmethod(lambda drive: drive.files().copy, *args, **kwargs)

    def create(self, *args, **kwargs):
        return self._wrapmethod(lambda drive: drive.files().create, *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._wrapmethod(
            lambda drive: drive.files().get,
            *args,
            **{
                **kwargs,
                "includeItemsFromAllDrives": True,
            },
        )

    def update(self, *args, **kwargs):
        return self._wrapmethod(lambda drive: drive.files().update, *args, **kwargs)

    def permissions(self, *args, **kwargs):
        return self._wrapmethod(lambda drive: drive.permissions().list, *args, **kwargs)


class Runner:
    def __init__(self, drive, owners_file):
        self.drive = drive
        # for enumerating drive owners, if specified
        self.owners = {}
        self.insufficient_permissions = set()
        self.output_buffer = {}

        if owners_file:
            with open(owners_file, "w", encoding="utf-8") as f:
                print(", ".join(("Email", "File link")), file=f)

        self.owners_file = owners_file

    def listdir(self, folder_id):
        """Yield all children of `id`"""
        page_token = None
        while True:
            response = self.drive.list(
                q=f'"{folder_id}" in parents and trashed = false',
                pageSize=1000,
                pageToken=page_token,
                # by providing fields we can specify shortcutDetails which avoids an extra call when
                # copying shortcuts and modifiedTime and size which we use when deciding whether to copy
                # NB: you can set files(*) to see _all_ available fields
                fields="nextPageToken, files(*)",
            )
            yield from response["files"]
            page_token = response.get("nextPageToken", None)
            if not page_token:
                break

    def get_one(self, q):
        """Return the attributes of a single file using drive#list"""
        try:
            return self.drive.list(q=q)["files"][0]
        except IndexError:
            return None

    def action_move(self, item, parent_id, dest):
        """Action: move the enqueued item to folder {parent_id}"""
        self.drive.update(fileId=item["id"], addParents=dest, removeParents=parent_id)
        self.output_buffer["moveFile"] = f'Moving {item["name"]}'

    def action_enumerate_owners(self, item):
        """Action: print owner of item"""

        try:
            permissions = self.drive.permissions(
                fileId=item["id"], fields="permissions(emailAddress,role)"
            )["permissions"]
        except HttpError as e:
            if "does not have sufficient permissions" in str(e):
                self.insufficient_permissions.add(item["id"])
                return

            raise e

        if not permissions:
            raise ValueError(f'No permissions found for {item["id"]}')

        owners = set([x["emailAddress"] for x in permissions if x["role"] == "owner"])
        if not owners:
            raise ValueError(f'No owners found for {item["id"]}')

        if item["owners"]:
            owners |= set([x["emailAddress"] for x in item["owners"]])

        for owner in owners:
            entry = self.owners.get(owner, set())
            entry.add(item["webViewLink"])
            self.owners[owner] = entry

        if self.owners_file:
            with open(self.owners_file, "a", encoding="utf-8") as f:
                for owner in owners:
                    print(", ".join((owner, item["webViewLink"])), file=f)

        self.output_buffer["owners"] = (
            f"{len(self.owners.keys())} owners for {sum([len(v) for v in self.owners.values()])} files"
        )

    def run(self, source_root, dest_root, move_files, enumerate_owners):
        """Run the specified action on dest_root, reproducing the directory structure of source_root"""
        q = queue.Queue()
        drive = self.drive

        for item in self.listdir(source_root):
            q.put(("/", item, source_root, dest_root))

        while not q.empty():
            (folder_name, item, parent_id, dest) = q.get()

            item_path = os.path.join(folder_name, item["name"])

            # if folder, create destination folder and add children to queue
            if item["mimeType"] == "application/vnd.google-apps.folder":
                maybe_f = self.get_one(
                    (
                        f'"{dest}" in parents and trashed = false '
                        f'and name = "{item["name"]}" and mimeType = "{item["mimeType"]}"'
                    ),
                )
                if maybe_f:
                    folder = maybe_f
                else:
                    folder = drive.create(
                        body={
                            **{key: item[key] for key in ["name", "mimeType"]},
                            "parents": [dest],
                        },
                    )

                self.output_buffer["folder"] = (
                    f'Folder: {item_path}{"" if maybe_f else " (created)"}'
                )

                for child_item in self.listdir(item["id"]):
                    q.put(
                        (
                            item_path,
                            child_item,
                            item["id"],
                            folder["id"],
                        )
                    )

                continue

            # else, run actions
            try:
                if move_files:
                    self.action_move(item, parent_id, dest)

                if enumerate_owners:
                    self.action_enumerate_owners(item)

                print(" | ".join(self.output_buffer.values()))
                print("\033[1A", end="\x1b[2K")
            except HttpError as e:
                print()
                print(f'Error on file {os.path.join(folder_name, item["name"])}.')
                print()
                raise e

        if self.owners:
            print(self.owners)
        if self.insufficient_permissions:
            print(
                f"Did not have sufficient permissions to operate on files: {self.insufficient_permissions}"
            )


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
    parser.add_argument(
        "--move-files",
        help="Attempt to move files to corresponding folder in <dest>",
        action="store_true",
    )
    parser.add_argument(
        "--list-owners",
        help="Enumerate owners of all files under <source>",
        action="store_true",
    )
    parser.add_argument(
        "-o",
        "--owners-file",
        help="CSV for storing list of owners (can run long)",
        type=argparse.FileType("w"),
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if not (args.move_files or args.list_owners):
        print("Warning: no actions specified. Directory structure will simply be copied")

    from_id = os.path.basename(args.source)
    to_id = os.path.basename(args.dest)
    user_credentials = os.path.join(os.getcwd(), "token.json")

    def generate_creds():
        return authenticate(user_credentials, args.app_credentials.name)

    drive = DriveFiles(generate_creds)
    runner = Runner(drive, args.owners_file.name if args.owners_file else None)
    runner.run(from_id, to_id, args.move_files, args.list_owners)


if __name__ == "__main__":
    main()
