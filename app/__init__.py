from flask import Flask

app = Flask(__name__)
app.secret_key = 'rent-managment-system'

from app import routes  # Import routes after app is created to avoid circular imports
