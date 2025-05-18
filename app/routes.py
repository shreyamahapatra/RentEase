from flask import render_template, request, redirect, url_for, flash, jsonify, session, send_file
from app import app  # or from . import app if using a package
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import io


# Database initialization
def init_db():
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # # Drop existing tables in reverse order of dependencies
    # c.execute('DROP TABLE IF EXISTS bill_payments')
    # c.execute('DROP TABLE IF EXISTS tenants')
    # c.execute('DROP TABLE IF EXISTS room_configurations')
    # c.execute('DROP TABLE IF EXISTS properties')
    # c.execute('DROP TABLE IF EXISTS users')
    
    # Create tables with new schema
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL)''')
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
                  FOREIGN KEY (property_id) REFERENCES properties (id),
                  FOREIGN KEY (room_config_id) REFERENCES room_configurations (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS tenants
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  property_id INTEGER NOT NULL,
                  room_id INTEGER NOT NULL,
                  phone_number TEXT NOT NULL,
                  move_in_date DATE NOT NULL,
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
    
    conn.commit()
    conn.close()

# Initialize database when app starts
init_db()

@app.route('/')
@app.route('/index')
def index():
    if 'user_id' in session:
        conn = sqlite3.connect('rentease.db')
        c = conn.cursor()
        properties = c.execute('SELECT * FROM properties WHERE user_id = ?', (session['user_id'],)).fetchall()
        
        # Get tenants data with payment information
        current_month = datetime.now().month
        current_year = datetime.now().year
        
        # Calculate previous month and year
        if current_month == 1:
            prev_month = 12
            prev_year = current_year - 1
        else:
            prev_month = current_month - 1
            prev_year = current_year
        
        tenants = c.execute('''
            SELECT t.*, p.name as property_name, r.room_number, rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
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
                   COUNT(CASE 
                        WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                        THEN 1 
                   END) as has_prev_month_entry
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
              str(prev_month).zfill(2), str(prev_year),
              str(current_month).zfill(2), str(prev_month).zfill(2),
              str(current_year), str(prev_year),
              session['user_id'])).fetchall()
        
        # Format tenants data and calculate total pending
        formatted_tenants = []
        total_pending = 0
        current_date = datetime.now()
        
        for tenant in tenants:
            # Calculate bill amount
            rent = float(tenant[10])  # rent from room_configurations
            electricity = float(tenant[11])  # electricity_charge
            water = float(tenant[12])  # water_charge
            total_amount = rent + electricity + water
            
            # Get move-in date
            move_in_date = datetime.strptime(tenant[5], '%Y-%m-%d')
            
            # Calculate due date (same day of month as move-in)
            due_date = current_date.replace(day=move_in_date.day)
            if due_date < current_date:
                due_date = (due_date.replace(day=1) + timedelta(days=32)).replace(day=1)
            
            # Calculate pending amount
            current_month_paid = float(tenant[13])  # current_month_paid
            prev_month_paid = float(tenant[14])  # prev_month_paid
            prev_month_pending = total_amount - prev_month_paid if prev_month_paid < total_amount else 0
            pending_amount = total_amount + prev_month_pending - current_month_paid
            
            # Only add tenants with pending amounts
            if pending_amount > 0:
                total_pending += pending_amount
                formatted_tenants.append({
                    'id': tenant[0],
                    'name': tenant[1],
                    'property_id': tenant[2],
                    'room_id': tenant[3],
                    'phone_number': tenant[4],
                    'move_in_date': tenant[5],
                    'police_verification': tenant[6],
                    'property_name': tenant[7],
                    'room_number': tenant[8],
                    'room_type': tenant[9],
                    'rent': tenant[10],
                    'electricity': tenant[11],
                    'water': tenant[12],
                    'current_month_paid': current_month_paid,
                    'prev_month_pending': prev_month_pending,
                    'total_amount': total_amount,
                    'due_date': due_date.strftime('%Y-%m-%d'),
                    'pending_amount': pending_amount
                })
        
        conn.close()
        return render_template('index.html', 
                             properties=properties, 
                             tenants=formatted_tenants,
                             total_pending=total_pending)
    return render_template('index.html', tenants=[], total_pending=0)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect('rentease.db')
        c = conn.cursor()
        user = c.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
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
        
        if not username or not password:
            flash('Please fill in all fields', 'danger')
            return redirect(url_for('register'))
        
        # hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        try:
            conn = sqlite3.connect('rentease.db')
            c = conn.cursor()
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, password))
            conn.commit()
            conn.close()
            flash('Registration successful! Please login.', 'success')
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
        
        conn = sqlite3.connect('rentease.db')
        c = conn.cursor()
        c.execute('INSERT INTO properties (name, address, user_id) VALUES (?, ?, ?)',
                 (name, address, user_id))
        property_id = c.lastrowid
        
        # Save room configurations
        room_types = ['one_room', 'two_room']
        for room_type in room_types:
            room_count = request.form.get(f'{room_type}_count')
            rent = request.form.get(f'{room_type}_rent')
            electricity_charge = request.form.get(f'{room_type}_electricity')
            water_charge = request.form.get(f'{room_type}_water')
            security_deposit = request.form.get(f'{room_type}_security')
            
            c.execute('''INSERT INTO room_configurations 
                         (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit) 
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (property_id, room_type, room_count, rent, electricity_charge, water_charge, security_deposit))
        
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
        move_in_date = request.form.get('move_in_date')
        police_verification = request.files.get('police_verification')
        
        print("Raw form data:")
        print("Property ID:", property_id, type(property_id))
        print("Room ID:", room_id, type(room_id))
        print("Tenant Name:", tenant_name, type(tenant_name))
        print("Phone Number:", phone_number, type(phone_number))
        print("Move-in Date:", move_in_date, type(move_in_date))
        
        # Save police verification file if provided
        filename = None
        if police_verification:
            filename = f"police_verification_{property_id}_{room_id}_{int(time.time())}.pdf"
            police_verification.save(os.path.join('app/static/uploads', filename))
        
        conn = sqlite3.connect('rentease.db')
        c = conn.cursor()
        
        # Check if room is already occupied
        existing_tenant = c.execute('''
            SELECT id FROM tenants WHERE room_id = ?
        ''', (room_id,)).fetchone()
        
        if existing_tenant:
            conn.close()
            flash('This room is already occupied!', 'danger')
            return redirect(url_for('add_tenant'))
        
        # Insert tenant data with explicit parameter names
        params = (
            tenant_name,      # name
            int(property_id), # property_id
            int(room_id),     # room_id
            phone_number,     # phone_number
            move_in_date,     # move_in_date
            filename         # police_verification
        )
        print("Insert parameters:", params)
        
        c.execute('''INSERT INTO tenants 
                    (name, property_id, room_id, phone_number, move_in_date, police_verification) 
                    VALUES (?, ?, ?, ?, ?, ?)''', params)
        
        conn.commit()
        conn.close()
        
        flash('Tenant added successfully!', 'success')
        return redirect(url_for('list_tenants'))

    # Get all properties for the logged-in user with their rooms
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # Get all properties for the logged-in user with their rooms
    properties = c.execute('''
        SELECT p.id, p.name, p.address, 
               r.id as room_id, r.room_number,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit
        FROM properties p
        LEFT JOIN rooms r ON p.id = r.property_id
        LEFT JOIN room_configurations rc ON r.room_config_id = rc.id
        WHERE p.user_id = ?
        AND (r.id IS NULL OR r.id NOT IN (SELECT room_id FROM tenants))
        ORDER BY p.id, r.room_number
    ''', (session['user_id'],)).fetchall()
    
    print("Properties for add tenant:", properties)
    
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
    
    conn = sqlite3.connect('rentease.db')
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
    
    # Format tenants data
    formatted_tenants = []
    for tenant in tenants:
        formatted_tenants.append({
            'id': tenant[0],
            'name': tenant[1],
            'property_id': tenant[2],
            'room_id': tenant[3],
            'phone_number': tenant[4],
            'move_in_date': tenant[5],
            'police_verification': tenant[6],
            'property_name': tenant[7],
            'room_type': tenant[8],
            'rent': tenant[9],
            'electricity_charge': tenant[10],
            'water_charge': tenant[11],
            'room_number': tenant[12]
        })
    print("Shreya Formatted Properties:", formatted_properties)
    print("Shreya Formatted Tenants:", formatted_tenants)
    conn.close()
    return render_template('list_tenants.html', 
                         properties=formatted_properties,
                         tenants=formatted_tenants)

@app.route('/delete-tenant/<int:tenant_id>', methods=['POST'])
def delete_tenant(tenant_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # Get tenant's police verification file
    tenant = c.execute('''
        SELECT t.police_verification 
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if tenant:
        # Delete the file if it exists
        if tenant[0]:
            try:
                os.remove(os.path.join('app/static/uploads', tenant[0]))
            except:
                pass  # Ignore if file doesn't exist
        
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
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # Get all properties for the logged-in user with their room configurations
    properties = c.execute('''
        SELECT p.id, p.name, p.address, 
               rc.id as room_config_id, rc.room_type, rc.room_count, 
               rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit
        FROM properties p
        LEFT JOIN room_configurations rc ON p.id = rc.property_id
        WHERE p.user_id = ?
    ''', (session['user_id'],)).fetchall()
    
    # Format the properties data
    formatted_properties = []
    current_property = None
    
    for row in properties:
        if current_property is None or current_property['id'] != row[0]:
            if current_property is not None:
                formatted_properties.append(current_property)
            
            current_property = {
                'id': row[0],
                'name': row[1],
                'address': row[2],
                'rooms': []
            }
        
        if row[3]:  # if room_config_id exists
            current_property['rooms'].append({
                'id': row[3],
                'room_type': row[4],
                'room_count': row[5],
                'rent': row[6],
                'electricity_charge': row[7],
                'water_charge': row[8],
                'security_deposit': row[9]
            })
    
    if current_property is not None:
        formatted_properties.append(current_property)

    print("Shreya Formatted Properties:", formatted_properties)    
    conn.close()
    return render_template('my_properties.html', properties=formatted_properties)

@app.route('/edit-property/<int:property_id>', methods=['GET', 'POST'])
def edit_property(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    if request.method == 'POST':
        # Update property information
        name = request.form.get('name')
        address = request.form.get('address')
        
        c.execute('''UPDATE properties 
                    SET name = ?, address = ? 
                    WHERE id = ? AND user_id = ?''',
                 (name, address, property_id, session['user_id']))
        
        # Update room configurations
        room_types = ['one_room', 'two_room']
        for room_type in room_types:
            room_count = request.form.get(f'{room_type}_count')
            rent = request.form.get(f'{room_type}_rent')
            electricity_charge = request.form.get(f'{room_type}_electricity')
            water_charge = request.form.get(f'{room_type}_water')
            security_deposit = request.form.get(f'{room_type}_security')
            
            # Update existing room configuration
            c.execute('''UPDATE room_configurations 
                        SET room_count = ?, rent = ?, electricity_charge = ?, 
                            water_charge = ?, security_deposit = ?
                        WHERE property_id = ? AND room_type = ?''',
                     (room_count, rent, electricity_charge, water_charge, 
                      security_deposit, property_id, room_type))
        
        conn.commit()
        flash('Property updated successfully!', 'success')
        return redirect(url_for('my_properties'))
    
    # Get property and room configuration data
    property_data = c.execute('''
        SELECT * FROM properties 
        WHERE id = ? AND user_id = ?
    ''', (property_id, session['user_id'])).fetchone()
    
    if not property_data:
        conn.close()
        flash('Property not found', 'danger')
        return redirect(url_for('my_properties'))
    
    room_configs = c.execute('''
        SELECT * FROM room_configurations 
        WHERE property_id = ?
    ''', (property_id,)).fetchall()
    
    # Format room configurations into a dictionary
    room_data = {}
    for room in room_configs:
        room_data[room[2]] = {  # room[2] is room_type
            'room_count': room[3],
            'rent': room[4],
            'electricity_charge': room[5],
            'water_charge': room[6],
            'security_deposit': room[7]
        }
    
    conn.close()
    print("Property Data:", property_data)
    return render_template('edit_property.html', 
                         property=property_data,
                         room_data=room_data)

@app.route('/delete-property/<int:property_id>', methods=['POST'])
def delete_property(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
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
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    if request.method == 'POST':
        tenant_name = request.form.get('tenant_name')
        phone_number = request.form.get('phone_number')
        move_in_date = request.form.get('move_in_date')
        police_verification = request.files.get('police_verification')
        
        # Get current tenant data
        current_tenant = c.execute('''
            SELECT t.*, p.user_id 
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            WHERE t.id = ?
        ''', (tenant_id,)).fetchone()
        
        if not current_tenant or current_tenant[6] != session['user_id']:
            conn.close()
            flash('Tenant not found', 'danger')
            return redirect(url_for('list_tenants'))
        
        # Handle police verification file
        filename = current_tenant[5]  # Keep existing filename by default
        if police_verification:
            # Delete old file if exists
            if filename:
                try:
                    os.remove(os.path.join('app/static/uploads', filename))
                except:
                    pass
            
            # Save new file
            filename = f"police_verification_{current_tenant[2]}_{current_tenant[3]}_{int(time.time())}.pdf"
            police_verification.save(os.path.join('app/static/uploads', filename))
        
        # Update tenant data
        c.execute('''
            UPDATE tenants 
            SET name = ?, phone_number = ?, move_in_date = ?, police_verification = ?
            WHERE id = ?
        ''', (tenant_name, phone_number, move_in_date, filename, tenant_id))
        
        conn.commit()
        conn.close()
        
        flash('Tenant updated successfully!', 'success')
        return redirect(url_for('list_tenants'))
    
    # Get tenant data for editing
    tenant = c.execute('''
        SELECT t.*, p.name as property_name,
               rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
               (SELECT COUNT(*) FROM room_configurations rc2 
                WHERE rc2.property_id = p.id AND rc2.id <= rc.id) as room_number
        FROM tenants t
        JOIN properties p ON t.property_id = p.id
        JOIN room_configurations rc ON t.room_id = rc.id
        WHERE t.id = ? AND p.user_id = ?
    ''', (tenant_id, session['user_id'])).fetchone()
    
    if not tenant:
        conn.close()
        flash('Tenant not found', 'danger')
        return redirect(url_for('list_tenants'))
    
    formatted_tenant = {
        'id': tenant[0],
        'name': tenant[1],
        'phone_number': tenant[2],
        'property_id': tenant[3],
        'room_id': tenant[4],
        'move_in_date': tenant[5],
        'police_verification': tenant[6],
        'property_name': tenant[7],
        'room_type': tenant[8],
        'rent': tenant[9],
        'electricity_charge': tenant[10],
        'water_charge': tenant[11],
        'room_number': tenant[12]
    }
    
    conn.close()
    return render_template('edit_tenant.html', tenant=formatted_tenant)

@app.route('/add-room/<int:property_id>', methods=['POST'])
def add_room(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    # Verify property ownership
    conn = sqlite3.connect('rentease.db')
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

    print("Tarun Room Config ID:", room_config_id)
    print("Tarun Room Number:", room_number)
    
    # Get room configuration details
    room_config = c.execute('''
        SELECT room_count FROM room_configurations 
        WHERE id = ? AND property_id = ?
    ''', (room_config_id, property_id)).fetchone()
    
    if not room_config:
        conn.close()
        flash('Room configuration not found', 'danger')
        return redirect(url_for('my_properties'))
    
    # Check if room number already exists for this property
    existing_room = c.execute('''
        SELECT id FROM rooms 
        WHERE property_id = ? AND room_number = ?
    ''', (property_id, room_number)).fetchone()
    
    if existing_room:
        conn.close()
        flash('Room number already exists for this property', 'danger')
        return redirect(url_for('my_properties'))
    
    # Check if we've reached the room count limit for this configuration
    current_rooms = c.execute('''
        SELECT COUNT(*) FROM rooms 
        WHERE property_id = ? AND room_config_id = ?
    ''', (property_id, room_config_id)).fetchone()[0]
    
    if current_rooms >= room_config[0]:
        conn.close()
        flash('Maximum number of rooms reached for this configuration', 'danger')
        return redirect(url_for('my_properties'))
    
    # Insert new room
    c.execute('''INSERT INTO rooms 
                 (property_id, room_config_id, room_number) 
                 VALUES (?, ?, ?)''',
             (property_id, room_config_id, room_number))
    
    conn.commit()
    conn.close()

    print("Tarun Room Added:", room_number)
    
    flash('Room added successfully!', 'success')
    return redirect(url_for('my_properties'))

@app.route('/view-rooms/<int:property_id>')
def view_rooms(property_id):
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # Get property details
    property_data = c.execute('''
        SELECT id, name, address 
        FROM properties 
        WHERE id = ? AND user_id = ?
    ''', (property_id, session['user_id'])).fetchone()
    
    if not property_data:
        conn.close()
        flash('Property not found', 'danger')
        return redirect(url_for('my_properties'))
    
    # Get all rooms for this property with their configurations
    rooms = c.execute('''
        SELECT r.id, r.room_number, rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge, rc.security_deposit,
               CASE WHEN t.id IS NOT NULL THEN 1 ELSE 0 END as is_occupied
        FROM rooms r
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN tenants t ON r.id = t.room_id
        WHERE r.property_id = ?
        ORDER BY r.room_number
    ''', (property_id,)).fetchall()
    
    print(f"Property: {property_data}, Rooms: {rooms}")
    
    # Format the rooms data
    formatted_rooms = []
    for room in rooms:
        formatted_rooms.append({
            'id': room[0],
            'room_number': room[1],
            'room_type': room[2],
            'rent': room[3],
            'electricity_charge': room[4],
            'water_charge': room[5],
            'security_deposit': room[6],
            'is_occupied': bool(room[7])
        })
    
    conn.close()
    return render_template('view_rooms.html', 
                         property=property_data,
                         rooms=formatted_rooms)

@app.route('/bills')
def view_bills():
    if 'user_id' not in session:
        flash('Please login to access this page', 'warning')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
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

@app.route('/all-bills')
def all_bills():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
    c = conn.cursor()
    
    # Get all properties for the user
    properties = c.execute('''
        SELECT id, name FROM properties WHERE user_id = ?
    ''', (session['user_id'],)).fetchall()
    
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
    
    tenants = c.execute('''
        SELECT t.*, p.name as property_name, r.room_number, rc.room_type, rc.rent, rc.electricity_charge, rc.water_charge,
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
               COUNT(CASE 
                    WHEN strftime('%m', bp.payment_date) = ? AND strftime('%Y', bp.payment_date) = ? 
                    THEN 1 
               END) as has_prev_month_entry
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
          str(prev_month).zfill(2), str(prev_year),
          str(current_month).zfill(2), str(prev_month).zfill(2),
          str(current_year), str(prev_year),
          session['user_id'])).fetchall()
    
    formatted_tenants = []
    total_pending = 0
    
    for tenant in tenants:
        # Calculate total amount
        total_amount = float(tenant[10]) + float(tenant[11]) + float(tenant[12])  # rent + electricity + water
        current_month_paid = float(tenant[13])  # current month paid
        prev_month_paid = float(tenant[14])  # previous month paid
        has_prev_month_entry = int(tenant[15]) > 0  # check if there are previous month entries
        
        print(f"Debug - Tenant {tenant[1]}:")
        print(f"  Current Month Paid: {current_month_paid}")
        print(f"  Total Amount: {total_amount}")
        print(f"  Previous Month Paid: {prev_month_paid}")
        print(f"  Has Previous Month Entry: {has_prev_month_entry}")
        
        # Calculate previous month's pending amount only if there are previous month entries
        prev_month_pending = (total_amount - prev_month_paid) if has_prev_month_entry else 0
        
        # Total pending is previous month's pending plus current month's amount minus current month's paid
        pending_amount = prev_month_pending + (total_amount - current_month_paid)
        total_pending += pending_amount
        
        # Calculate due date (5th of next month)
        move_in_date = datetime.strptime(tenant[5], '%Y-%m-%d')
        due_date = datetime(current_year, current_month, 5)
        if move_in_date.day > 5:
            due_date = (due_date.replace(day=1) + timedelta(days=32)).replace(day=5)
        
        formatted_tenants.append({
            'id': tenant[0],
            'property_id': tenant[2],
            'property_name': tenant[7],
            'name': tenant[1],
            'room_number': tenant[8],
            'rent': tenant[10],
            'electricity': tenant[11],
            'water': tenant[12],
            'total_amount': total_amount,
            'current_month_paid': current_month_paid,
            'prev_month_pending': prev_month_pending,
            'pending_amount': pending_amount,
            'due_date': due_date.strftime('%Y-%m-%d')
        })
    
    conn.close()
    return render_template('all_bills.html', 
                         tenants=formatted_tenants, 
                         properties=properties,
                         total_pending=total_pending,
                         current_month=datetime.now().strftime('%B %Y'))

@app.route('/bill-payments/<int:tenant_id>')
def bill_payments(tenant_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('rentease.db')
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
    
    conn = sqlite3.connect('rentease.db')
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
    
    # Calculate new paid and pending amounts
    paid_amount = float(existing_payments) + float(amount)
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
    
    conn = sqlite3.connect('rentease.db')
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