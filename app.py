import requests
import urllib3
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from databricks.sdk import WorkspaceClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_original_send = requests.Session.send
def _send_no_verify(self, *args, **kwargs):
    kwargs['verify'] = False
    return _original_send(self, *args, **kwargs)
requests.Session.send = _send_no_verify

load_dotenv()

app = Flask(__name__)
db = WorkspaceClient()

CONTACT_METHODS = [
    "delivery", "door_knock", "email", "email_blast", "face_to_face",
    "facebook", "meeting", "phone_call", "robocall", "snail_mail",
    "text", "text_1to1", "text_blast", "tweet", "video_call",
    "webinar", "linkedin", "other",
]
CONTACT_STATUSES = [
    "answered", "bad_info", "left_message", "meaningful_interaction",
    "send_information", "not_interested", "no_answer", "refused",
    "inaccessible", "other",
]


def get_nb_token(nation_slug: str) -> str:
    secret_key = db.secrets.get_secret(scope="api", key="surus_server_nb_secret").value
    resp = requests.get(
        f"https://server.surusenterprises.com/auth/api_token/{nation_slug}",
        headers={"x-api-key": secret_key},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@app.route("/")
def index():
    return render_template("index.html", contact_methods=CONTACT_METHODS, contact_statuses=CONTACT_STATUSES)


@app.route("/import", methods=["POST"])
def import_contact():
    form = request.form
    nation_slug = form.get("nation_slug", "").strip()

    if not nation_slug:
        return jsonify({"success": False, "error": "Nation slug is required"}), 400

    attributes = {}

    for field in ["author_id", "signup_id", "broadcaster_id", "content", "path_id", "path_step_id"]:
        val = form.get(field, "").strip()
        if val:
            attributes[field] = val

    for field in ["contact_status", "contact_method"]:
        val = form.get(field, "").strip()
        if val:
            attributes[field] = val

    pc = form.get("pc_in_cents", "").strip()
    if pc:
        try:
            attributes["pc_in_cents"] = int(pc)
        except ValueError:
            return jsonify({"success": False, "error": "Political capital must be a whole number"}), 400

    try:
        token = get_nb_token(nation_slug)
        url = f"https://{nation_slug}.nationbuilder.com/api/v2/contacts"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"data": {"type": "contacts", "attributes": attributes}},
        )
        resp.raise_for_status()
        return jsonify({"success": True, "data": resp.json()})
    except requests.HTTPError as e:
        detail = None
        if e.response is not None:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text
        return jsonify({"success": False, "error": str(e), "detail": detail}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
