import os
import argparse
import base64
from email.message import EmailMessage
from pathlib import Path
import csv
from textwrap import dedent

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


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


def gmail_create_draft(
    send_messages, from_email, to_email, owner_target_email, drive_folder, resource_key, creds
):
    """
    Create and insert a draft email.
     Print the returned draft's message and id.
     Returns: Draft object, including draft id and message meta data.

    Load pre-authorized user credentials from the environment.
    TODO(developer) - See https://developers.google.com/identity
    for guides on implementing OAuth2 for the application.
    """

    try:
        # create gmail api client
        service = build("gmail", "v1", credentials=creds)

        message = EmailMessage()

        html_content = f"""
            <html>
            <body>
            <p>Hello,</p>

            <p>You are receiving this email because you are the owner of one or more files in the NeuroPoly
            shared GDrive folder. Since this folder is hosted on someone's personal Google Drive, <strong>we're
            migrating all our files to a Shared Drive</strong> managed by the lab. Unfortunately, Google doesn't provide
            a way for us to take ownership of all these files automatically, so we need your help in
            transferring them. <strong>This should take less than 2 minutes of your time.</strong></p>

            <ol>
            <li><strong>View the files owned by you</strong> listed here:
            https://drive.google.com/drive/u/0/folders/{drive_folder}?resourcekey={resource_key}&q=owner:{to_email}%20parent:{drive_folder}

            <li><strong>Select all files</strong> (<pre style="display: inline">ctrl</pre> + A / <pre style="display: inline">⌘</pre> + A on Mac)

            <li><strong>Open the sharing menu</strong> by pressing <pre style="display: inline">ctrl</pre> + <pre style="display: inline">alt</pre> + A (<pre style="display: inline">⌘</pre> + <pre style="display: inline">alt</pre> + A on Mac) OR right-click and select Share → Share

            <li><strong>Add {owner_target_email} as an Editor</strong> and press "Send"

            <li><strong>Repeat steps 2-3</strong> to bring up the Sharing dialog again

            <li><strong>Find {owner_target_email} in the list</strong> click the dropdown next to "Editor", then click Transfer Ownership.

            <li><strong>Confirm ownership transfer</strong>. That's it; you're done!
            </ol>

            <p>Thanks so much for taking the time to do this. It saves us a lot of trouble and means our files can
            keep their editing history and comments. If you have any issues in the transfer process, <strong>don't
            hesitate to reach out</strong>! If you're on our Slack, ping IT staff there; otherwise you can
            email neuropoly-admin@liste.polymtl.ca</p>
            <p>Best,</p>
            <p>The NeuroPoly IT team</p>
        </body>
        </html>
        """

        plain_content = f"""
            Hello,

            You are receiving this email because you are the owner of one or more files in the NeuroPoly
            shared GDrive folder. Since this folder is hosted on someone's personal Google Drive, **we're
            migrating all our files to a Shared Drive** managed by the lab. Unfortunately, Google doesn't provide
            a way for us to take ownership of all these files automatically, so we need your help in
            transferring them. **This should take less than 2 minutes of your time.**


            1. View the files owned by you listed here:
            https://drive.google.com/drive/u/0/folders/{drive_folder}?resourcekey={resource_key}&q=owner:{to_email}%20parent:{drive_folder}

            2. Select all files (ctrl + A / ⌘ + A on Mac)

            3. Open the sharing menu by pressing ctrl + alt + A (⌘ + alt + A on Mac) OR right-click and select Share → Share

            4. Add {owner_target_email} as an Editor and press "Send"

            5. Repeat steps 2-3 to bring up the Sharing dialog again

            6. Find {owner_target_email} in the list click the dropdown next to "Editor", then click Transfer Ownership.

            7. Confirm ownership transfer. That's it; you're done!

            Thanks so much for taking the time to do this. It saves us a lot of trouble and means our files can
            keep their editing history and comments. If you have any issues in the transfer process, don't
            hesitate to reach out! If you're on our Slack, ping IT staff there; otherwise you can email
            neuropoly-admin@liste.polymtl.ca .

            Best,
            The NeuroPoly IT team
        """

        message.set_content(plain_content)
        message.add_alternative(html_content, subtype="html")
        message["To"] = to_email
        message["From"] = from_email
        message["Subject"] = "[Action Required] NeuroPoly Drive Migration Notice"

        # encoded message
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        # pylint: disable=E1101
        if send_messages:
            create_message = {"raw": encoded_message}
            send_message = (
                service.users().messages().send(userId="me", body=create_message).execute()
            )
            print(f'Message Id: {send_message["id"]}')
            return

        create_message = {"message": {"raw": encoded_message}}
        draft = service.users().drafts().create(userId="me", body=create_message).execute()

        print(f'Draft id: {draft["id"]}\nDraft message: {draft["message"]}')

    except HttpError as error:
        print(f"An error occurred: {error}")
        draft = None

    return draft


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
        "-o",
        "--owners-file",
        help="CSV for storing list of owners (can run long)",
        type=argparse.FileType("r"),
        default="owners.csv",
    )

    parser.add_argument("-f", "--from-email", help="Email to send from", required=True)

    parser.add_argument(
        "--owner-target-email", help="The email to migrate file ownership to", required=True
    )

    parser.add_argument("--drive-folder", help="The ID of the Drive folder", required=True)

    parser.add_argument(
        "--resource-key",
        help="Resource key (obtainable as a query param in the share link for the folder)",
        required=True,
    )

    parser.add_argument(
        "--send-messages",
        help="Set to 'send' to send emails rather than creating a draft. All other values will fail",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.send_messages and args.send_messages.lower() != "send":
        print("If --send-messages is specified, it must equal 'send'")
        return

    send_messages = args.send_messages and args.send_messages.lower() == "send"

    user_credentials = os.path.join(os.getcwd(), "token.json")
    creds = authenticate(user_credentials, args.app_credentials.name)

    counts = {}
    with open(args.owners_file.name, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row["Email"]
            if email not in counts:
                counts[email] = 0

            counts[email] += 1

    for email in counts:
        gmail_create_draft(
            send_messages,
            args.from_email,
            email,
            args.owner_target_email,
            args.drive_folder,
            args.resource_key,
            creds,
        )


if __name__ == "__main__":
    main()
