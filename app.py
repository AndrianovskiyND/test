from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from models import Database, UserManager, TaskManager
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# Инициализация базы данных
db = Database()
user_manager = UserManager(db)
task_manager = TaskManager(db)

# Русские названия для отображения
PRIORITY_LABELS = {
    'critical': 'Критически важно',
    'high': 'Высокая важность', 
    'medium': 'Средняя важность',
    'low': 'Низкая важность'
}

URGENCY_LABELS = {
    'critical': 'Критично',
    'high': 'Срочно',
    'medium': 'Средняя срочность', 
    'low': 'Не срочно'
}

STATUS_LABELS = {
    'новая': 'Новая',
    'в_работе': 'В работе',
    'тестирование': 'Тестирование',
    'завершена': 'Завершена',
    'отменена': 'Отменена'
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'username' not in session:
                return redirect(url_for('login'))
            if session['role'] != role and session['role'] != 'admin':
                flash('Недостаточно прав для доступа к этой странице', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('create_task_public'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = user_manager.verify_user(username, password)
        if user:
            session['username'] = username
            session['role'] = user['role']
            session['name'] = user['name']
            session['user_id'] = user['id']
            flash('Успешный вход в систему!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    tasks = task_manager.get_all_tasks()
    workers = user_manager.get_workers()
    return render_template('dashboard.html', 
                         tasks=tasks, 
                         workers=workers,
                         priority_labels=PRIORITY_LABELS,
                         urgency_labels=URGENCY_LABELS,
                         status_labels=STATUS_LABELS)

@app.route('/create-task', methods=['GET', 'POST'])
def create_task_public():
    if request.method == 'POST':
        task_data = {
            'title': request.form['title'],
            'description': request.form['description'],
            'priority': request.form['priority'],
            'urgency': request.form['urgency'],
            'created_by': request.form.get('creator_name', 'Аноним')
        }
        
        task = task_manager.add_task(task_data)
        flash(f'Задача создана успешно! Номер: {task["number"]}', 'success')
        return redirect(url_for('create_task_public'))
    
    return render_template('create_task.html')

@app.route('/task/<int:task_id>')
@login_required
def task_detail(task_id):
    task = task_manager.get_task(task_id)
    if not task:
        flash('Задача не найдена', 'error')
        return redirect(url_for('dashboard'))
    
    workers = user_manager.get_workers()
    return render_template('task_detail.html', 
                         task=task, 
                         workers=workers,
                         priority_labels=PRIORITY_LABELS,
                         urgency_labels=URGENCY_LABELS,
                         status_labels=STATUS_LABELS)

@app.route('/task/<int:task_id>/update', methods=['POST'])
@login_required
def update_task(task_id):
    updates = {}
    
    if 'progress' in request.form:
        updates['progress'] = int(request.form['progress'])
    if 'status' in request.form:
        updates['status'] = request.form['status']
    if 'priority' in request.form:
        updates['priority'] = request.form['priority']
    if 'urgency' in request.form:
        updates['urgency'] = request.form['urgency']
    if 'assigned_to' in request.form:
        updates['assigned_to'] = request.form['assigned_to']
    
    task = task_manager.update_task(task_id, updates, session['name'])
    if task:
        flash('Задача обновлена успешно', 'success')
    else:
        flash('Ошибка при обновлении задачи', 'error')
    
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/task/<int:task_id>/comment', methods=['POST'])
@login_required
def add_comment(task_id):
    comment = request.form['comment']
    task = task_manager.add_comment(task_id, comment, session['name'])
    if task:
        flash('Комментарий добавлен', 'success')
    else:
        flash('Ошибка при добавлении комментария', 'error')
    
    return redirect(url_for('task_detail', task_id=task_id))

@app.route('/admin')
@login_required
@role_required('admin')
def admin():
    users = user_manager.get_all_users()
    return render_template('admin.html', users=users)

@app.route('/admin/add_user', methods=['POST'])
@login_required
@role_required('admin')
def add_user():
    username = request.form['username']
    password = request.form['password']
    role = request.form['role']
    name = request.form['name']
    
    if user_manager.add_user(username, password, role, name):
        flash('Пользователь добавлен успешно', 'success')
    else:
        flash('Пользователь с таким именем уже существует', 'error')
    
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)