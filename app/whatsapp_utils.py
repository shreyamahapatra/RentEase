import requests
import json
import os

def send_whatsapp_message(to_number, template_name="hello_world", language_code="en_US"):
    url = "https://graph.facebook.com/v22.0/633957106475257/messages"

    payload = json.dumps({
      "messaging_product": "whatsapp",
      "to": to_number,
      "type": "template",
      "template": {
        "name": template_name,
        "language": {
          "code": "en_US"
        }
      }
    })
    headers = {
      'Authorization': 'Bearer ',
      'Content-Type': 'application/json'
    }
    response = requests.post(url, headers=headers, data=payload)
    return response.json() 