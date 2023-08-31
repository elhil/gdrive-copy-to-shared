# Google shared drive to Google TeamDrive via reparenting

Requires:
- Permissions to a shared drive you want to copy from
- A google workspace account (possibly administrative?)

## Initial setup

(described at length in https://martinheinz.dev/blog/84)

You will need to create a Google Cloud Project, via cloud resource manager (https://console.cloud.google.com/cloud-resource-manager/#rationale) or cli `gcloud projects create PROJECT_NAME`

The only API you need to enable here is the drive API, I started with read-only to test but you will want full read-write access.

(expand on oauth, etc. later)

save oauth credentials as a json file to be callable via the script.

## To run

```
touch token.json # this will be populated as needed by the authentication
python3 main.py -C [CREDENTIALS_FILE.json] -T token.json -f [SHARED_DRIVE] -t [TEAM_DRIVE]
```

## Extremely helpful links

- https://martinheinz.dev/blog/84
  - the author presents super helpful information about how to make use of the google api via python
- https://stackoverflow.com/questions/30716568/how-can-i-make-a-copy-of-a-file-in-google-drive-via-python/70890884#70890884
- https://developers.google.com/drive/api/reference/rest/v3/files
  - the python api is largely based on the underlying rest api
