import requests
import urllib3
from dotenv import load_dotenv

# Disable SSL verification globally (corporate proxy intercepts HTTPS)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_original_send = requests.Session.send
def _send_no_verify(self, *args, **kwargs):
    kwargs['verify'] = False
    return _original_send(self, *args, **kwargs)
requests.Session.send = _send_no_verify

from databricks.sdk import WorkspaceClient

load_dotenv()

# Fetch the API secret key from Databricks
db = WorkspaceClient()
secret_key = db.secrets.get_secret(scope="api", key="surus_server_nb_secret").value

# Exchange secret key for a NationBuilder access token
nation_slug = "barringtongop"
token_response = requests.get(
    f"https://server.surusenterprises.com/auth/api_token/{nation_slug}",
    headers={"x-api-key": secret_key}
)
token_response.raise_for_status()
access_token = token_response.json()["access_token"]

# Call the NationBuilder API
url = f"https://{nation_slug}.nationbuilder.com/api/v2/contacts?page%5Bsize%5D=100"
response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})

print(response.text)
