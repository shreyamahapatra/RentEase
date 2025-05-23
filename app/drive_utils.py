from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
import pickle
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_credentials():
    """Gets valid user credentials from storage.

    If nothing has been stored, or if the stored credentials are invalid,
    the OAuth2 flow is completed to obtain the new credentials.

    Returns:
        Credentials, the obtained credential.
    """
    creds = None
    # The file token.pickle stores the user's access and refresh tokens
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return creds

def find_or_create_folder(folder_name, parent_folder_id=None):
    """Finds a folder by name or creates it if it doesn't exist.

    Args:
        folder_name: The name of the folder to find or create.
        parent_folder_id: The ID of the parent folder (default to root).

    Returns:
        The ID of the found or created folder.
    """
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)

    # Search for the folder
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"
    else:
         # Search in root
         query += " and 'root' in parents"

    try:
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        items = results.get('files', [])

        if items:
            # Folder found
            print(f"Folder '{folder_name}' found with ID: {items[0]['id']}")
            return items[0]['id']
        else:
            # Folder not found, create it
            print(f"Folder '{folder_name}' not found, creating...")
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_folder_id:
                 file_metadata['parents'] = [parent_folder_id]
            else:
                 # Create in root
                 # Google Drive API automatically creates in root if parents is not specified
                 pass

            folder = service.files().create(
                body=file_metadata,
                fields='id'
            ).execute()
            print(f"Folder '{folder_name}' created with ID: {folder.get('id')}")
            return folder.get('id')

    except HttpError as error:
        print(f'An error occurred: {error}')
        return None

def upload_file(file_path, file_name, mime_type, folder_id=None):
    """Uploads a file to Google Drive.

    Args:
        file_path: Path to the file to upload
        file_name: Name to give the file in Google Drive
        mime_type: MIME type of the file
        folder_id: The ID of the folder to upload the file to (optional).

    Returns:
        The ID of the uploaded file
    """
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)

    file_metadata = {'name': file_name}
    if folder_id:
        file_metadata['parents'] = [folder_id]

    media = MediaFileUpload(file_path, mimetype=mime_type)
    
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    
    return file.get('id')

def get_file_url(file_id):
    """Gets the web view link for a file.

    Args:
        file_id: The ID of the file in Google Drive

    Returns:
        The web view URL for the file
    """
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    
    file = service.files().get(
        fileId=file_id,
        fields='webViewLink'
    ).execute()
    
    return file.get('webViewLink') 