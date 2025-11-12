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

STATUS_GROUPS = [
    {
        'key': 'active',
        'label': 'Активные задачи',
        'statuses': ['в_работе', 'тестирование']
    },
    {
        'key': 'backlog',
        'label': 'Не взятые в работу',
        'statuses': ['новая']
    },
    {
        'key': 'closed',
        'label': 'Закрытые задачи',
        'statuses': ['завершена', 'отменена']
    }
]


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    value = str(value).strip().lower()
    return value in {'1', 'true', 'yes', 'on'}

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
    show_all = parse_bool(request.args.get('show_all'), default=False)
    tasks = task_manager.get_tasks_filtered(include_closed=show_all)
    assignable_users = user_manager.get_assignable_users()
    return render_template('dashboard.html', 
                         tasks=tasks, 
                         show_all=show_all,
                         assignable_users=assignable_users,
                         priority_labels=PRIORITY_LABELS,
                         urgency_labels=URGENCY_LABELS,
                         status_labels=STATUS_LABELS,
                         status_groups=STATUS_GROUPS)

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
    
    assignable_users = user_manager.get_assignable_users()
    return render_template('task_detail.html', 
                         task=task, 
                         assignable_users=assignable_users,
                         priority_labels=PRIORITY_LABELS,
                         urgency_labels=URGENCY_LABELS,
                         status_labels=STATUS_LABELS,
                         status_groups=STATUS_GROUPS)

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


@app.route('/api/tasks', methods=['GET'])
@login_required
def api_get_tasks():
    show_all = parse_bool(request.args.get('show_all'), default=False)
    tasks = task_manager.get_tasks_filtered(include_closed=show_all)
    return jsonify({
        'tasks': tasks,
        'meta': {
            'show_all': show_all,
            'priority_labels': PRIORITY_LABELS,
            'urgency_labels': URGENCY_LABELS,
            'status_labels': STATUS_LABELS,
            'status_groups': STATUS_GROUPS,
            'assignable_users': user_manager.get_assignable_users()
        }
    })


@app.route('/api/tasks/<int:task_id>', methods=['PATCH'])
@login_required
def api_update_task(task_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'Некорректный формат данных'}), 400

    allowed_fields = {'progress', 'priority', 'urgency', 'assigned_to', 'status'}
    unknown_fields = set(payload.keys()) - allowed_fields
    if unknown_fields:
        return jsonify({'error': f'Недопустимые поля: {", ".join(sorted(unknown_fields))}'}), 400

    updates = {}
    errors = {}

    if 'progress' in payload:
        try:
            progress_value = int(payload['progress'])
            if progress_value < 0 or progress_value > 100:
                errors['progress'] = 'Значение прогресса должно быть от 0 до 100'
            else:
                updates['progress'] = progress_value
        except (TypeError, ValueError):
            errors['progress'] = 'Прогресс должен быть целым числом'

    if 'priority' in payload:
        priority_value = payload['priority']
        if priority_value not in PRIORITY_LABELS:
            errors['priority'] = 'Недопустимое значение важности'
        else:
            updates['priority'] = priority_value

    if 'urgency' in payload:
        urgency_value = payload['urgency']
        if urgency_value not in URGENCY_LABELS:
            errors['urgency'] = 'Недопустимое значение срочности'
        else:
            updates['urgency'] = urgency_value

    if 'assigned_to' in payload:
        assigned_value = payload['assigned_to']
        if assigned_value in (None, '', 'null'):
            updates['assigned_to'] = None
        elif user_manager.user_name_exists(assigned_value):
            updates['assigned_to'] = assigned_value
        else:
            errors['assigned_to'] = 'Такой пользователь не найден'

    if 'status' in payload:
        status_value = payload['status']
        if status_value not in STATUS_LABELS:
            errors['status'] = 'Недопустимое значение статуса'
        else:
            updates['status'] = status_value

    if errors:
        return jsonify({'errors': errors}), 400

    if not updates:
        return jsonify({'message': 'Нет изменений'}), 400

    updated_task = task_manager.update_task(task_id, updates, session['name'])
    if not updated_task:
        return jsonify({'error': 'Задача не найдена'}), 404

    return jsonify({
        'message': 'Задача обновлена успешно',
        'task': updated_task,
        'meta': {
            'priority_labels': PRIORITY_LABELS,
            'urgency_labels': URGENCY_LABELS,
            'status_labels': STATUS_LABELS
        }
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)