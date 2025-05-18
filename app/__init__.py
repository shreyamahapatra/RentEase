from flask import Flask
from flask_mail import Mail
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rent-managment-system'  # Change this to a secure secret key

# Email configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'rentease.in@gmail.com'
app.config['MAIL_PASSWORD'] = 'mcsh fwdx gruv xvdv'  # Replace with the App Password you generated
app.config['MAIL_DEFAULT_SENDER'] = 'rentease.in@gmail.com'

mail = Mail(app)

from app import routes  # Import routes after app is created to avoid circular imports
