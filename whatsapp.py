
import requests
import json

url = "https://graph.facebook.com/v22.0/633957106475257/messages"

payload = json.dumps({
  "messaging_product": "whatsapp",
  "to": "917898272111",
  "type": "template",
  "template": {
    "name": "hello_world",
    "language": {
      "code": "en_US"
    }
  }
})
headers = {
  'Authorization': 'Bearer ',
  'Content-Type': 'application/json'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)
