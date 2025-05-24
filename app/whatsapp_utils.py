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
      'Authorization': 'Bearer EAAJwUCHV0J0BO4wZAADSMhIuZBxnt8Dp2iKUpQidEZBUufbTZC77noQAaEKQBobTEy7jfD69cCqE1v8FTNfqW8XGafphQfX5dhJ02h3kE8AZChVurMQ97VqjG7ZCwKCyPQEZA2D4H0OgvXIBwwUs1ZArMyhgIbcOUZAzxZBc8mHc3v2ULyrdLWE6owO403J86i7YlgUE0VNhpZCn7t7DNnTZCfpgKb5gQOvbkni3BQQZD',
      'Content-Type': 'application/json'
    }
    response = requests.post(url, headers=headers, data=payload)
    return response.json() 