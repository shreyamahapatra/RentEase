from flask import render_template, request, redirect, url_for, flash, jsonify, session, send_file
from app import app, mail  # Import mail from app
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import io
from app.drive_utils import upload_file, get_file_url, find_or_create_folder
from app.whatsapp_utils import send_whatsapp_message

# Create uploads directory if it doesn't exist
UPLOADS_DIR = os.path.join('app', 'static', 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

def get_db():
    conn = sqlite3.connect('rentease.db')
    conn.row_factory = sqlite3.Row
    return conn

# Database initialization
def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Create tables with new schema
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  email TEXT NOT NULL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS properties
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  address TEXT NOT NULL,
                  user_id INTEGER NOT NULL,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS room_configurations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  property_id INTEGER NOT NULL,
                  room_type TEXT NOT NULL,
                  room_count INTEGER NOT NULL,
                  rent REAL NOT NULL,
                  electricity_charge REAL NOT NULL,
                  water_charge REAL NOT NULL,
                  security_deposit REAL NOT NULL,
                  FOREIGN KEY (property_id) REFERENCES properties (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS rooms
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  property_id INTEGER NOT NULL,
                  room_config_id INTEGER NOT NULL,
                  room_number INTEGER NOT NULL,
                  media_url TEXT,
                  is_available BOOLEAN DEFAULT TRUE,
                  FOREIGN KEY (property_id) REFERENCES properties (id),
                  FOREIGN KEY (room_config_id) REFERENCES room_configurations (id))''')
    
    # Check if is_available column exists in rooms table
    c.execute("PRAGMA table_info(rooms)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add is_available column if it doesn't exist
    if 'is_available' not in columns:
        c.execute('ALTER TABLE rooms ADD COLUMN is_available BOOLEAN DEFAULT TRUE')
    
    c.execute('''CREATE TABLE IF NOT EXISTS tenants
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  property_id INTEGER NOT NULL,
                  room_id INTEGER NOT NULL,
                  phone_number TEXT NOT NULL,
                  email TEXT,
                  move_in_date DATE NOT NULL,
                  move_out_date DATE,
                  police_verification TEXT,
                  FOREIGN KEY (property_id) REFERENCES properties (id),
                  FOREIGN KEY (room_id) REFERENCES rooms (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS bill_payments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tenant_id INTEGER NOT NULL,
                  amount REAL NOT NULL,
                  payment_date DATE NOT NULL,
                  payment_mode TEXT NOT NULL,
                  notes TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  total_amount REAL NOT NULL,
                  paid_amount REAL NOT NULL,
                  pending_amount REAL NOT NULL,
                  FOREIGN KEY (tenant_id) REFERENCES tenants (id))''')
    
    # Check if phone_number column exists in tenants table
    c.execute("PRAGMA table_info(tenants)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add phone_number column if it doesn't exist
    if 'phone_number' not in columns:
        c.execute('ALTER TABLE tenants ADD COLUMN phone_number TEXT')
    
    # Check if media_url column exists in rooms table
    c.execute("PRAGMA table_info(rooms)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add media_url column if it doesn't exist
    if 'media_url' not in columns:
        c.execute('ALTER TABLE rooms ADD COLUMN media_url TEXT')
    
    # Check if move_out_date column exists in tenants table
    c.execute("PRAGMA table_info(tenants)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add move_out_date column if it doesn't exist
    if 'move_out_date' not in columns:
        c.execute('ALTER TABLE tenants ADD COLUMN move_out_date DATE')
    
    conn.commit()
    conn.close()

# Initialize database when app starts
init_db()

@app.route('/')
@app.route('/index')
def index():
    if 'user_id' in session:
        conn = get_db()
        c = conn.cursor()
        properties = c.execute('SELECT * FROM properties WHERE user_id = ?', (session['user_id'],)).fetchall()
        
        # Get current month and year
        current_date = datetime.now()
        current_month = current_date.month
        current_year = current_date.year
        
        # Calculate previous month and year
        if current_month == 1:
            prev_month = 12
            prev_year = current_year - 1
        else:
            prev_month = current_month - 1
            prev_year = current_year
        
        # Get tenants data with payment information - only active tenants
        tenants = c.execute('''
            SELECT t.id, t.name, t.property_id, t.room_id, t.phone_number, t.email, t.move_in_date, t.move_out_date, t.police_verification,
                   p.name as property_name, r.room_number, rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit,
                   COALESCE(SUM(CASE 
                        WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                        THEN bp.amount 
                        ELSE 0 
                   END), 0) as current_month_paid,
                   COALESCE(SUM(CASE 
                        WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                        THEN bp.amount 
                        ELSE 0 
                   END), 0) as prev_month_paid
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            JOIN rooms r ON t.room_id = r.id
            JOIN room_configurations rc ON r.room_config_id = rc.id
            LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
                AND (strftime('%m', bp.payment_date) IN (?, ?) 
                    AND strftime('%Y', bp.payment_date) IN (?, ?))
            WHERE p.user_id = ? AND t.move_out_date IS NULL
            GROUP BY t.id
        ''', (str(current_month).zfill(2), str(current_year),
              str(prev_month).zfill(2), str(prev_year),
              str(current_month).zfill(2), str(prev_month).zfill(2),
              str(current_year), str(prev_year),
              session['user_id'])).fetchall()
        
        # Calculate total collection and pending amounts
        total_collection = 0
        total_pending = 0
        total_expected = 0
        
        # Get monthly data for the last 6 months
        monthly_labels = []
        monthly_collections = []
        monthly_expected = []
        
        for i in range(6):
            month_date = current_date - timedelta(days=30*i)
            year = month_date.year
            month = month_date.month
            month_str = month_date.strftime('%b %Y')
            monthly_labels.insert(0, month_str)
            
            # Get monthly totals
            c.execute('''
                SELECT 
                    COALESCE(SUM(bp.amount), 0) as paid_amount,
                    SUM(rc.rent + rc.electricity_charge + rc.water_charge) as expected_total
                FROM tenants t
                JOIN rooms r ON t.room_id = r.id
                JOIN properties p ON r.property_id = p.id
                JOIN room_configurations rc ON r.room_config_id = rc.id
                LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
                    AND strftime('%Y', bp.payment_date) = ? 
                    AND strftime('%m', bp.payment_date) = ?
                WHERE p.user_id = ?
            ''', (str(year), f"{month:02d}", session['user_id']))
            
            result = c.fetchone()
            monthly_collections.insert(0, result['paid_amount'] if result else 0)
            monthly_expected.insert(0, result['expected_total'] if result else 0)
        
        # Calculate property-wise collections
        property_collections = []
        for property in properties:
            c.execute('''
                SELECT 
                    COALESCE(SUM(bp.amount), 0) as collected,
                    SUM(rc.rent + rc.electricity_charge + rc.water_charge) as expected
                FROM tenants t
                JOIN rooms r ON t.room_id = r.id
                JOIN properties p ON r.property_id = p.id
                JOIN room_configurations rc ON r.room_config_id = rc.id
                LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
                    AND strftime('%Y-%m', bp.payment_date) = strftime('%Y-%m', 'now')
                WHERE t.property_id = ?
                GROUP BY t.property_id
            ''', (property[0],))
            
            result = c.fetchone()
            if result:
                collected = result['collected']
                expected = result['expected']
                pending = expected - collected
                collection_rate = (collected / expected * 100) if expected > 0 else 0
                
                property_collections.append({
                    'name': property[1],
                    'expected': expected,
                    'collected': collected,
                    'pending': pending,
                    'collection_rate': round(collection_rate, 1)
                })
                
                total_collection += collected
                total_pending += pending
                total_expected += expected
        
        # Calculate overall collection rate
        collection_rate = (total_collection / total_expected * 100) if total_expected > 0 else 0
        
        conn.close()
        return render_template('index.html', 
                             properties=properties, 
                             tenants=tenants,
                             total_pending=total_pending,
                             total_collection=total_collection,
                             collection_rate=round(collection_rate, 1),
                             monthly_labels=monthly_labels,
                             monthly_collections=monthly_collections,
                             monthly_expected=monthly_expected,
                             property_collections=property_collections)
    return render_template('index.html', 
                         tenants=[], 
                         total_pending=0,
                         total_collection=0,
                         collection_rate=0,
                         monthly_labels=[],
                         monthly_collections=[],
                         monthly_expected=[],
                         property_collections=[])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        c = conn.cursor()
        user = c.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        print(user)
        if user and user[2] == password:
            session['user_id'] = user[0]
            session['username'] = user[1]
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        
        if not username or not password or not email:
            flash('Please fill in all fields', 'danger')
            return redirect(url_for('register'))
        
        try:
            conn = get_db()
            c = conn.cursor()
            
            # Check if username already exists
            existing_user = c.execute('SELECT * FROM users WHERE username = ?', 
                                    (username,)).fetchone()
            if existing_user:
                flash('Username already exists', 'danger')
                return redirect(url_for('register'))
            
            # Insert new user
            c.execute('INSERT INTO users (username, password, email) VALUES (?, ?, ?)',
                     (username, password, email))
            conn.commit()
            
            # Send welcome email
            try:
                msg = Message(
                    'Welcome to RentEase!',
                    recipients=[email]
                )
                msg.body = f'''Welcome to RentEase!

Thank you for registering with us. Your account has been successfully created.

Your login details:
Username: {username}

You can now log in to your account and start managing your properties.

Best regards,
The RentEase Team'''
                
                mail.send(msg)
                flash('Registration successful! A welcome email has been sent to your email address.', 'success')
            except Exception as e:
                print(f"Failed to send email: {str(e)}")
                flash('Registration successful! However, we could not send the welcome email.', 'warning')
            
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/add-property', methods=['GET', 'POST'])
def add_property():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name')
        address = request.form.get('address')
        user_id = session['user_id']

        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO properties (name, address, user_id) VALUES (?, ?, ?)',
                 (name, address, user_id))
        property_id = c.lastrowid

        # Save default room configurations (1 Room Set and 2 Room Set)
        default_room_types = ['one_room', 'two_room']
        for room_type in default_room_types:
            room_count = request.form.get(f'{room_type}_count')
            rent = request.form.get(f'{room_type}_rent')
            electricity_charge = request.form.get(f'{room_type}_electricity')
            water_charge = request.form.get(f'{room_type}_water')
            security_deposit = request.form.get(f'{room_type}_security')

            # Only insert if count, rent, etc. are provided (meaning the section was used)
            if room_count or rent or electricity_charge or water_charge or security_deposit:
                c.execute('''INSERT INTO room_configurations
                             (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit)
                             VALUES (?, ?, ?, ?, ?, ?, ?)''',
                         (property_id, room_type, room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0, security_deposit or 0))


        # Save custom room configurations
        # Flask's request.form treats list of dicts with indexed names like a multidict.
        # We need to manually group them.
        custom_configs_data = {}
        for key, value in request.form.items():
            if key.startswith('custom_configs['):
                # Extract index and field name: e.g., custom_configs[0][room_type]
                parts = key.split('[')
                index = int(parts[1].split(']')[0])
                field = parts[2].split(']')[0]

                if index not in custom_configs_data:
                    custom_configs_data[index] = {}
                custom_configs_data[index][field] = value

        # Sort by index to process in order
        for index in sorted(custom_configs_data.keys()):
            config = custom_configs_data[index]
            room_type = config.get('room_type')
            room_count = config.get('room_count')
            rent = config.get('rent')
            electricity_charge = config.get('electricity_charge')
            water_charge = config.get('water_charge')
            security_deposit = config.get('security_deposit')

            # Ensure minimum data is present for a custom config
            if room_type and (room_count or rent or electricity_charge or water_charge or security_deposit):
                 c.execute('''INSERT INTO room_configurations
                             (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit)
                             VALUES (?, ?, ?, ?, ?, ?, ?)''',
                         (property_id, room_type, room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0, security_deposit or 0))

        conn.commit()
        conn.close()

        flash('Property and room configurations added successfully!', 'success')
        return redirect(url_for('my_properties'))
    return render_template('add_property.html')

@app.route('/add-tenant', methods=['GET', 'POST'])
def add_tenant():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Get form data
        property_id = request.form.get('property')
        room_id = request.form.get('room')
        tenant_name = request.form.get('tenant_name')
        phone_number = request.form.get('phone_number')
        email = request.form.get('email')
        move_in_date = request.form.get('move_in_date')
        move_out_date = request.form.get('move_out_date')
        police_verification = request.files.get('police_verification')
        
        conn = get_db()
        c = conn.cursor()
        
        # Check if room is available
        room = c.execute('SELECT is_available FROM rooms WHERE id = ?', (room_id,)).fetchone()
        if not room or not room['is_available']:
            conn.close()
            flash('This room is not available!', 'danger')
            return redirect(url_for('add_tenant'))
        
        # Handle police verification file
        drive_file_data = None
        if police_verification:
            # Get property name and room number
            property_data = c.execute('SELECT name FROM properties WHERE id = ?', (property_id,)).fetchone()
            property_name = property_data['name'] if property_data else 'Unknown Property'
            
            room_data = c.execute('SELECT room_number FROM rooms WHERE id = ?', (room_id,)).fetchone()
            room_number = room_data['room_number'] if room_data else 'Unknown Room'
            
            # Find or create property folder
            folder_id = find_or_create_folder(property_name)

            if folder_id:
                # Save file temporarily
                temp_filename = f"temp_police_verification_{property_id}_{room_id}_{int(time.time())}.pdf"
                temp_path = os.path.join('app/static/uploads', temp_filename)
                police_verification.save(temp_path)

                try:
                    # Upload to Google Drive into the property folder
                    drive_file_id = upload_file(
                        temp_path,
                        f"police_verification_{tenant_name}_Room{room_number}.pdf",
                        'application/pdf',
                        folder_id=folder_id
                    )

                    # Get the web view URL
                    drive_file_url = get_file_url(drive_file_id)

                    # Store both the file ID and URL in the database
                    drive_file_data = f"{drive_file_id}|{drive_file_url}"
                except Exception as e:
                    print(f"Error uploading to Google Drive: {str(e)}")
                    flash('Error uploading police verification document', 'danger')
                    drive_file_data = None
                finally:
                    # Clean up temporary file
                    try:
                        os.remove(temp_path)
                    except:
                        pass
        
        # Start transaction
        try:
            c.execute('BEGIN TRANSACTION')
            
            # Insert tenant
            c.execute('''INSERT INTO tenants 
                        (name, property_id, room_id, phone_number, email, move_in_date, move_out_date, police_verification) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (tenant_name, property_id, room_id, phone_number, email, move_in_date, move_out_date, drive_file_data))
            
            # Mark room as unavailable
            c.execute('UPDATE rooms SET is_available = FALSE WHERE id = ?', (room_id,))
            
            c.execute('COMMIT')
            flash('Tenant added successfully!', 'success')
        except Exception as e:
            c.execute('ROLLBACK')
            flash(f'Error adding tenant: {str(e)}', 'danger')
        finally:
            conn.close()
        
        return redirect(url_for('list_tenants'))

    # Get all properties for the logged-in user with their available rooms
    conn = get_db()
    c = conn.cursor()
    
    properties = c.execute('''
        SELECT p.id, p.name, p.address, 
               r.id as room_id, r.room_number,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit
        FROM properties p
        LEFT JOIN rooms r ON p.id = r.property_id
        LEFT JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE p.user_id = ? AND (r.is_available = TRUE OR r.is_available IS NULL)
        ORDER BY p.id, r.room_number
    ''', (session['user_id'],)).fetchall()
    
    # Format properties and rooms
    formatted_properties = []
    current_property = None
    
    for prop in properties:
        if current_property is None or current_property['id'] != prop[0]:
            if current_property is not None:
                formatted_properties.append(current_property)
            
            current_property = {
                'id': prop[0],
                'name': prop[1],
                'address': prop[2],
                'rooms': []
            }
        
        if prop[3]:  # if room_id exists
            current_property['rooms'].append({
                'id': prop[3],
                'number': prop[4],
                'type': prop[5],
                'rent': prop[6],
                'electricity_charge': prop[7],
                'water_charge': prop[8],
                'security_deposit': prop[9]
            })
    
    if current_property is not None:
        formatted_properties.append(current_property)
    
    print("Formatted properties:", formatted_properties)
    conn.close()
    return render_template('add_tenant.html', properties=formatted_properties)

@app.route('/list-tenants')
def list_tenants():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    # Get filter parameter
    status_filter = request.args.get('status', 'all')  # 'all', 'active', or 'past'
    
    conn = get_db()
    c = conn.cursor()
    
    # Get all properties for the user
    properties = c.execute('SELECT * FROM properties WHERE user_id = ?', 
                         (session['user_id'],)).fetchall()
    
    # Format properties data
    formatted_properties = []
    for prop in properties:
        formatted_properties.append({
            'id': prop[0],
            'name': prop[1],
            'address': prop[2]
        })
    
    # Base query for tenants
    query = '''
        SELECT t.id, t.name, t.property_id, t.room_id, t.phone_number, t.email, 
               t.move_in_date, t.move_out_date, t.police_verification,
               p.name as property_name,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
               r.room_number
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE p.user_id = ?
    '''
    
    # Add status filter
    if status_filter == 'active':
        query += ' AND t.move_out_date IS NULL'
    elif status_filter == 'past':
        query += ' AND t.move_out_date IS NOT NULL'
    
    query += ' ORDER BY t.move_in_date DESC'
    
    # Get all tenants with their property and room information
    tenants = c.execute(query, (session['user_id'],)).fetchall()
    
    # Format tenants data
    formatted_tenants = []
    for tenant in tenants:
        # Parse police verification data to get the URL
        police_verification_url = None
        if tenant[8]:  # police_verification field
            try:
                _, police_verification_url = tenant[8].split('|')
            except:
                pass

        formatted_tenants.append({
            'id': tenant[0],
            'name': tenant[1],
            'property_id': tenant[2],
            'room_id': tenant[3],
            'phone_number': tenant[4],
            'email': tenant[5],
            'move_in_date': tenant[6],
            'move_out_date': tenant[7],
            'police_verification': police_verification_url,
            'property_name': tenant[9],
            'room_type': tenant[10],
            'rent': tenant[11],
            'electricity_charge': tenant[12],
            'water_charge': tenant[13],
            'room_number': tenant[14]
        })
    
    conn.close()
    return render_template('list_tenants.html', 
                         properties=formatted_properties,
                         tenants=formatted_tenants,
                         status_filter=status_filter)

@app.route('/delete-tenant/<int:tenant_id>', methods=['POST'])
def delete_tenant(tenant_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get tenant's police verification file data
    tenant = c.execute('''
        SELECT t.police_verification 
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if tenant:
        # Delete the file from Google Drive if it exists
        if tenant[0]:
            try:
                drive_file_id, _ = tenant[0].split('|')
                # TODO: Implement file deletion from Google Drive
                # For now, we'll just delete the database record
            except:
                pass  # Ignore if file data is invalid
        
        # Delete the tenant record
        c.execute('DELETE FROM tenants WHERE id = ?', (tenant_id,))
        conn.commit()
        flash('Tenant deleted successfully!', 'success')
    
    conn.close()
    return redirect(url_for('list_tenants'))

@app.route('/get-rooms')
def get_rooms():
    property_id = request.args.get('property_id', type=int)
    rooms = ROOMS.get(property_id, [])
    return jsonify({'rooms': rooms})

@app.route('/my-properties')
def my_properties():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()

    # Get all properties for the logged-in user with all their room configurations
    properties_data = c.execute('''
        SELECT p.id, p.name, p.address,
               rc.id as room_config_id, rc.room_type, rc.room_count,
               rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit
        FROM properties p
        LEFT JOIN room_configurations rc ON p.id = rc.property_id
        WHERE p.user_id = ?
        ORDER BY p.id, rc.room_type -- Order by property and then room type
    ''', (session['user_id'],)).fetchall()

    # Format the properties data to group configurations by property
    formatted_properties = []
    current_property = None

    for row in properties_data:
        if current_property is None or current_property['id'] != row['id']:
            if current_property is not None:
                formatted_properties.append(current_property)

            current_property = {
                'id': row['id'],
                'name': row['name'],
                'address': row['address'],
                'room_configurations': [] # Renamed key to be more accurate
            }

        # Add room configuration if it exists for this property
        if row['room_config_id']: # Check if rc.id is not NULL
             current_property['room_configurations'].append({
                'id': row['room_config_id'],
                'room_type': row['room_type'],
                'room_count': row['room_count'],
                'rent': row['rent'],
                'electricity_charge': row['electricity_charge'],
                'water_charge': row['water_charge'],
                'security_deposit': row['security_deposit']
            })

    # Append the last property if it exists
    if current_property is not None:
        formatted_properties.append(current_property)

    conn.close()
    return render_template('my_properties.html',
                         properties=formatted_properties)

@app.route('/edit-property/<int:property_id>', methods=['GET', 'POST'])
def edit_property(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()

    # Get property and room configuration data for GET request and validation
    property_data = c.execute('''
        SELECT * FROM properties
        WHERE id = ? AND user_id = ?
    ''', (property_id, session['user_id'])).fetchone()

    if not property_data:
        conn.close()
        flash('Property not found', 'danger')
        return redirect(url_for('my_properties'))

    if request.method == 'POST':
        # Update property information
        name = request.form.get('name')
        address = request.form.get('address')

        c.execute('''UPDATE properties
                    SET name = ?, address = ?
                    WHERE id = ? AND user_id = ?''',
                 (name, address, property_id, session['user_id']))

        # --- Handle Room Configurations ---

        # 1. Update default room configurations (one_room and two_room)
        default_room_types = ['one_room', 'two_room']
        for room_type in default_room_types:
            room_count = request.form.get(f'{room_type}_count')
            rent = request.form.get(f'{room_type}_rent')
            electricity_charge = request.form.get(f'{room_type}_electricity')
            water_charge = request.form.get(f'{room_type}_water')
            security_deposit = request.form.get(f'{room_type}_security')

            # Find the existing configuration for this type and property
            existing_config_id = c.execute('''
                SELECT id FROM room_configurations
                WHERE property_id = ? AND room_type = ?
            ''', (property_id, room_type)).fetchone()

            if existing_config_id:
                # Update existing configuration
                c.execute('''UPDATE room_configurations
                            SET room_count = ?, rent = ?, electricity_charge = ?,
                                water_charge = ?, security_deposit = ?
                            WHERE id = ?''',
                         (room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0,
                          security_deposit or 0, existing_config_id[0]))
            else:
                 # Insert as a new configuration if it doesn't exist (shouldn't happen with current template logic but as a safeguard)
                if room_count or rent or electricity_charge or water_charge or security_deposit:
                     c.execute('''INSERT INTO room_configurations
                                 (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
                             (property_id, room_type, room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0, security_deposit or 0))


        # 2. Handle existing and new custom configurations
        submitted_existing_ids = []
        custom_configs_data = {}

        # Group submitted custom configuration data (existing and new)
        print("--- Start of Request Form Items ---")
        for key, value in request.form.items():
            print(f"Key: {key}, Value: {value}")
        print("--- End of Request Form Items ---")

        custom_configs_data = {}

        # Group submitted custom configuration data (existing and new)
        for key, value in request.form.items():
            if key.startswith('existing_custom_configs[') or key.startswith('new_custom_configs['):
                parts = key.split('[')
                index = int(parts[1].split(']')[0])
                field = parts[2].split(']')[0]
                config_type = parts[0] # 'existing_custom_configs' or 'new_custom_configs'

                if index not in custom_configs_data:
                    custom_configs_data[index] = {'_type': config_type}
                custom_configs_data[index][field] = value

        print("--- Start of Custom Configs Data ---")
        print(custom_configs_data)
        print("--- End of Custom Configs Data ---")
        # Process grouped custom configurations
        for index in sorted(custom_configs_data.keys()):
            config = custom_configs_data[index]
            config_type = config['_type']
            room_type = config.get('room_type')
            room_count = config.get('room_count')
            rent = config.get('rent')
            electricity_charge = config.get('electricity_charge')
            water_charge = config.get('water_charge')
            security_deposit = config.get('security_deposit')

            if room_type and (room_count or rent or electricity_charge or water_charge or security_deposit):
                if config_type == 'existing_custom_configs':
                    config_id = config.get('id')
                    if config_id:
                         # Update existing custom configuration
                        c.execute('''UPDATE room_configurations
                                    SET room_type = ?, room_count = ?, rent = ?, electricity_charge = ?,
                                        water_charge = ?, security_deposit = ?
                                    WHERE id = ? AND property_id = ?''', # Add property_id check for safety
                                 (room_type, room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0,
                                  security_deposit or 0, config_id, property_id))
                        submitted_existing_ids.append(int(config_id)) # Keep track of submitted existing IDs
                elif config_type == 'new_custom_configs':
                     print("--- Inserting New Custom Configuration ---")
                     print(f"Property ID: {property_id}, Room Type: {room_type}, Room Count: {room_count}, Rent: {rent}, Electricity Charge: {electricity_charge}, Water Charge: {water_charge}, Security Deposit: {security_deposit}")
                    # Insert new custom configuration
                     c.execute('''INSERT INTO room_configurations
                                 (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
                             (property_id, room_type, room_count or 0, rent or 0, electricity_charge or 0, water_charge or 0, security_deposit or 0))

        # # 3. Delete custom configurations that were removed on the form
        # # Get current custom config IDs from the database (excluding default types)
        # current_custom_configs_db = c.execute('''
        #     SELECT id FROM room_configurations
        #     WHERE property_id = ? AND room_type NOT IN (?, ?)
        # ''', (property_id, 'one_room', 'two_room')).fetchall()

        # # current_custom_ids_db = [config['id'] for config in current_custom_configs_db]

        # # Find IDs in DB that were NOT submitted in the form
        # ids_to_delete = [id for id in current_custom_ids_db if id not in submitted_existing_ids]

        # # if ids_to_delete:
        #     # Delete the configurations
        #     # Use a dynamic number of placeholders for the IN clause
        #     # placeholders = ','.join('?' for _ in ids_to_delete)
        #     # c.execute(f'''DELETE FROM room_configurations
        #     #             WHERE id IN ({placeholders}) AND property_id = ?''',
        #     #           ids_to_delete + [property_id]) # Add property_id for safety

        conn.commit()
        conn.close()

        flash('Property and room configurations updated successfully!', 'success')
        return redirect(url_for('my_properties'))

    # GET request part: Fetch property data and all room configurations
    # This part remains largely the same as before, ensuring all configs are fetched
    # The template logic handles separating default and custom ones for display
    room_configs = c.execute('''
        SELECT id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit
        FROM room_configurations
        WHERE property_id = ?
    ''', (property_id,)).fetchall()

    # Format room configurations into a dictionary for easy access by type in template
    # And also keep a list for iterating over all configs
    room_data = {}
    all_room_configs_list = [] # Use a list to pass all configurations

    for room in room_configs:
        # Use room_type as key for default configs for easy lookup
        if room['room_type'] in ['one_room', 'two_room']:
             room_data[room['room_type']] = {
                'id': room['id'], # Include ID for updates
                'room_count': room['room_count'],
                'rent': room['rent'],
                'electricity_charge': room['electricity_charge'],
                'water_charge': room['water_charge'],
                'security_deposit': room['security_deposit']
             }
        # Add all configurations to the list
        all_room_configs_list.append(dict(room)) # Convert Row to dict for consistency


    conn.close()
    return render_template('edit_property.html',
                         property=property_data,
                         room_data=room_data, # For default configs
                         room_configs=all_room_configs_list) # For iterating through all in template

@app.route('/delete-property/<int:property_id>', methods=['POST'])
def delete_property(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # First delete room configurations
    c.execute('DELETE FROM room_configurations WHERE property_id = ?', (property_id,))
    
    # Then delete the property
    c.execute('DELETE FROM properties WHERE id = ? AND user_id = ?', 
             (property_id, session['user_id']))
    
    conn.commit()
    conn.close()
    
    flash('Property deleted successfully!', 'success')
    return redirect(url_for('my_properties'))

@app.route('/edit-tenant/<int:tenant_id>', methods=['GET', 'POST'])
def edit_tenant(tenant_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        tenant_name = request.form.get('tenant_name')
        phone_number = request.form.get('phone_number')
        email = request.form.get('email')  # Get email from form
        move_in_date = request.form.get('move_in_date')
        move_out_date = request.form.get('move_out_date')
        police_verification = request.files.get('police_verification')
        
        # Get current tenant data, including property name and room number
        current_tenant = c.execute('''
            SELECT t.*, p.user_id, p.name as property_name, r.room_number
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            JOIN rooms r ON t.room_id = r.id
            WHERE t.id = ?
        ''', (tenant_id,)).fetchone()
        
        if not current_tenant or current_tenant['user_id'] != session['user_id']: # Use dict access
            conn.close()
            flash('Tenant not found', 'danger')
            return redirect(url_for('list_tenants'))
        
        # Handle police verification file
        drive_file_data = current_tenant['police_verification']  # Keep existing file data by default
        if police_verification:
            # --- Google Drive Upload --- START
            property_name = current_tenant['property_name'] # Get property name
            room_number = current_tenant['room_number'] # Get room number

            # Find or create property folder
            folder_id = find_or_create_folder(property_name)

            if folder_id:
                # Save file temporarily
                temp_filename = f"temp_police_verification_{current_tenant['property_id']}_{current_tenant['room_id']}_{int(time.time())}.pdf"
                temp_path = os.path.join('app/static/uploads', temp_filename)
                police_verification.save(temp_path)

                try:
                    # Upload to Google Drive into the property folder
                    drive_file_id = upload_file(
                        temp_path,
                        f"police_verification_{tenant_name}_Room{room_number}.pdf", # Include room number
                        'application/pdf',
                        folder_id=folder_id
                    )

                    # Get the web view URL
                    drive_file_url = get_file_url(drive_file_id)

                    # Store both the file ID and URL in the database
                    drive_file_data = f"{drive_file_id}|{drive_file_url}"
                except Exception as e:
                    print(f"Error uploading to Google Drive: {str(e)}")
                    flash('Error uploading police verification document', 'danger')
                    drive_file_data = current_tenant['police_verification'] # Keep existing on failure
                finally:
                    # Clean up temporary file
                    try:
                        os.remove(temp_path)
                    except: # Handle cases where the temp file might not exist for some reason
                        pass
            else:
                flash('Could not create or find Google Drive folder for the property.', 'danger')
                drive_file_data = current_tenant['police_verification'] # Keep existing on failure
            # --- Google Drive Upload --- END

        # Update tenant data
        c.execute('''
            UPDATE tenants
            SET name = ?, phone_number = ?, email = ?, move_in_date = ?, move_out_date = ?, police_verification = ?
            WHERE id = ?
        ''', (tenant_name, phone_number, email, move_in_date, move_out_date, drive_file_data, tenant_id))

        conn.commit()
        conn.close()

        flash('Tenant updated successfully!', 'success')
        return redirect(url_for('list_tenants'))
    
    # Get tenant data for editing
    tenant = c.execute('''
        SELECT t.id, t.name, t.property_id, t.room_id, t.phone_number, t.email, t.move_in_date, t.move_out_date, t.police_verification,
               p.name as property_name,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
               r.room_number
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if not tenant:
        conn.close()
        flash('Tenant not found', 'danger')
        return redirect(url_for('list_tenants'))
    
    # Parse the police verification data
    police_verification_url = None
    if tenant[8]:  # police_verification field
        try:
            _, police_verification_url = tenant[8].split('|')
        except:
            pass
    
    formatted_tenant = {
        'id': tenant[0],
        'name': tenant[1],
        'property_id': tenant[2],
        'room_id': tenant[3],
        'phone_number': tenant[4],
        'email': tenant[5],  # Add email field
        'move_in_date': tenant[6],
        'move_out_date': tenant[7],
        'police_verification': police_verification_url,
        'property_name': tenant[9],
        'room_type': tenant[10],
        'rent': tenant[11],
        'electricity_charge': tenant[12],
        'water_charge': tenant[13],
        'room_number': tenant[14]
    }
    
    conn.close()
    return render_template('edit_tenant.html', tenant=formatted_tenant)

@app.route('/add-room/<int:property_id>', methods=['POST'])
def add_room(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    # Debug logging
    print("\n=== ADD ROOM ROUTE CALLED ===")
    print("Request Method:", request.method)
    print("Request Files:", request.files)
    print("Request Form:", request.form)
    
    # Verify property ownership
    conn = get_db()
    c = conn.cursor()
    property = c.execute('SELECT * FROM properties WHERE id = ? AND user_id = ?', 
                        (property_id, session['user_id'])).fetchone()
    
    if not property:
        conn.close()
        flash('Property not found', 'danger')
        return redirect(url_for('my_properties'))
    
    # Get form data
    room_config_id = request.form.get('room_config')
    room_number = request.form.get('room_number')
    room_media = request.files.get('room_media')
    print("Room Media File:", room_media)
    print("Room Media Filename:", room_media.filename if room_media else None)
    print("Room Config ID:", room_config_id)
    print("Room Number:", room_number)
    
    # Get room configuration details
    room_config = c.execute('''
        SELECT room_count FROM room_configurations 
        WHERE id = ? AND property_id = ?
    ''', (room_config_id, property_id)).fetchone()
    print("Room Config Query Result:", room_config)
    
    if not room_config:
        print("Room configuration not found")
        conn.close()
        flash('Room configuration not found', 'danger')
        return redirect(url_for('my_properties'))
    
    # Check if room number already exists for this property
    existing_room = c.execute('''
        SELECT id FROM rooms 
        WHERE property_id = ? AND room_number = ?
    ''', (property_id, room_number)).fetchone()
    print("Existing Room Check Result:", existing_room)
    
    if existing_room:
        print("Room number already exists")
        conn.close()
        flash('Room number already exists for this property', 'danger')
        return redirect(url_for('my_properties'))
    
    # Check if we've reached the room count limit for this configuration
    current_rooms = c.execute('''
        SELECT COUNT(*) FROM rooms 
        WHERE property_id = ? AND room_config_id = ?
    ''', (property_id, room_config_id)).fetchone()[0]
    print("Current Rooms Count:", current_rooms)
    print("Room Config Count Limit:", room_config[0])
    
    if current_rooms >= room_config[0]:
        print("Maximum rooms reached for configuration")
        conn.close()
        flash('Maximum number of rooms reached for this configuration', 'danger')
        return redirect(url_for('my_properties'))
    
    print("All validation checks passed, proceeding with room creation")
    
    # Handle room media upload if provided
    media_url = None
    if room_media and room_media.filename:  # Check if file was actually uploaded
        try:
            print("Starting media upload process...")
            # Create the folder structure in Google Drive
            try:
                inventory_folder = find_or_create_folder('inventory')
                print("Inventory folder created/found:", inventory_folder)
            except Exception as e:
                print("Error creating inventory folder:", str(e))
                raise
            
            try:
                videos_folder = find_or_create_folder('videos', parent_folder_id=inventory_folder)
                print("Videos folder created/found:", videos_folder)
            except Exception as e:
                print("Error creating videos folder:", str(e))
                raise
            
            try:
                building_folder = find_or_create_folder(property[1], parent_folder_id=videos_folder)
                print("Building folder created/found:", building_folder)
            except Exception as e:
                print("Error creating building folder:", str(e))
                raise
            
            # Save file temporarily
            temp_filename = f"temp_room_media_{property_id}_{room_number}_{int(time.time())}"
            temp_path = os.path.join(UPLOADS_DIR, temp_filename)
            print("Saving file to temp path:", temp_path)
            try:
                room_media.save(temp_path)
                print("File saved successfully to temp path")
            except Exception as e:
                print("Error saving file to temp path:", str(e))
                raise
            
            # Determine file extension and mime type
            file_ext = os.path.splitext(room_media.filename)[1].lower()
            mime_type = 'video/mp4' if file_ext == '.mp4' else 'image/jpeg'
            print("File extension:", file_ext)
            print("MIME type:", mime_type)
            
            # Upload to Google Drive
            try:
                drive_file_id = upload_file(
                    temp_path,
                    f"room_{room_number}{file_ext}",
                    mime_type,
                    folder_id=building_folder
                )
                print("File uploaded to Drive with ID:", drive_file_id)
            except Exception as e:
                print("Error uploading file to Drive:", str(e))
                raise
            
            # Get the web view URL
            try:
                media_url = get_file_url(drive_file_id)
                print("Media URL:", media_url)
            except Exception as e:
                print("Error getting file URL:", str(e))
                raise
            
            # Clean up temporary file
            try:
                os.remove(temp_path)
                print("Temporary file cleaned up")
            except Exception as e:
                print("Error cleaning up temp file:", str(e))
                # Don't raise here, as the file is already uploaded
                
        except Exception as e:
            print("Error in media upload process:", str(e))
            flash('Error uploading room media: ' + str(e), 'warning')
            # Clean up temp file if it exists
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
    
    # Insert new room
    c.execute('''INSERT INTO rooms 
                 (property_id, room_config_id, room_number, media_url) 
                 VALUES (?, ?, ?, ?)''',
             (property_id, room_config_id, room_number, media_url))
    
    conn.commit()
    conn.close()
    
    flash('Room added successfully!', 'success')
    return redirect(url_for('my_properties'))

@app.route('/view-rooms/<int:property_id>')
def view_rooms(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get property details
    property_data = c.execute('''
        SELECT * FROM properties 
        WHERE id = ? AND user_id = ?
    ''', (property_id, session['user_id'])).fetchone()
    
    if not property_data:
        conn.close()
        flash('Property not found or unauthorized', 'danger')
        return redirect(url_for('list_properties'))
    
    # Get all rooms for the property with their configurations and current tenant info
    rooms = c.execute('''
        SELECT r.*, rc.*, 
               t.name as current_tenant_name,
               t.move_in_date as tenant_move_in_date,
               t.move_out_date as tenant_move_out_date,
               r.is_available
        FROM rooms r
        LEFT JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN tenants t ON r.id = t.room_id AND t.move_out_date IS NULL
        WHERE r.property_id = ?
        ORDER BY r.room_number
    ''', (property_id,)).fetchall()
    
    # Format rooms data
    formatted_rooms = []
    for room in rooms:
        formatted_room = {
            'id': room['id'],
            'room_number': room['room_number'],
            'room_type': room['room_type'],
            'rent': room['rent'],
            'electricity_charge': room['electricity_charge'],
            'water_charge': room['water_charge'],
            'security_deposit': room['security_deposit'],
            'is_occupied': not room['is_available'],
            'current_tenant': {
                'name': room['current_tenant_name'],
                'move_in_date': room['tenant_move_in_date'],
                'move_out_date': room['tenant_move_out_date']
            } if room['current_tenant_name'] else None
        }
        formatted_rooms.append(formatted_room)
    
    conn.close()
    return render_template('view_rooms.html', property=property_data, rooms=formatted_rooms)

@app.route('/delete-room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Verify room ownership and check if occupied
    room_data = c.execute('''
        SELECT r.id, r.room_number, r.property_id, p.user_id, t.id as tenant_id
        FROM rooms r
        JOIN properties p ON r.property_id = p.id
        LEFT JOIN tenants t ON r.id = t.room_id
        WHERE r.id = ?
    ''', (room_id,)).fetchone()
    
    if not room_data:
        conn.close()
        flash('Room not found', 'danger')
        # Redirect to a safe page, perhaps the property list or dashboard
        return redirect(url_for('my_properties'))
    
    # Check if the property belongs to the logged-in user
    if room_data['user_id'] != session['user_id']:
        conn.close()
        flash('Unauthorized to delete this room', 'danger')
        return redirect(url_for('my_properties'))
    
    # Check if the room is occupied
    if room_data['tenant_id'] is not None:
        conn.close()
        flash(f'Cannot delete Room {room_data["room_number"]} as it is currently occupied.', 'danger')
        # Redirect back to the view rooms page for this property
        return redirect(url_for('view_rooms', property_id=room_data['property_id']))
    
    # If not occupied and authorized, proceed with deletion
    try:
        c.execute('DELETE FROM rooms WHERE id = ?', (room_id,))
        conn.commit()
        flash(f'Room {room_data["room_number"]} deleted successfully!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'An error occurred while deleting Room {room_data["room_number"]}: {str(e)}', 'danger')
    finally:
        conn.close()
    
    # Redirect back to the view rooms page for this property
    return redirect(url_for('view_rooms', property_id=room_data['property_id']))

@app.route('/bills')
def view_bills():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get current month's bills
    current_date = datetime.now()
    current_month = current_date.month
    current_year = current_date.year
    
    # Get all tenants with their property and room information
    tenants = c.execute('''
        SELECT t.*, p.name as property_name,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
               r.room_number
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE p.user_id = ?
        ORDER BY t.move_in_date DESC
    ''', (session['user_id'],)).fetchall()
    
    # Format tenants data and calculate bills
    formatted_tenants = []
    total_pending = 0

    print("View Bills Formatted Tenants 1:", formatted_tenants)
    
    for tenant in tenants:
        # Calculate bill amount
        rent = float(tenant[9])  # rent from room_configurations
        electricity = float(tenant[10])  # electricity_charge
        water = float(tenant[11])  # water_charge
        total_amount = rent + electricity + water
        
        # Get move-in date
        move_in_date = datetime.strptime(tenant[5], '%Y-%m-%d')
        
        # Calculate due date (same day of month as move-in)
        due_date = current_date.replace(day=move_in_date.day)
        # if due_date < current_date:
        #     due_date = (due_date.replace(day=1) + timedelta(days=32)).replace(day=1)
        
        # Check if bill is pending
        # is_pending = current_date >= due_date
        
        # if is_pending:
        #     total_pending += total_amount
        
        formatted_tenants.append({
            'id': tenant[0],
            'name': tenant[1],
            'property_id': tenant[2],
            'room_id': tenant[3],
            'phone_number': tenant[4],
            'move_in_date': tenant[5],
            'property_name': tenant[7],
            'room_type': tenant[8],
            'rent': rent,
            'electricity_charge': electricity,
            'water_charge': water,
            'total_amount': total_amount,
            'due_date': due_date.strftime('%Y-%m-%d')
        })
        print("View Bills Formatted Tenants:", formatted_tenants)
    
    conn.close()
    return render_template('bills.html', 
                         tenants=formatted_tenants,
                         total_pending=total_pending,
                         current_month=current_date.strftime('%B %Y'))

@app.route('/monthly-bills/<int:year>/<int:month>')
def monthly_bills(year, month):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get month name
    month_name = datetime(year, month, 1).strftime('%B %Y')
    
    # Get all tenants with their bills for the specified month
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            p.name as property_name,
            t.name as tenant_name,
            r.room_number,
            rc.rent as rent_amount,
            COALESCE(SUM(bp.amount), 0) as paid_amount,
            rc.electricity_charge as electricity_rate,
            rc.water_charge as water_rate,
            t.id as tenant_id,
            t.move_in_date,
            CASE 
                WHEN t.move_in_date <= date('now') THEN date('now', 'start of month', '+4 days')
                ELSE date(t.move_in_date, 'start of month', '+4 days')
            END as due_date
        FROM tenants t
        JOIN rooms r ON t.room_id = r.id
        JOIN properties p ON r.property_id = p.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
            AND strftime('%Y', bp.payment_date) = ? 
            AND strftime('%m', bp.payment_date) = ?
        WHERE t.user_id = ?
        GROUP BY t.id
    ''', (str(year), f"{month:02d}", session['user_id']))
    
    tenants = cursor.fetchall()
    
    # Calculate monthly totals
    monthly_stats = {
        'paid': sum(tenant['paid_amount'] for tenant in tenants),
        'pending': sum(tenant['rent_amount'] + tenant['electricity_rate'] + tenant['water_rate'] - tenant['paid_amount'] for tenant in tenants)
    }
    
    # Get today's date for the payment form
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    conn.close()
    return render_template('monthly_bills.html', 
                         tenants=tenants,
                         month_name=month_name,
                         monthly_stats=monthly_stats,
                         today_date=today_date)

@app.route('/all-bills')
def all_bills():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all tenants with their bills
    cursor.execute('''
        SELECT 
            p.name as property_name,
            t.name as tenant_name,
            r.room_number,
            rc.rent as rent_amount,
            COALESCE(SUM(bp.amount), 0) as paid_amount,
            rc.electricity_charge as electricity_rate,
            rc.water_charge as water_rate,
            t.id as tenant_id,
            t.move_in_date,
            CASE 
                WHEN t.move_in_date <= date('now') THEN date('now', 'start of month', '+4 days')
                ELSE date(t.move_in_date, 'start of month', '+4 days')
            END as due_date,
            p.id as property_id,
            (rc.rent + rc.electricity_charge + rc.water_charge) as total_amount,
            CASE 
                WHEN EXISTS (
                    SELECT 1 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', '-1 month'))
                ) THEN (rc.rent + rc.electricity_charge + rc.water_charge - COALESCE((
                    SELECT SUM(amount) 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', '-1 month'))
                ), 0))
                ELSE 0
            END as prev_month_pending,
            (rc.rent + rc.electricity_charge + rc.water_charge - COALESCE(SUM(bp.amount), 0)) as current_month_pending,
            ((rc.rent + rc.electricity_charge + rc.water_charge - COALESCE(SUM(bp.amount), 0)) + 
            CASE 
                WHEN EXISTS (
                    SELECT 1 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', '-1 month'))
                ) THEN (rc.rent + rc.electricity_charge + rc.water_charge - COALESCE((
                    SELECT SUM(amount) 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', '-1 month'))
                ), 0))
                ELSE 0
            END) as total_pending
        FROM tenants t
        JOIN rooms r ON t.room_id = r.id
        JOIN properties p ON r.property_id = p.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
            AND strftime('%Y-%m', bp.payment_date) = strftime('%Y-%m', 'now')
        WHERE p.user_id = ?
        GROUP BY t.id
        ORDER BY r.room_number
    ''', (session['user_id'],))
    
    tenants = cursor.fetchall()
    
    # Calculate total statistics
    total_stats = {
        'total': sum(tenant['total_amount'] for tenant in tenants),
        'paid': sum(tenant['paid_amount'] for tenant in tenants),
        'pending': sum(tenant['total_pending'] for tenant in tenants)
    }
    
    # Get last 6 months for the monthly overview
    today = datetime.now()
    months = []
    monthly_totals = {}
    
    for i in range(6):
        month_date = today - timedelta(days=30*i)
        year = month_date.year
        month = month_date.month
        
        # Get monthly totals
        cursor.execute('''
            SELECT 
                COALESCE(SUM(bp.amount), 0) as paid_amount,
                COUNT(DISTINCT t.id) * (rc.rent + rc.electricity_charge + rc.water_charge) as expected_total
            FROM tenants t
            JOIN rooms r ON t.room_id = r.id
            JOIN properties p ON r.property_id = p.id
            JOIN room_configurations rc ON r.room_config_id = rc.id
            LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
                AND strftime('%Y', bp.payment_date) = ? 
                AND strftime('%m', bp.payment_date) = ?
            WHERE p.user_id = ?
        ''', (str(year), f"{month:02d}", session['user_id']))
        
        result = cursor.fetchone()
        monthly_totals[f"{year}-{month:02d}"] = {
            'paid': result['paid_amount'] if result else 0,
            'pending': (result['expected_total'] - result['paid_amount']) if result else 0
        }
        
        months.append(month_date)
    
    # Get all properties for the filter dropdown
    properties = cursor.execute('''
        SELECT id, name 
        FROM properties 
        WHERE user_id = ?
    ''', (session['user_id'],)).fetchall()
    
    # Get today's date for the payment form
    today_date = today.strftime('%Y-%m-%d')
    
    conn.close()
    return render_template('all_bills.html',
                         tenants=tenants,
                         total_stats=total_stats,
                         months=months,
                         monthly_totals=monthly_totals,
                         properties=properties,
                         today_date=today_date,
                         total_pending=total_stats['pending'],
                         current_month=today.strftime('%B %Y'))

@app.route('/bill-payments/<int:tenant_id>')
def bill_payments(tenant_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get tenant details
    tenant = c.execute('''
        SELECT t.*, p.name as property_name, r.room_number, rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if not tenant:
        conn.close()
        flash('Tenant not found', 'danger')
        return redirect(url_for('all_bills'))
    
    # Get payment history
    payments = c.execute('''
        SELECT * FROM bill_payments 
        WHERE tenant_id = ? 
        ORDER BY payment_date DESC
    ''', (tenant_id,)).fetchall()
    
    # Format tenant data
    formatted_tenant = {
        'id': tenant[0],
        'name': tenant[1],
        'property_name': tenant[7],
        'room_number': tenant[8],
        'room_type': tenant[9],
        'rent': tenant[10],
        'electricity_charge': tenant[11],
        'water_charge': tenant[12]
    }
    
    # Format payments data
    formatted_payments = []
    for payment in payments:
        formatted_payments.append({
            'id': payment[0],
            'amount': payment[2],
            'payment_date': payment[3],
            'payment_mode': payment[4],
            'notes': payment[5],
            'created_at': payment[6],
            'total_amount': payment[7],
            'paid_amount': payment[8],
            'pending_amount': payment[9]
        })
    
    conn.close()
    return render_template('bill_payments.html', 
                         tenant=formatted_tenant, 
                         payments=formatted_payments)

@app.route('/add-payment', methods=['POST'])
def add_payment():
    print("\n=== ADD PAYMENT ROUTE CALLED ===")
    print("Request Method:", request.method)
    print("Request Data:", request.json)
    
    if 'user_id' not in session:
        print("Error: User not logged in")
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    tenant_id = data.get('tenant_id')
    amount = data.get('amount')
    payment_date = data.get('payment_date')
    payment_mode = data.get('payment_mode')
    notes = data.get('notes')
    
    print("\nProcessing Payment:")
    print(f"Tenant ID: {tenant_id}")
    print(f"Amount: {amount}")
    print(f"Date: {payment_date}")
    print(f"Mode: {payment_mode}")
    print(f"Notes: {notes}")
    
    conn = get_db()
    c = conn.cursor()
    
    # Verify tenant belongs to user and get their charges
    tenant = c.execute('''
        SELECT t.id, t.name, rc.rent, rc.electricity_charge, rc.water_charge
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if not tenant:
        conn.close()
        print("Error: Tenant not found")
        return jsonify({'error': 'Tenant not found'}), 404
    
    # Calculate total amount for the month
    total_amount = float(tenant[2]) + float(tenant[3]) + float(tenant[4])  # rent + electricity + water
    
    # Get existing payments for this month
    payment_date_obj = datetime.strptime(payment_date, '%Y-%m-%d')
    month = payment_date_obj.month
    year = payment_date_obj.year
    
    print("\nAdding Payment Details:")
    print(f"Tenant: {tenant[1]}")
    print(f"Payment Amount: ₹{amount}")
    print(f"Payment Date: {payment_date}")
    print(f"Payment Mode: {payment_mode}")
    print(f"Notes: {notes}")
    print(f"Month/Year: {month}/{year}")
    
    existing_payments = c.execute('''
        SELECT COALESCE(SUM(amount), 0) FROM bill_payments 
        WHERE tenant_id = ? 
        AND strftime('%m', payment_date) = ? 
        AND strftime('%Y', payment_date) = ?
    ''', (tenant_id, str(month).zfill(2), str(year))).fetchone()[0]
    
    print(f"Existing Payments for {month}/{year}: ₹{existing_payments}")
    
    # Convert amount to float
    amount = float(amount)
    
    # If it's a penalty (notes contain "PENALTY:"), invert the sign
    if notes and "PENALTY:" in notes.upper():
        amount = -amount  # Invert the sign
    
    # Calculate new paid and pending amounts
    paid_amount = float(existing_payments) + amount
    pending_amount = total_amount - paid_amount
    
    print(f"Total Amount: ₹{total_amount}")
    print(f"New Paid Amount: ₹{paid_amount}")
    print(f"New Pending Amount: ₹{pending_amount}")
    
    # Create new payment record
    c.execute('''
        INSERT INTO bill_payments 
        (tenant_id, amount, payment_date, payment_mode, notes, total_amount, paid_amount, pending_amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (tenant_id, amount, payment_date, payment_mode, notes, total_amount, paid_amount, pending_amount))
    
    print("Payment Added Successfully!")
    print("----------------------------------------")
    
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'payment': {
            'amount': amount,
            'date': payment_date,
            'mode': payment_mode,
            'notes': notes,
            'total_amount': total_amount,
            'paid_amount': paid_amount,
            'pending_amount': pending_amount
        }
    })

@app.route('/export-bills')
def export_bills():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get current month's bills for all tenants
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    # Calculate previous month and year
    if current_month == 1:
        prev_month = 12
        prev_year = current_year - 1
    else:
        prev_month = current_month - 1
        prev_year = current_year
    
    # Get all bills data
    bills_data = c.execute('''
        SELECT 
            p.name as property_name,
            t.name as tenant_name,
            r.room_number,
            rc.rent,
            COALESCE(SUM(CASE 
                WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                THEN bp.amount 
                ELSE 0 
            END), 0) as current_month_paid,
            COALESCE(SUM(CASE 
                WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                THEN bp.amount 
                ELSE 0 
            END), 0) as prev_month_paid,
            rc.electricity_charge,
            rc.water_charge,
            t.move_in_date,
            CASE 
                WHEN t.move_in_date <= date('now') THEN date('now', 'start of month', '+4 days')
                ELSE date(t.move_in_date, 'start of month', '+4 days')
            END as due_date
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
            AND (strftime('%m', bp.payment_date) IN (?, ?) 
                AND strftime('%Y', bp.payment_date) IN (?, ?))
        WHERE p.user_id = ?
        GROUP BY t.id
    ''', (str(current_month).zfill(2), str(current_year),
          str(prev_month).zfill(2), str(prev_year),
          str(current_month).zfill(2), str(prev_month).zfill(2),
          str(current_year), str(prev_year),
          session['user_id'])).fetchall()
    
    # Create DataFrame
    df = pd.DataFrame(bills_data, columns=[
        'Property', 'Tenant', 'Room', 'Rent', 'Current Month Paid',
        'Previous Month Paid', 'Electricity', 'Water', 'Move-in Date', 'Due Date'
    ])
    
    # Calculate additional columns
    df['Total Amount'] = df['Rent'] + df['Electricity'] + df['Water']
    df['Previous Month Pending'] = df['Total Amount'] - df['Previous Month Paid']
    df['Current Month Pending'] = df['Total Amount'] - df['Current Month Paid']
    df['Total Pending'] = df['Previous Month Pending'] + df['Current Month Pending']
    
    # Format currency columns
    currency_columns = ['Rent', 'Current Month Paid', 'Previous Month Paid', 
                       'Electricity', 'Water', 'Total Amount', 
                       'Previous Month Pending', 'Current Month Pending', 'Total Pending']
    
    for col in currency_columns:
        df[col] = df[col].apply(lambda x: f'₹{x:,.2f}')
    
    # Format dates
    df['Move-in Date'] = pd.to_datetime(df['Move-in Date']).dt.strftime('%Y-%m-%d')
    df['Due Date'] = pd.to_datetime(df['Due Date']).dt.strftime('%Y-%m-%d')
    
    # Create Excel file in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Bills', index=False)
        
        # Get workbook and worksheet objects
        workbook = writer.book
        worksheet = writer.sheets['Bills']
        
        # Add some formatting
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#4CAF50',
            'font_color': 'white',
            'border': 1
        })
        
        # Write the column headers with the defined format
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 15)  # Set column width
    
    # Seek to the beginning of the BytesIO buffer
    output.seek(0)
    
    # Generate filename with current date
    filename = f'bills_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    
    conn.close()
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/monthly-bills/<string:month>')
def api_monthly_bills(month):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    property_id = request.args.get('property_id')
    tenant_id = request.args.get('tenant_id')
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Parse month string (format: YYYY-MM)
    year, month = month.split('-')
    
    # Base query
    query = '''
        WITH latest_payments AS (
            SELECT 
                tenant_id,
                id as payment_id,
                payment_date,
                ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY payment_date DESC) as rn
            FROM bill_payments
        )
        SELECT 
            p.name as property_name,
            t.name as tenant_name,
            r.room_number,
            rc.rent as rent_amount,
            COALESCE(SUM(bp.amount), 0) as paid_amount,
            rc.electricity_charge as electricity_rate,
            rc.water_charge as water_rate,
            t.id as tenant_id,
            t.move_in_date,
            CASE 
                WHEN t.move_in_date <= date('now') THEN date('now', 'start of month', '+4 days')
                ELSE date(t.move_in_date, 'start of month', '+4 days')
            END as due_date,
            (rc.rent + rc.electricity_charge + rc.water_charge) as total_amount,
            (rc.rent + rc.electricity_charge + rc.water_charge - COALESCE(SUM(bp.amount), 0)) as pending_amount,
            lp.payment_id as latest_payment_id
        FROM tenants t
        JOIN rooms r ON t.room_id = r.id
        JOIN properties p ON r.property_id = p.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN bill_payments bp ON t.id = bp.tenant_id 
            AND strftime('%Y', bp.payment_date) = ? 
            AND strftime('%m', bp.payment_date) = ?
        LEFT JOIN latest_payments lp ON t.id = lp.tenant_id AND lp.rn = 1
        WHERE p.user_id = ?
        AND strftime('%Y-%m', t.move_in_date) <= ?
    '''
    
    params = [year, month, session['user_id'], f"{year}-{month}"]
    
    # Add property filter if specified
    if property_id:
        query += ' AND p.id = ?'
        params.append(property_id)
    
    # Add tenant filter if specified
    if tenant_id:
        query += ' AND t.id = ?'
        params.append(tenant_id)
    
    query += ' GROUP BY t.id'
    
    cursor.execute(query, params)
    tenants = cursor.fetchall()
    
    # Convert Row objects to dictionaries
    tenants_list = []
    for tenant in tenants:
        tenant_dict = dict(tenant)
        # Convert numeric values to float
        for key in ['rent_amount', 'paid_amount', 'electricity_rate', 'water_rate', 'total_amount', 'pending_amount']:
            tenant_dict[key] = float(tenant_dict[key])
        tenants_list.append(tenant_dict)
    
    # Calculate totals
    total_expected = sum(tenant['total_amount'] for tenant in tenants_list)
    total_received = sum(tenant['paid_amount'] for tenant in tenants_list)
    total_pending = sum(tenant['pending_amount'] for tenant in tenants_list)
    
    conn.close()
    
    return jsonify({
        'tenants': tenants_list,
        'total_expected': total_expected,
        'total_received': total_received,
        'total_pending': total_pending
    })

@app.route('/delete-payment/<int:payment_id>', methods=['POST'])
def delete_payment(payment_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get payment details to verify ownership
    payment = c.execute('''
        SELECT bp.*, t.id as tenant_id 
        FROM bill_payments bp
        JOIN tenants t ON bp.tenant_id = t.id
        JOIN properties p ON t.property_id = p.id
        WHERE bp.id = ? AND p.user_id = ?
    ''', (payment_id, session['user_id'])).fetchone()
    
    if payment:
        # Delete the payment
        c.execute('DELETE FROM bill_payments WHERE id = ?', (payment_id,))
        conn.commit()
        flash('Payment deleted successfully!', 'success')
    else:
        flash('Payment not found or unauthorized', 'danger')
    
    conn.close()
    return redirect(url_for('bill_payments', tenant_id=payment['tenant_id']))

@app.route('/send-reminder/<int:payment_id>', methods=['POST'])
def send_reminder(payment_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get payment and tenant details
    payment_data = c.execute('''
        SELECT bp.*, t.name as tenant_name, t.email, t.phone_number, p.name as property_name, r.room_number
        FROM bill_payments bp
        JOIN tenants t ON bp.tenant_id = t.id
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        WHERE bp.id = ? AND p.user_id = ?
    ''', (payment_id, session['user_id'])).fetchone()
    
    if not payment_data:
        conn.close()
        flash('Payment not found or unauthorized', 'danger')
        return redirect(url_for('all_bills'))
    
    # Email reminder (existing code)
    if payment_data['email']:
        try:
            # Create email message
            msg = Message(
                'Payment Reminder - RentEase',
                recipients=[payment_data['email']]
                # cc=['trnvshisth@gmail.com']
            )
            
            # Format the email body with HTML
            msg.html = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #000000; }}
                    .container {{ width: 100%; max-width: 600px; margin: 0 auto; border: 1px solid #dddddd; border-collapse: collapse; }}
                    .header {{ background-color: #d32f2f; padding: 10px 0; text-align: center; }}
                    .header img {{ max-width: 150px; height: auto; }}
                    .content {{ padding: 20px; }}
                    .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #555555; }}
                    .button {{ display: inline-block; background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <table class="container" cellpadding="0" cellspacing="0" role="presentation">
                    <!-- Header -->
                    

                    <!-- Main Content -->
                    <tr>
                        <td class="content">
                            <p>Dear {payment_data['tenant_name']},</p>

                            <p>I hope this email finds you well. This is a friendly reminder regarding your pending payment at <strong>{payment_data['property_name']}</strong>, Room <strong>{payment_data['room_number']}</strong>.</p>

                            <div style="margin: 20px 0; padding: 15px; border: 1px solid #eeeeee; border-left: 4px solid #f44336;">
                                <h3 style="color: #000000; margin-top: 0;">📊 Payment Details:</h3>
                                <p style="margin-bottom: 5px;"><strong>Pending Amount:</strong> ₹{payment_data['pending_amount']:.2f}</p>
                                <p style="margin-bottom: 0;">Please ensure timely payment.</p>
                            </div>

                            <p>Best regards,<br>
                            RentEase Team</p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td class="footer">
                            <p style="color: #000000;">This is a system-generated e-mail. Please do not reply to this e-mail.</p>
                            <!-- Placeholder for Footer Image Banner -->
                            <!-- Example: <img src="YOUR_BANNER_IMAGE_URL" alt="Offer Banner" style="display: block; max-width: 100%; height: auto; margin-top: 20px;"> -->
                        </td>
                    </tr>
                </table>
            </body>
            </html>
            '''
            
            # Send the email
            mail.send(msg)
            flash('Email reminder sent successfully!', 'success')
        except Exception as e:
            print(f"Failed to send email: {str(e)}")
            flash('Failed to send reminder email', 'danger')
    
    send_whatsapp_reminder = False
    # WhatsApp reminder
    if payment_data['phone_number'] and send_whatsapp_reminder:
        try:
            phone_number = payment_data['phone_number']
            # Ensure phone number is in international format (e.g., 91xxxxxxxxxx)
            if not phone_number.startswith("+"):
                phone_number = "91" + phone_number  # Change '91' to your country code if needed
            wa_response = send_whatsapp_message(phone_number)
            if wa_response.get("messages"):
                flash('WhatsApp reminder sent successfully!', 'success')
            else:
                flash(f"Failed to send WhatsApp reminder: {wa_response}", 'warning')
        except Exception as e:
            print(f"Failed to send WhatsApp message: {str(e)}")
            flash('Failed to send WhatsApp reminder', 'danger')
    
    conn.close()
    return redirect(url_for('all_bills'))

@app.route('/edit-room/<int:room_id>', methods=['POST'])
def edit_room(room_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Verify room ownership
    room_data = c.execute('''
        SELECT r.*, p.user_id, p.name as property_name
        FROM rooms r
        JOIN properties p ON r.property_id = p.id
        WHERE r.id = ?
    ''', (room_id,)).fetchone()
    
    if not room_data or room_data['user_id'] != session['user_id']:
        conn.close()
        flash('Room not found or unauthorized', 'danger')
        return redirect(url_for('my_properties'))
    
    # Get form data
    room_number = request.form.get('room_number')
    room_config_id = request.form.get('room_config')
    room_media = request.files.get('room_media')
    
    # Verify room configuration belongs to the property
    config = c.execute('''
        SELECT id FROM room_configurations 
        WHERE id = ? AND property_id = ?
    ''', (room_config_id, room_data['property_id'])).fetchone()
    
    if not config:
        conn.close()
        flash('Invalid room configuration', 'danger')
        return redirect(url_for('view_rooms', property_id=room_data['property_id']))
    
    # Check if new room number already exists for this property
    if room_number != room_data['room_number']:
        existing_room = c.execute('''
            SELECT id FROM rooms 
            WHERE property_id = ? AND room_number = ? AND id != ?
        ''', (room_data['property_id'], room_number, room_id)).fetchone()
        
        if existing_room:
            conn.close()
            flash('Room number already exists for this property', 'danger')
            return redirect(url_for('view_rooms', property_id=room_data['property_id']))
    
    # Handle room media upload if provided
    media_url = room_data['media_url']  # Keep existing media URL by default
    if room_media and room_media.filename:
        try:
            # Create the folder structure in Google Drive
            inventory_folder = find_or_create_folder('inventory')
            videos_folder = find_or_create_folder('videos', parent_folder_id=inventory_folder)
            building_folder = find_or_create_folder(room_data['property_name'], parent_folder_id=videos_folder)
            
            # Save file temporarily
            temp_filename = f"temp_room_media_{room_id}_{int(time.time())}"
            temp_path = os.path.join(UPLOADS_DIR, temp_filename)
            room_media.save(temp_path)
            
            # Determine file extension and mime type
            file_ext = os.path.splitext(room_media.filename)[1].lower()
            mime_type = 'video/mp4' if file_ext == '.mp4' else 'image/jpeg'
            
            # Upload to Google Drive
            drive_file_id = upload_file(
                temp_path,
                f"room_{room_number}{file_ext}",
                mime_type,
                folder_id=building_folder
            )
            
            # Get the web view URL
            media_url = get_file_url(drive_file_id)
            
            # Clean up temporary file
            try:
                os.remove(temp_path)
            except:
                pass
                
        except Exception as e:
            print("Error in media upload process:", str(e))
            flash('Error uploading room media: ' + str(e), 'warning')
            # Clean up temp file if it exists
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
    
    # Update room data
    c.execute('''
        UPDATE rooms 
        SET room_number = ?, room_config_id = ?, media_url = ?
        WHERE id = ?
    ''', (room_number, room_config_id, media_url, room_id))
    
    conn.commit()
    conn.close()
    
    flash('Room updated successfully!', 'success')
    return redirect(url_for('view_rooms', property_id=room_data['property_id']))

@app.route('/tenant/login', methods=['GET', 'POST'])
def tenant_login():
    if request.method == 'POST':
        email = request.form.get('email')
        phone = request.form.get('phone')
        
        conn = get_db()
        c = conn.cursor()
        
        # Verify tenant credentials
        tenant = c.execute('''
            SELECT t.*, p.name as property_name, r.room_number, rc.room_type, rc.rent, 
                   rc.electricity_charge, rc.water_charge
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            JOIN rooms r ON t.room_id = r.id
            JOIN room_configurations rc ON r.room_config_id = rc.id
            WHERE t.email = ? AND t.phone_number = ?
        ''', (email, phone)).fetchone()
        
        if tenant:
            # Store tenant info in session
            session['tenant_id'] = tenant[0]
            session['tenant_name'] = tenant[1]
            flash('Welcome back!', 'success')
            return redirect(url_for('tenant_portal'))
        else:
            flash('Invalid credentials', 'danger')
        
        conn.close()
    
    return render_template('tenant_login.html')

@app.route('/tenant/logout')
def tenant_logout():
    session.pop('tenant_id', None)
    session.pop('tenant_name', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('tenant_login'))

@app.route('/tenant/portal')
def tenant_portal():
    if 'tenant_id' not in session:
        flash('Please login to access the portal', 'warning')
        return redirect(url_for('tenant_login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get tenant details
    tenant = c.execute('''
        SELECT t.*, p.name as property_name, r.room_number, rc.room_type, rc.rent, 
               rc.electricity_charge, rc.water_charge
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN rooms r ON t.room_id = r.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE t.id = ?
    ''', (session['tenant_id'],)).fetchone()
    
    if not tenant:
        conn.close()
        flash('Tenant not found', 'danger')
        return redirect(url_for('tenant_login'))
    
    # Get current month's bill status
    current_date = datetime.now()
    current_month = current_date.month
    current_year = current_date.year
    
    # Calculate total amount using named columns
    total_amount = float(tenant['rent']) + float(tenant['electricity_charge']) + float(tenant['water_charge'])
    
    # Check if current month's bill is paid
    current_month_payment = c.execute('''
        SELECT COALESCE(SUM(amount), 0)
        FROM bill_payments
        WHERE tenant_id = ? 
        AND strftime('%m', payment_date) = ? 
        AND strftime('%Y', payment_date) = ?
    ''', (session['tenant_id'], str(current_month).zfill(2), str(current_year))).fetchone()[0]
    
    is_paid = float(current_month_payment) >= total_amount
    
    # Get recent payments
    recent_payments = c.execute('''
        SELECT payment_date, amount, payment_mode
        FROM bill_payments
        WHERE tenant_id = ?
        ORDER BY payment_date DESC
        LIMIT 5
    ''', (session['tenant_id'],)).fetchall()
    
    # Get previous bills
    previous_bills = []
    for i in range(1, 7):  # Last 6 months
        month_date = current_date - timedelta(days=30*i)
        month = month_date.month
        year = month_date.year
        
        payment = c.execute('''
            SELECT COALESCE(SUM(amount), 0)
            FROM bill_payments
            WHERE tenant_id = ? 
            AND strftime('%m', payment_date) = ? 
            AND strftime('%Y', payment_date) = ?
        ''', (session['tenant_id'], str(month).zfill(2), str(year))).fetchone()[0]
        
        previous_bills.append({
            'month': month_date.strftime('%B %Y'),
            'rent': float(tenant['rent']),
            'electricity': float(tenant['electricity_charge']),
            'water': float(tenant['water_charge']),
            'total': total_amount,
            'is_paid': float(payment) >= total_amount
        })
    
    # Format tenant data
    formatted_tenant = {
        'name': tenant['name'],
        'property_name': tenant['property_name'],
        'room_number': tenant['room_number'],
        'rent': float(tenant['rent']),
        'electricity_charge': float(tenant['electricity_charge']),
        'water_charge': float(tenant['water_charge']),
        'total_amount': total_amount,
        'due_date': (current_date.replace(day=1) + timedelta(days=4)).strftime('%Y-%m-%d'),
        'is_paid': is_paid
    }
    
    # Format recent payments
    formatted_payments = []
    for payment in recent_payments:
        formatted_payments.append({
            'payment_date': payment[0],
            'amount': float(payment[1]),
            'payment_mode': payment[2]
        })
    
    conn.close()
    
    return render_template('tenant_portal.html',
                         tenant=formatted_tenant,
                         recent_payments=formatted_payments,
                         previous_bills=previous_bills)

@app.route('/move-out-tenant/<int:tenant_id>', methods=['POST'])
def move_out_tenant(tenant_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    move_out_date = request.form.get('move_out_date')
    
    conn = get_db()
    c = conn.cursor()
    
    # Verify tenant ownership and get room_id
    tenant = c.execute('''
        SELECT t.*, p.user_id as property_owner_id 
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        WHERE t.id = ?
    ''', (tenant_id,)).fetchone()
    
    if not tenant or tenant['property_owner_id'] != session['user_id']:
        conn.close()
        flash('Tenant not found or unauthorized', 'danger')
        return redirect(url_for('list_tenants'))
    
    try:
        c.execute('BEGIN TRANSACTION')
        
        # Update tenant's move out date
        c.execute('UPDATE tenants SET move_out_date = ? WHERE id = ?', 
                 (move_out_date, tenant_id))
        
        # Mark room as available
        c.execute('UPDATE rooms SET is_available = TRUE WHERE id = ?', 
                 (tenant['room_id'],))
        
        c.execute('COMMIT')
        flash('Tenant has been marked as moved out successfully!', 'success')
    except Exception as e:
        c.execute('ROLLBACK')
        flash(f'Error updating tenant: {str(e)}', 'danger')
    finally:
        conn.close()
    
    return redirect(url_for('list_tenants'))