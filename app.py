from flask import Flask, render_template, redirect, url_for, request, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import sqlite3
import os

app = Flask(__name__)
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

@app.before_first_request
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
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )''')
    conn.commit()
    conn.close()

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
        logs = conn.execute('''SELECT u.username, c.name, l.hours, u.pay_rate, c.pay_rate FROM logs l
                               JOIN users u ON l.user_id = u.id
                               JOIN customers c ON l.customer_id = c.id''').fetchall()
        conn.close()
        total_out = sum(l[2] * l[3] for l in logs)
        total_in = sum(l[2] * l[4] for l in logs)
        return render_template('manager_dashboard.html', logs=logs, total_out=total_out, total_in=total_in)
    else:
        customers = conn.execute("SELECT id, name FROM customers").fetchall()
        conn.close()
        return render_template('worker_dashboard.html', customers=customers)

@app.route('/submit_hours', methods=['POST'])
@login_required
def submit_hours():
    if current_user.role != 'worker':
        return 'Unauthorized', 403
    customer_id = request.form['customer_id']
    hours = float(request.form['hours'])
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO logs (user_id, customer_id, hours) VALUES (?, ?, ?)",
                 (current_user.id, customer_id, hours))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
