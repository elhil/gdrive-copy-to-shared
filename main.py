import argparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pathlib import Path
import queue

SCOPES = ['https://www.googleapis.com/auth/drive']

parser = argparse.ArgumentParser()
parser.add_argument('-C', '--credentials', required=True, help='credentials.json')
parser.add_argument('-T', '--token', required=True, help='token.json, can be empty')
parser.add_argument('-f', '--from_folder', help='From shared drive')
parser.add_argument('-t', '--to_folder', help='To team drive')


def authenticate(filename_token, filename_credentials):
    creds = None
    if Path(filename_token).exists():
        creds = Credentials.from_authorized_user_file(filename_token, SCOPES)
    if not creds or not creds.valid:
        if creds and not creds.expired and creds.refresh_token:
            creds.Refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(filename_credentials, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
            with open(filename_token, 'w') as token:
                token.write(creds.to_json())
    return creds

args = parser.parse_args()

def list_contents_of_folder(folder_id, service):
    return service.files().list(q=f'"{folder_id}" in parents').execute()


def recurse_folders(folder_id_top, service):
    q = queue.Queue()

    q.put((folder_id_top, ''))
    while True:
        if q.empty():
            print('done')
            return
        else:
            (folder_id, name) = q.get()
            response = list_contents_of_folder(folder_id, service)
            if getattr(response, 'nextPageToken', None):
                next_page_token = response['nextPageToken']
            for r in response['files']:
                if r['mimeType'] == 'application/vnd.google-apps.folder':
                    q.put((r['id'], r['name']))
                    print(r['name'])
        
    
    

from_id = args.from_folder
to_id = args.to_folder

creds = authenticate(args.token, args.credentials)

with build('drive', 'v3', credentials=creds) as service:
    # list directory for now
    #r = list_contents_of_folder(from_id, service)
    recurse_folders(from_id, service)



