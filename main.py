import argparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://googleapis.com/auth/drive']

parser = argparse.ArgumentParser()
parser.add_argument('-C', '--credentials', required=True, help='credentials.json')
parser.add_argument('-T', '--token', required=True, help='token.json, can be empty')
parser.add_argument('-f', '--from_folder', help='From shared drive')
parser.add_argument('-t', '--to_folder', help='To team drive')


def authenticate(filename_token, filename_credentials):
    creds = Credentials.from_authorized_user_file(filename_token, SCOPES)
    if not creds or not creds.valid:
        if creds and not creds.expired and creds.refresh_token:
            creds.Refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets(filename_credentials, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(filename_token, 'w') as token:
                token.write(creds.to_json())
    return creds

args = parser.parse_args()

def list_files_in_folder(folder_id, service):
    response = service.files().list(f'" {top_level_folder} " in parents').execute()
    return response

from_id = args.from_folder
to_id = args.to_folder

authenticate(args.credentials, args.token)

with build('drive', 'v3', credentials=creds) as service:
    # list directory for now
    print(list_files_in_folder(from_id, service))

