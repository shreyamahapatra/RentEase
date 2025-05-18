# RentEase - Property Management System

A Flask-based web application for managing rental properties, tenants, and bill payments.

## Features

- User authentication and authorization
- Property management with room configurations
- Tenant management
- Bill tracking and payment management
- Export functionality for bills
- Room occupancy tracking

## Setup

1. Clone the repository:
```bash
git clone <your-repo-url>
cd Invent
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Initialize the database:
```bash
flask run
```
The database will be automatically initialized when you first run the application.

5. Run the application:
```bash
flask run
```

## Usage

1. Register a new account or login with existing credentials
2. Add properties with room configurations
3. Add tenants to rooms
4. Track and manage bill payments
5. Export bills for record keeping

## Technologies Used

- Flask
- SQLite
- HTML/CSS
- JavaScript
- Bootstrap 