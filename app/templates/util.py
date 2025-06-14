import sqlite3
import os
from datetime import datetime, timedelta

# Get all tenants with their bills
def get_db():
    # Get the absolute path to the project root directory
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    db_path = os.path.join(project_root, 'rentease.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_tenants_with_bills(user_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            p.name as property_name,
            t.name as tenant_name,
            r.room_number,
            rc.rent as rent_amount,
            COALESCE(SUM(CASE 
                WHEN bp.payment_mode != 'penalty' 
                AND strftime('%Y-%m', bp.payment_date) = strftime('%Y-%m', 'now', 'localtime')
                    THEN bp.amount 
                    ELSE 0 
            END), 0) as paid_amount,
            COALESCE((
                SELECT er.total_cost FROM electricity_readings er
                WHERE er.property_id = t.property_id
                    AND er.room_id = t.room_id
                    AND strftime('%Y-%m', er.reading_date) = strftime('%Y-%m', 'now', 'localtime')
                ORDER BY er.reading_date DESC LIMIT 1
            ), rc.electricity_charge) as electricity_rate,
            rc.water_charge as water_rate,
            t.id as tenant_id,
            t.move_in_date,
            CASE 
                WHEN t.move_in_date <= date('now', 'localtime') THEN date('now', 'localtime', 'start of month', '+4 days')
                ELSE date(t.move_in_date, 'start of month', '+4 days')
            END as due_date,
            p.id as property_id,
            (rc.rent + 
                COALESCE((
                    SELECT er.total_cost FROM electricity_readings er
                    WHERE er.property_id = t.property_id
                        AND er.room_id = t.room_id
                        AND strftime('%Y-%m', er.reading_date) = strftime('%Y-%m', 'now', 'localtime')
                    ORDER BY er.reading_date DESC LIMIT 1
                ), rc.electricity_charge)
                + rc.water_charge) as total_amount,
            CASE 
                WHEN EXISTS (
                    SELECT 1 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', 'localtime', 'start of month', '-1 month'))
                ) THEN COALESCE((
                    SELECT pending_amount 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', 'localtime', 'start of month', '-1 month'))
                    ORDER BY id DESC LIMIT 1
                ), 0)
                ELSE 0 
            END as prev_month_pending,
            ((rc.rent + 
                COALESCE((
                    SELECT er.total_cost FROM electricity_readings er
                    WHERE er.property_id = t.property_id
                        AND er.room_id = t.room_id
                        AND strftime('%Y-%m', er.reading_date) = strftime('%Y-%m', 'now', 'localtime')
                    ORDER BY er.reading_date DESC LIMIT 1
                ), rc.electricity_charge)
                + rc.water_charge) + 
            CASE 
                WHEN EXISTS (
                    SELECT 1 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', 'localtime', 'start of month', '-1 month'))
                ) THEN COALESCE((
                    SELECT pending_amount 
                    FROM bill_payments 
                    WHERE tenant_id = t.id 
                    AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', date('now', 'localtime', 'start of month', '-1 month'))
                    ORDER BY payment_date DESC, id DESC LIMIT 1
                ), 0)
                ELSE 0 
            END - COALESCE(SUM(CASE 
                WHEN strftime('%Y-%m', bp.payment_date) = strftime('%Y-%m', 'now', 'localtime')
                    THEN bp.amount 
                    ELSE 0 
            END), 0)) as total_pending
        FROM tenants t
        JOIN rooms r ON t.room_id = r.id
        JOIN properties p ON r.property_id = p.id
        JOIN room_configurations rc ON r.room_config_id = rc.id
        LEFT JOIN bill_payments bp ON t.id = bp.tenant_id
        WHERE p.user_id = ? AND t.move_out_date IS NULL
        GROUP BY t.id
        ORDER BY r.room_number
    ''', (user_id,))
    
    tenants = cursor.fetchall()
    conn.close()
    
    # Calculate total statistics
    total_stats = {
        'total': sum(tenant['total_amount'] + tenant['prev_month_pending'] for tenant in tenants),
        'paid': sum(tenant['paid_amount'] for tenant in tenants),
        'pending': sum(tenant['total_pending'] for tenant in tenants)
    }
    
    # Calculate building-wise statistics
    building_stats = {}
    for tenant in tenants:
        property_name = tenant['property_name']
        if property_name not in building_stats:
            building_stats[property_name] = {
                'total': 0,
                'paid': 0,
                'pending': 0,
                'tenant_count': 0
            }
        
        building_stats[property_name]['total'] += tenant['total_amount'] + tenant['prev_month_pending']
        building_stats[property_name]['paid'] += tenant['paid_amount']
        building_stats[property_name]['pending'] += tenant['total_pending']
        building_stats[property_name]['tenant_count'] += 1
    
    return tenants, total_stats, building_stats

def get_monthly_collection_data(user_id):
    conn = get_db()
    c = conn.cursor()
    # Get current month and year
    current_date = datetime.now()
    monthly_labels = []
    monthly_collections = []
    monthly_expected = []
    
    for i in range(6):
        month_date = current_date - timedelta(days=30*i)
        year = month_date.year
        month = month_date.month
        month_name = month_date.strftime('%b %Y')
        monthly_labels.insert(0, month_name)
        
        # Get collection data for this month
        collection_data = c.execute('''
            SELECT COALESCE(SUM(bp.amount), 0) as collection
            FROM bill_payments bp
            JOIN tenants t ON bp.tenant_id = t.id
            JOIN rooms r ON t.room_id = r.id
            JOIN properties p ON r.property_id = p.id
            WHERE p.user_id = ?
            AND strftime('%m', bp.payment_date) = ? 
            AND strftime('%Y', bp.payment_date) = ?
            AND bp.payment_mode != 'penalty'
        ''', (user_id, str(month).zfill(2), str(year))).fetchone()
        
        monthly_collections.insert(0, collection_data['collection'])
        
        # Get expected amount for this month
        expected_data = c.execute('''
            SELECT COALESCE(SUM(
                rc.rent + 
                COALESCE((
                    SELECT er.total_cost 
                    FROM electricity_readings er
                    WHERE er.property_id = t.property_id
                    AND er.room_id = t.room_id
                    AND strftime('%Y-%m', er.reading_date) = ?
                    ORDER BY er.reading_date DESC LIMIT 1
                ), rc.electricity_charge) + 
                rc.water_charge
            ), 0) as expected
            FROM tenants t
            JOIN rooms r ON t.room_id = r.id
            JOIN properties p ON r.property_id = p.id
            JOIN room_configurations rc ON r.room_config_id = rc.id
            WHERE p.user_id = ? 
            AND t.move_out_date IS NULL
            AND t.move_in_date <= date(?, 'start of month', '+1 month', '-1 day')
        ''', (f"{year}-{str(month).zfill(2)}", user_id, f"{year}-{str(month).zfill(2)}-01")).fetchone()
        
        monthly_expected.insert(0, expected_data['expected'])
    conn.close()
    return monthly_labels, monthly_collections, monthly_expected


# Example usage:
# tenants, stats, building_stats = get_tenants_with_bills(4)
# print("Building-wise stats:", building_stats)
# print("Total stats:", stats)

monthly_labels, monthly_collections, monthly_expected = get_monthly_collection_data(4)
print("Monthly labels:", monthly_labels)
print("Monthly collections:", monthly_collections)
print("Monthly expected:", monthly_expected)