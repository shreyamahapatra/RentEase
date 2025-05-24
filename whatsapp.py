
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
  'Authorization': 'Bearer EAAJwUCHV0J0BO4wZAADSMhIuZBxnt8Dp2iKUpQidEZBUufbTZC77noQAaEKQBobTEy7jfD69cCqE1v8FTNfqW8XGafphQfX5dhJ02h3kE8AZChVurMQ97VqjG7ZCwKCyPQEZA2D4H0OgvXIBwwUs1ZArMyhgIbcOUZAzxZBc8mHc3v2ULyrdLWE6owO403J86i7YlgUE0VNhpZCn7t7DNnTZCfpgKb5gQOvbkni3BQQZD',
  'Content-Type': 'application/json'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)
