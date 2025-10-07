from flask import Flask, render_template, redirect, url_for, request, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import sqlite3
import os

app = Flask(__name__, template_folder='template')
app.secret_key = 'secretkey'  # Change this in production

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

DB_NAME = 'database.db'

class User(UserMixin):
    def __init__(self, id_, username, role):
        self.id = id_
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    user = conn.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if user:
        return User(*user)
    return None

def setup():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT,
        role TEXT,
        pay_rate REAL
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        pay_rate REAL
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        customer_id INTEGER,
        hours REAL,
        description TEXT,
        work_date TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )''')
    # Best-effort migration if table exists without new columns
    try:
        cursor.execute("ALTER TABLE logs ADD COLUMN description TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE logs ADD COLUMN work_date TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()
setup()

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DB_NAME)
        user = conn.execute("SELECT id, username, role FROM users WHERE username=? AND password=?",
                            (username, password)).fetchone()
        conn.close()
        if user:
            login_user(User(*user))
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect(DB_NAME)
    if current_user.role == 'manager':
        logs = conn.execute('''SELECT u.username, c.name, l.hours, u.pay_rate, c.pay_rate, IFNULL(l.description, ''), IFNULL(l.work_date, '')
                               FROM logs l
                               JOIN users u ON l.user_id = u.id
                               JOIN customers c ON l.customer_id = c.id
                               ORDER BY COALESCE(l.work_date, ''), u.username, c.name''').fetchall()
        conn.close()
        total_out = 0.0
        total_in = 0.0
        net_logs = []
        # Aggregations
        hours_by_worker = {}
        hours_by_customer = {}

        for log in logs:
            username, customer_name, hours, worker_monthly, customer_monthly, description, work_date = log
            hourly_worker = (worker_monthly or 0) / 160.0
            hourly_customer = (customer_monthly or 0) / 160.0
            cost = (hours or 0) * hourly_worker
            profit = (hours or 0) * (hourly_customer - hourly_worker)
            total_out += cost
            total_in += profit
            net_logs.append((username, customer_name, hours, cost, profit, description, work_date))

            hours_by_worker[username] = hours_by_worker.get(username, 0.0) + (hours or 0)
            hours_by_customer[customer_name] = hours_by_customer.get(customer_name, 0.0) + (hours or 0)

        return render_template(
            'manager_dashboard.html',
            logs=net_logs,
            total_out=total_out,
            total_in=total_in,
            hours_by_worker=hours_by_worker,
            hours_by_customer=hours_by_customer,
        )
    else:
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        # show worker's recent logs
        recent_logs = conn.execute('''SELECT c.name, l.hours, IFNULL(l.description, ''), IFNULL(l.work_date, '')
                                      FROM logs l
                                      JOIN customers c ON l.customer_id = c.id
                                      WHERE l.user_id = ?
                                      ORDER BY COALESCE(l.work_date, '') DESC, l.id DESC
                                      LIMIT 20''', (current_user.id,)).fetchall()
        conn.close()
        return render_template('worker_dashboard.html', customers=customers, recent_logs=recent_logs)

@app.route('/submit_hours', methods=['POST'])
@login_required
def submit_hours():
    if current_user.role not in ('worker', 'manager'):
        return 'Unauthorized', 403
    customer_id = request.form['customer_id']
    hours = float(request.form['hours'])
    description = request.form.get('description', '').strip()
    work_date = request.form.get('work_date', '').strip()
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO logs (user_id, customer_id, hours, description, work_date) VALUES (?, ?, ?, ?, ?)",
                 (current_user.id, customer_id, hours, description, work_date))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/work')
@login_required
def work_form():
    # A worker-style page available to both workers and managers for logging their own work
    conn = sqlite3.connect(DB_NAME)
    customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    recent_logs = conn.execute('''SELECT c.name, l.hours, IFNULL(l.description, ''), IFNULL(l.work_date, '')
                                  FROM logs l
                                  JOIN customers c ON l.customer_id = c.id
                                  WHERE l.user_id = ?
                                  ORDER BY COALESCE(l.work_date, '') DESC, l.id DESC
                                  LIMIT 20''', (current_user.id,)).fetchall()
    conn.close()
    return render_template('worker_dashboard.html', customers=customers, recent_logs=recent_logs)

@app.route('/logs')
@login_required
def list_logs():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Manager can see all or filter by user_id; workers see only their own
    user_filter = request.args.get('user_id') if current_user.role == 'manager' else str(current_user.id)
    if user_filter:
        rows = cursor.execute('''SELECT l.id, IFNULL(l.work_date,''), c.name, l.hours, IFNULL(l.description,''), u.username
                                 FROM logs l
                                 JOIN customers c ON l.customer_id=c.id
                                 JOIN users u ON l.user_id=u.id
                                 WHERE l.user_id=?
                                 ORDER BY COALESCE(l.work_date, '' ) DESC, l.id DESC''', (user_filter,)).fetchall()
    else:
        rows = cursor.execute('''SELECT l.id, IFNULL(l.work_date,''), c.name, l.hours, IFNULL(l.description,''), u.username
                                 FROM logs l
                                 JOIN customers c ON l.customer_id=c.id
                                 JOIN users u ON l.user_id=u.id
                                 ORDER BY COALESCE(l.work_date, '' ) DESC, l.id DESC''').fetchall()
    # Needed for edit form selects
    customers = cursor.execute('SELECT id, name FROM customers ORDER BY name').fetchall()
    users = cursor.execute('SELECT id, username FROM users ORDER BY username').fetchall()
    conn.close()
    return render_template('logs.html', rows=rows, customers=customers, users=users, user_filter=user_filter)

@app.route('/logs/delete/<int:log_id>', methods=['POST'])
@login_required
def delete_log(log_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Ensure permissions: workers can delete only their own
    if current_user.role == 'worker':
        cursor.execute('DELETE FROM logs WHERE id=? AND user_id=?', (log_id, current_user.id))
    else:
        cursor.execute('DELETE FROM logs WHERE id=?', (log_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('list_logs'))

@app.route('/logs/edit/<int:log_id>', methods=['POST'])
@login_required
def edit_log(log_id):
    customer_id = request.form.get('customer_id')
    hours = request.form.get('hours')
    work_date = request.form.get('work_date')
    description = request.form.get('description', '')
    assign_user_id = request.form.get('user_id') if current_user.role == 'manager' else str(current_user.id)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Permissions: workers can modify only their own logs
    if current_user.role == 'worker':
        cursor.execute('''UPDATE logs SET customer_id=?, hours=?, work_date=?, description=?
                          WHERE id=? AND user_id=?''', (customer_id, float(hours), work_date, description, log_id, current_user.id))
    else:
        cursor.execute('''UPDATE logs SET customer_id=?, hours=?, work_date=?, description=?, user_id=?
                          WHERE id=?''', (customer_id, float(hours), work_date, description, assign_user_id, log_id))
    conn.commit()
    conn.close()
    return redirect(url_for('list_logs'))

@app.route('/rates', methods=['GET', 'POST'])
@login_required
def rates():
    if current_user.role != 'manager':
        return 'Unauthorized', 403
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action', 'update_rates')
        if action == 'update_rates':
            # Update users and customers pay rates
            for key, value in request.form.items():
                if key.startswith('user_'):
                    try:
                        user_id = int(key.split('_')[1])
                        rate = float(value) if value.strip() != '' else None
                    except Exception:
                        continue
                    cursor.execute("UPDATE users SET pay_rate=? WHERE id=?", (rate, user_id))
                if key.startswith('customer_'):
                    try:
                        customer_id = int(key.split('_')[1])
                        rate = float(value) if value.strip() != '' else None
                    except Exception:
                        continue
                    cursor.execute("UPDATE customers SET pay_rate=? WHERE id=?", (rate, customer_id))
            conn.commit()
        elif action == 'add_user':
            username = request.form.get('new_user_name', '').strip()
            pay = request.form.get('new_user_pay', '').strip()
            if username:
                # Avoid duplicates by name
                exists = cursor.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if not exists:
                    try:
                        rate = float(pay) if pay != '' else None
                    except Exception:
                        rate = None
                    cursor.execute(
                        "INSERT INTO users (username, password, role, pay_rate) VALUES (?, ?, ?, ?)",
                        (username, '1234', 'worker', rate),
                    )
                    conn.commit()
        elif action == 'add_customer':
            name = request.form.get('new_customer_name', '').strip()
            fee = request.form.get('new_customer_fee', '').strip()
            if name:
                exists = cursor.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()
                if not exists:
                    try:
                        rate = float(fee) if fee != '' else None
                    except Exception:
                        rate = None
                    cursor.execute(
                        "INSERT INTO customers (name, pay_rate) VALUES (?, ?)",
                        (name, rate),
                    )
                    conn.commit()
        # After post actions, redirect to GET to avoid resubmission
        conn.close()
        return redirect(url_for('rates'))

    users = cursor.execute("SELECT id, username, role, pay_rate FROM users ORDER BY role DESC, username").fetchall()
    customers = cursor.execute("SELECT id, name, pay_rate FROM customers ORDER BY name").fetchall()
    conn.close()
    return render_template('manager_rates.html', users=users, customers=customers)

@app.route('/report')
@login_required
def report():
    if current_user.role != 'manager':
        return 'Unauthorized', 403
    # month parameter format: YYYY-MM
    month = request.args.get('month')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = '''
        SELECT c.name,
               SUM(IFNULL(l.hours, 0)) as total_hours,
               AVG(IFNULL(u.pay_rate, 0)) as avg_worker_monthly,
               AVG(IFNULL(cu.pay_rate, 0)) as avg_customer_monthly
        FROM logs l
        JOIN users u ON l.user_id = u.id
        JOIN customers c ON l.customer_id = c.id
        JOIN customers cu ON cu.id = l.customer_id
        WHERE (? IS NULL OR substr(IFNULL(l.work_date, ''), 1, 7) = ?)
        GROUP BY c.name
        ORDER BY c.name
    '''
    params = (month, month)
    rows = cursor.execute(query, params).fetchall()
    conn.close()

    # Compute totals using 160 hours standard
    report_rows = []
    total_hours = 0.0
    total_cost = 0.0
    total_revenue = 0.0
    for name, hours, worker_monthly, customer_monthly in rows:
        hourly_worker = (worker_monthly or 0) / 160.0
        hourly_customer = (customer_monthly or 0) / 160.0
        cost = (hours or 0) * hourly_worker
        revenue = (hours or 0) * (hourly_customer)
        profit = revenue - cost
        report_rows.append((name, hours or 0, cost, revenue, profit))
        total_hours += hours or 0
        total_cost += cost
        total_revenue += revenue

    return render_template(
        'manager_report.html',
        rows=report_rows,
        month=month,
        total_hours=total_hours,
        total_cost=total_cost,
        total_revenue=total_revenue,
        total_profit=total_revenue - total_cost,
    )

if __name__ == '__main__':
    app.run(debug=True)
